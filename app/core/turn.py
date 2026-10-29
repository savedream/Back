"""한 턴: ③ 정규화 → ④ 모델 → ⑤ 파서 → ⑥ 엔진.

명세서: /api/utterances는 ③부터 음성 턴과 같은 코드 경로를 탄다. 음성 턴도 STT 뒤에 이 함수들을 쓴다.
"""
import asyncio
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import BaseModel

from app.common.error_code import ErrorCode
from app.common.exceptions import AppError
from app.core.parts import Parts
from app.schemas.function_call import FunctionCall
from app.schemas.turn import NormalizedText
from app.schemas.utterance import Utterance
from app.services.events import EventSink
from app.services.model_runner import ModelRunner
from app.services.normalizer import normalize
from app.services.output_parser import ModelOutputError, parse
from app.services.procedure_engine.actions import SayAction
from app.services.procedure_engine.functions import is_progress_phrase
from app.services.procedure_engine.inputs import (
    FunctionCallInput, ModelErrorInput, ProgressPhraseInput, SpeechActivityInput,
)
from app.services.procedure_engine.timers import listen_state

log = logging.getLogger(__name__)


class TurnOutcome(BaseModel):
    turn_id: str
    normalized: NormalizedText
    raw: str | None  # 모델 출력 원문. 시간 초과·모델 오류면 None
    function_call: FunctionCall | None
    error_code: ErrorCode | None  # MODEL_TIMEOUT · MODEL_FAILED · MODEL_PARSE_ERROR · MODEL_OUT_OF_RANGE
    latency_ms: dict[str, int]  # normalize, model, parse


def _elapsed_ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


async def run_text_turn(turn_id: str, text: str, model: ModelRunner, model_timeout_s: float) -> TurnOutcome:
    latency: dict[str, int] = {}
    start = time.perf_counter()
    normalized = normalize(text)
    latency["normalize"] = _elapsed_ms(start)
    return await interpret(turn_id, normalized, model, model_timeout_s, latency)


async def interpret(turn_id: str, normalized: NormalizedText, model: ModelRunner, model_timeout_s: float,
                    latency: dict[str, int]) -> TurnOutcome:
    """④ 모델 → ⑤ 파서. latency에 model·parse 시간을 더한다."""

    def failed(code: ErrorCode, raw: str | None = None) -> TurnOutcome:
        return TurnOutcome(
            turn_id=turn_id, normalized=normalized, raw=raw, function_call=None, error_code=code, latency_ms=latency
        )

    start = time.perf_counter()
    try:
        raw = await asyncio.wait_for(model.run(normalized.text), timeout=model_timeout_s)
    except asyncio.TimeoutError:
        latency["model"] = _elapsed_ms(start)
        log.warning("모델 시간 초과 (%.1f초): %s", model_timeout_s, normalized.text)
        return failed(ErrorCode.MODEL_TIMEOUT)
    except Exception:
        latency["model"] = _elapsed_ms(start)
        log.exception("모델 실행 오류: %s", normalized.text)
        return failed(ErrorCode.MODEL_FAILED)
    latency["model"] = _elapsed_ms(start)

    start = time.perf_counter()
    try:
        function_call = parse(raw)
    except ModelOutputError as e:
        latency["parse"] = _elapsed_ms(start)
        log.warning("모델 출력 해석 실패 %s: %s (%s)", e.code.value, e, raw)
        return failed(e.code, raw)
    latency["parse"] = _elapsed_ms(start)
    return TurnOutcome(
        turn_id=turn_id, normalized=normalized, raw=raw, function_call=function_call, error_code=None, latency_ms=latency
    )


class TurnLock:
    """음성 경로·/api/speech/start·/api/utterances 공용 턴 잠금(계획 3-5). 턴을 시작할 때 잡으며 턴 번호를 매기고,
    엔진이 그 턴 입력을 처리한 뒤 놓는다. 잡혀 있으면 409 AUDIO_BUSY."""

    def __init__(self) -> None:
        self._busy = False
        self._count = 0

    @property
    def busy(self) -> bool:
        return self._busy

    @contextmanager
    def turn(self) -> Iterator[str]:
        if self._busy:
            raise AppError(ErrorCode.AUDIO_BUSY, "다른 턴을 처리 중입니다")
        self._busy = True
        self._count += 1
        try:
            yield f"t_{self._count:04d}"
        finally:
            self._busy = False


class UtteranceReply(BaseModel):
    """POST /api/utterances 응답 data. say는 이 입력 하나의 엔진 결과(다음 ask·wait까지), step_id는 처리 뒤 엔진 위치."""

    turn_id: str
    normalized: str
    raw: str | None  # 모델까지 가지 않았으면 None
    function_call: FunctionCall | None
    error_code: ErrorCode | None
    say: list[Utterance]
    step_id: str | None
    latency_ms: dict[str, int]


_LATENCY_KEYS = ("vad_tail", "stt", "normalize", "model", "parse", "engine")


async def run_utterance(parts: Parts, model_timeout_s: float, turn_id: str, text: str, speak: bool) -> UtteranceReply:
    """문장 하나로 한 턴을 처리한다. speak=False면 낭독 대신 바로 끝난 것으로 처리한 뒤(알림 시작 시각까지 반영) 돌려주고,
    speak=True면 엔진 결과가 나오면 바로 돌려준다(낭독은 기다리지 않음)."""
    engine, sequencer = parts.engine, parts.engine.sequencer
    started = time.perf_counter()
    sequencer.set_speech_state("processing")
    # 턴을 처리하는 동안 무응답·대기 타이머를 멈추고 절차 알림은 턴이 끝난 뒤로 미룬다(6-9, 6-12)
    engine.post(SpeechActivityInput(state="started"))
    try:
        return await _run_turn(parts, model_timeout_s, turn_id, text, speak, started)
    except Exception:
        engine.post(SpeechActivityInput(state="cancelled"))
        _back_to_listening(parts)  # 실패해도 "처리 중"이 남지 않게
        raise


def _back_to_listening(parts: Parts) -> None:
    """읽을 문장이 없는 턴이 끝났을 때의 듣기 상태. 앞 턴의 안내를 아직 읽는 중이면 안내 중으로 둔다."""
    sequencer = parts.engine.sequencer
    if sequencer.reading:
        sequencer.set_speech_state("speaking")
    else:
        sequencer.set_speech_state(**listen_state(parts.engine.state))


async def _run_turn(parts: Parts, model_timeout_s: float, turn_id: str, text: str, speak: bool,
                    started: float) -> UtteranceReply:
    engine = parts.engine
    latency: dict[str, int] = {}
    normalized = normalize(text)
    latency["normalize"] = _elapsed_ms(started)
    state = engine.state  # 말소리가 잡힌 시점의 위치를 들고 간다(6-1)
    base = {"turn_id": turn_id, "epoch": state.epoch, "step_id": state.step_id, "text": text,
            "normalized": normalized.text}
    raw = function_call = error_code = None
    if is_progress_phrase(engine.db, state, normalized.text):
        inp = ProgressPhraseInput(**base)  # 대기 단계의 진행 표현은 모델을 건너뛴다(6-8)
    else:
        outcome = await interpret(turn_id, normalized, parts.model, model_timeout_s, latency)
        raw, function_call, error_code = outcome.raw, outcome.function_call, outcome.error_code
        inp = (FunctionCallInput(**base, function_call=function_call) if function_call is not None
               else ModelErrorInput(**base, error_code=error_code))
    start = time.perf_counter()
    result, batch = await engine.put(inp, speak)
    latency["engine"] = _elapsed_ms(start)
    say = [a.utterance for a in result.actions if isinstance(a, SayAction)]
    if not say:
        _back_to_listening(parts)
    if speak:
        produced = time.perf_counter()
        batch.first_sound.add_done_callback(
            lambda sound: _report_latency(parts.events, turn_id, latency, started, produced, sound))
    else:
        await batch.done
    return UtteranceReply(turn_id=turn_id, normalized=normalized.text, raw=raw, function_call=function_call,
                          error_code=error_code, say=say, step_id=result.state.step_id, latency_ms=latency)


def _report_latency(events: EventSink, turn_id: str, latency: dict[str, int], started: float, produced: float,
                    sound: asyncio.Future) -> None:
    """턴의 첫 소리가 나간 직후 turn.latency. 텍스트 턴은 vad_tail·stt가 없다(None)."""
    if sound.cancelled():
        return
    at = sound.result()
    data = {key: latency.get(key) for key in _LATENCY_KEYS}
    data["tts_first"] = max(0, round((at - produced) * 1000))
    data["e2e"] = round((at - started) * 1000)
    events.publish("turn.latency", turn_id, data)
