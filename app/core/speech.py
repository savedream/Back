"""말하기 버튼 녹음 한 턴 (명세서 POST /api/speech/start·stop·restart, 계획 결정 D1).

시작(start)에서 턴 잠금을 잡고 녹음하다가, 말하기 완료(stop)나 최대 녹음 시간 만료에서 녹음을 끝내고 처리를 백그라운드로
넘긴다. 잠금은 처리가 끝나야 놓는다. 다시 말하기(restart)는 같은 턴으로 처음부터 다시 녹음한다. stop·만료·restart는
generation으로 맞춰 녹음 → 처리 전환을 한 번만 한다.
"""
import asyncio
import logging
import time

from app.common.error_code import ErrorCode
from app.common.exceptions import AppError
from app.core.parts import Parts
from app.core.turn import TurnLock, abandon_turn, run_voice_turn
from app.services.audio_input import AudioSegment
from app.services.procedure_engine.inputs import BargeInInput, CaptureInput, SpeechActivityInput

log = logging.getLogger(__name__)


class SpeechCapture:
    def __init__(self, parts: Parts, lock: TurnLock, max_s: float, model_timeout_s: float):
        self._parts = parts
        self._lock = lock
        self._max_s = max_s  # 최대 녹음 시간(max_utterance_s, 엔진 시계로 잰다)
        self._model_timeout_s = model_timeout_s
        self.turn_id: str | None = None  # 녹음 중인 턴. 녹음 중이 아니면(처리 중 포함) None
        self.generation = 0  # 녹음을 (다시) 시작할 때마다 오른다
        self._deadline = 0.0  # 최대 녹음 시간 마감
        self._expiry: asyncio.Task | None = None
        self._processing: asyncio.Task | None = None

    @property
    def idle(self) -> bool:
        """처리 중인 턴이 없고 지난 최대 녹음 시간도 없음(시험이 기다리는 기준). 남은 마감은 기다리지 않는다."""
        due = self.turn_id is not None and self._deadline <= self._parts.clock.now()
        return not due and (self._processing is None or self._processing.done())

    async def start(self) -> str:
        """녹음을 시작하고 턴 번호를 돌려준다. 마이크 일시정지·구조신호 중에도 된다."""
        parts = self._parts
        engine = parts.engine
        if not parts.recorder.connected or not engine.state.mic.connected:
            raise AppError(ErrorCode.MIC_UNAVAILABLE)
        turn_id = self._lock.acquire("이미 듣거나 처리 중입니다")
        try:
            if engine.sequencer.reading:
                # 읽던 안내를 끊고 아직 다 읽지 않은 첫 단계로 되돌린다(6-2). 끊긴 뒤에 녹음 상태를 내야 speaking에 덮이지 않는다
                await engine.submit(BargeInInput(turn_id=turn_id))
            parts.recorder.start(turn_id)
        except BaseException:
            self._lock.release(turn_id)
            raise
        # 녹음 동안 무응답·대기 타이머를 멈추고(턴 처리와 같음) 켜 둔 구조신호를 멈춘다(held_by recording)
        engine.post(SpeechActivityInput(state="started"))
        engine.post(CaptureInput(state="started"))
        engine.sequencer.set_speech_state("capturing", trigger="button")
        self.turn_id = turn_id
        self._arm()
        return turn_id

    def stop(self) -> str:
        """녹음을 끝내고 처리를 백그라운드로 넘긴다. 지연 t0는 이 호출 시각."""
        turn_id = self.turn_id
        if turn_id is None:
            raise AppError(ErrorCode.NOT_LISTENING)
        self._finish(self.generation, time.perf_counter())
        return turn_id

    def restart(self) -> str:
        """지금까지 녹음을 인식 없이 버리고 같은 턴으로 처음부터 다시 녹음한다. 버린 녹음은 기록하지 않는다."""
        turn_id = self.turn_id
        if turn_id is None:
            raise AppError(ErrorCode.NOT_LISTENING)
        recorder = self._parts.recorder
        recorder.discard()
        recorder.start(turn_id)
        self._arm()
        return turn_id

    async def close(self) -> None:
        """앱을 끌 때: 녹음 중이면 버리고 최대 녹음 시간·처리 태스크를 취소한다."""
        if self.turn_id is not None:
            self._parts.recorder.discard()
            self._lock.release(self.turn_id)
            self.turn_id = None
        tasks = [t for t in (self._expiry, self._processing) if t is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _arm(self) -> None:
        """녹음을 (다시) 시작함: generation을 올리고 최대 녹음 시간을 처음부터 잰다."""
        self.generation += 1
        self._cancel_expiry()
        self._deadline = self._parts.clock.now() + self._max_s
        self._expiry = asyncio.create_task(self._expire(self.generation, self._deadline))

    async def _expire(self, generation: int, deadline: float) -> None:
        """버튼 녹음도 최대 녹음 시간에 닿으면 끝내고 말하기 완료와 같이 처리한다(① audio_input)."""
        await self._parts.clock.sleep_until(deadline)
        self._finish(generation, time.perf_counter())

    def _cancel_expiry(self) -> None:
        if self._expiry is not None and self._expiry is not asyncio.current_task():
            self._expiry.cancel()
        self._expiry = None

    def _finish(self, generation: int, started: float) -> None:
        """녹음 → 처리. 이미 끝냈거나 그사이 다시 녹음했으면(generation이 다름) 아무것도 하지 않는다."""
        if self.turn_id is None or generation != self.generation:
            return
        turn_id, self.turn_id = self.turn_id, None
        self._cancel_expiry()
        parts = self._parts
        parts.engine.post(CaptureInput(state="ended"))
        try:
            segment = parts.recorder.stop()
        except Exception:
            log.exception("녹음을 끝내지 못함: %s", turn_id)
            abandon_turn(parts)
            self._lock.release(turn_id)
            return
        parts.engine.sequencer.set_speech_state("processing")
        self._processing = asyncio.create_task(self._process(segment, started))

    async def _process(self, segment: AudioSegment, started: float) -> None:
        """STT → 문장 턴과 같은 경로. 실패해도 턴 경로가 듣기 상태를 되돌리고, 잠금은 여기서 놓는다."""
        try:
            await run_voice_turn(self._parts, self._model_timeout_s, segment, started)
        except Exception:
            log.exception("말하기 버튼 턴 처리 실패: %s", segment.turn_id)
        finally:
            self._lock.release(segment.turn_id)
