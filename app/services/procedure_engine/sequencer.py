"""낭독 실행기 (계획 3-3·3-4): 엔진 결과의 actions를 순서대로 실행하고 되먹임을 엔진에 넣는다.

say는 끝까지 읽은 뒤 다음으로 넘어가고, device·record는 시작만 하고 넘어간다(결과는 되먹임으로 돌아온다).
소리 내 읽는 say는 읽기 직전에 시작 되먹임(started)을 넣고 엔진이 처리할 때까지 기다린다(소리 기능 멈춤, held_by).
replace 결과가 오면 남은 say는 읽지 않고 끊김(cut)으로 돌려주고, 남은 device·record는 그대로 실행한다.
"""
import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.schemas.session import SessionState
from app.services.devices import DeviceController
from app.services.events import EventSink
from app.services.manual_db import ManualDB
from app.services.procedure_engine.actions import Action, DeviceAction, EngineResult, RecordAction, SayAction
from app.services.procedure_engine.clock import Clock
from app.services.procedure_engine.inputs import DeviceResultInput, EngineInput, PlaybackInput, RecordSavedInput
from app.services.procedure_engine.timers import listen_state
from app.services.records import RecordSink
from app.services.tts import Speaker

log = logging.getLogger(__name__)

_GUIDE_STEP_FIELDS = ("manual_id", "step_id", "kind", "screen", "image")


class Batch:
    """엔진 결과 하나의 actions. 모두 실행하면(say는 낭독이 끝나면) done이 풀린다."""

    def __init__(self, turn_id: str | None, size: int):
        loop = asyncio.get_running_loop()
        self.turn_id = turn_id
        self.remaining = size
        self.done: asyncio.Future[None] = loop.create_future()
        self.created_at = time.perf_counter()  # 엔진 결과가 나온 시각(리듀스 직후에 만든다)
        self.first_sound: asyncio.Future[float] = loop.create_future()  # 첫 소리가 실제로 나간 시각(perf_counter)
        if size == 0:
            self.done.set_result(None)

    def finish_one(self) -> None:
        self.remaining -= 1
        if self.remaining == 0 and not self.done.done():
            self.done.set_result(None)


@dataclass
class _Item:
    action: Action
    batch: Batch
    silent: bool  # speak: false. 낭독 대신 바로 끝난 것으로
    cut: bool = False  # replace로 버려진 say


class Sequencer:
    def __init__(self, db: ManualDB, events: EventSink, speaker: Speaker, devices: DeviceController,
                 records: RecordSink, feed: Callable[[EngineInput], Awaitable[object]],
                 state: Callable[[], SessionState], clock: Clock, device_timeout_s: float):
        self._db = db
        self._clock = clock
        self._events = events
        self._speaker = speaker
        self._devices = devices
        self._records = records
        self._feed = feed
        self._state = state
        self._device_timeout_s = device_timeout_s
        self._queue: deque[_Item] = deque()
        self._current: _Item | None = None
        self._playing: asyncio.Task | None = None
        self._side: set[asyncio.Future] = set()  # 응답을 기다리는 장치 명령·기록 저장
        self._wake = asyncio.Event()
        self._quiet = asyncio.Event()  # 쉬게 되면(idle) 켠다. drain()이 기다린다
        self._shown_step: str | None = None
        self._speech: dict | None = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        tasks = [t for t in (self._task, *self._side) if t is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    @property
    def idle(self) -> bool:
        return not self._queue and self._current is None and not self._side

    async def drain(self) -> None:
        """남은 액션과 기다리는 장치 응답·기록 저장이 모두 끝날 때까지 기다린다. 되먹임으로 이어진 액션(끊긴 문장 기록, 장치
        결과 기록)까지 포함한다(키트 끄기 전)."""
        while not self.idle:
            self._quiet.clear()
            await self._quiet.wait()

    @property
    def reading(self) -> bool:
        """읽고 있거나 읽을 문장이 남았는지(버려진 문장 제외)."""
        items = ([self._current] if self._current is not None else []) + list(self._queue)
        return any(isinstance(i.action, SayAction) and not i.cut for i in items)

    @property
    def speech_state(self) -> str | None:
        """마지막으로 낸 speech.state의 state. 아직 내지 않았으면 None."""
        return self._speech["state"] if self._speech is not None else None

    def submit(self, result: EngineResult, speak: bool = True) -> Batch:
        if result.mode == "replace":
            self._cut()
        batch = Batch(result.turn_id, len(result.actions))
        self._queue.extend(_Item(action, batch, silent=not speak) for action in result.actions)
        self._wake.set()
        return batch

    def set_speech_state(self, state: str, **extra: str) -> None:
        """듣기 상태가 바뀔 때만 speech.state를 낸다."""
        data = {"state": state, **extra}
        if data != self._speech:
            self._speech = data
            self._events.publish("speech.state", None, data)

    def _cut(self) -> None:
        for item in self._queue:
            item.cut = True
        if self._playing is not None:
            self._playing.cancel()
        elif self._current is not None and isinstance(self._current.action, SayAction):
            self._current.cut = True  # 시작 되먹임을 기다리는 중이면 읽지 않는다

    async def _run(self) -> None:
        while True:
            if not self._queue:
                if self._speech == {"state": "speaking"}:
                    self.set_speech_state(**listen_state(self._state()))
                if self.idle:
                    self._quiet.set()
                self._wake.clear()
                await self._wake.wait()
                continue
            item = self._current = self._queue.popleft()
            try:
                await self._execute(item)
            except Exception:
                log.exception("액션 실행 실패: %s", item.action)
            finally:
                self._current = None
                item.batch.finish_one()

    async def _execute(self, item: _Item) -> None:
        action = item.action
        if isinstance(action, SayAction):
            result = "cut"
            if not item.cut:
                try:
                    result = await self._say(item)
                except Exception:
                    # 낭독 끝 되먹임이 빠지면 엔진이 무응답 타이머를 다시 걸지 못하므로 어떤 경우에도 돌려준다
                    log.exception("낭독 실패, 글로만 낸 것으로 처리: %s", action.utterance.text)
                    result = "text_only"
            await self._feed(PlaybackInput(action_id=action.action_id, event="finished", result=result))
        elif isinstance(action, DeviceAction):
            if action.command.object in (2, 4) and action.action_id not in self._state().pending_devices:
                return  # 대기 중에 끄기로 취소된 켜기(6-10). 엔진이 이미 요청 전 값으로 되돌렸다
            # 바로 앞 문장이 끝난 지금 명령을 내고(다음 문장보다 먼저), 응답은 기다리지 않는다
            call = asyncio.ensure_future(self._devices.execute(action.command))
            self._track(call)
            self._spawn(self._device(action, call))
        elif isinstance(action, RecordAction):
            self._spawn(self._record(action))

    async def _say(self, item: _Item) -> str:
        action, u = item.action, item.action.utterance
        turn_id = item.batch.turn_id
        if not item.silent:
            try:
                await self._feed(PlaybackInput(action_id=action.action_id, event="started"))
            except Exception:
                log.exception("낭독 시작 되먹임 실패, 그대로 읽음: %s", u.text)
            if item.cut:
                return "cut"  # 기다리는 사이 replace로 버려짐. 화면에도 내지 않는다
        if u.source == "step" and u.step_id != self._shown_step:
            step = self._db.steps_by_id[u.step_id]
            self._events.publish("guide.step", turn_id, {k: step[k] for k in _GUIDE_STEP_FIELDS})
            self._shown_step = u.step_id
        self._events.publish("guide.say", turn_id, {**u.model_dump(), "record_id": action.record_id})
        self.set_speech_state("speaking")
        if item.silent:
            return "done"
        batch = item.batch

        def on_sound() -> None:
            if not batch.first_sound.done():
                batch.first_sound.set_result(time.perf_counter())  # turn.latency 기준

        task = self._playing = asyncio.create_task(self._speaker.speak(u, self._state().volume, on_sound))
        try:
            await asyncio.wait({task})
        except asyncio.CancelledError:
            task.cancel()
            raise
        finally:
            self._playing = None
        if task.cancelled():
            return "cut"
        if task.exception() is not None:
            log.error("TTS 실패, 글로만 냄: %s", u.text, exc_info=task.exception())
            return "text_only"
        return task.result()

    async def _device(self, action: DeviceAction, call: asyncio.Future) -> None:
        command = action.command
        status, detail = await self._wait_device(command, call)
        await self._feed(DeviceResultInput(action_id=action.action_id, object=command.object, status=status,
                                           detail=detail))

    async def _wait_device(self, command, task: asyncio.Future) -> tuple[str, dict | None]:
        """장치 응답을 device_timeout_s까지 기다린다. 시간은 엔진 시계로 잰다(시험에서는 FakeClock으로 넘긴다)."""
        timer = asyncio.ensure_future(self._clock.sleep_until(self._clock.now() + self._device_timeout_s))
        try:
            await asyncio.wait({task, timer}, return_when=asyncio.FIRST_COMPLETED)
            answered = task.done()
        finally:
            for t in (task, timer):
                if not t.done():
                    t.cancel()
        if not answered:
            return "timeout", None
        if task.exception() is not None:
            log.error("장치 명령 실패: %s", command, exc_info=task.exception())
            return "failed", None
        outcome = task.result()
        return outcome.status, outcome.detail

    async def _record(self, action: RecordAction) -> None:
        try:
            ok = await self._records.save(action.record)
        except Exception:
            log.exception("기록 저장 실패: %s", action.record.record_id)
            ok = False
        if action.wants_result:
            await self._feed(RecordSavedInput(action_id=action.action_id, ok=ok))

    def _spawn(self, coro) -> None:
        self._track(asyncio.create_task(coro))

    def _track(self, task: asyncio.Future) -> None:
        self._side.add(task)  # 끝낼 때 함께 취소하고, 남아 있는 동안은 쉬는 것으로 보지 않는다
        task.add_done_callback(self._side_done)

    def _side_done(self, task: asyncio.Task) -> None:
        self._side.discard(task)
        if self.idle:
            self._quiet.set()
        if not task.cancelled() and task.exception() is not None:
            log.error("되먹임 처리 실패", exc_info=task.exception())
