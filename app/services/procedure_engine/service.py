"""절차 엔진 서비스 (계획 3-2·3-4): 엔진 태스크 하나가 입력을 하나씩 리듀스하고, 가장 이른 마감 시각에 깨어나 TickInput을 넣는다.

리듀스 직후 한 곳에서 이벤트를 내고(turn.result → session.updated → 나머지) actions를 낭독 실행기로 넘긴다.
"""
import asyncio
import logging
from collections.abc import Callable

from app.schemas.device import Location
from app.schemas.session import SessionState
from app.services.devices import DeviceController
from app.services.events import EventSink
from app.services.manual_db import ManualDB
from app.services.procedure_engine.actions import EngineResult
from app.services.procedure_engine.clock import Clock
from app.services.procedure_engine.context import Ctx
from app.services.procedure_engine.core import new_session, recover, reduce
from app.services.procedure_engine.inputs import EngineInput, TickInput
from app.services.procedure_engine.sequencer import Batch, Sequencer
from app.services.procedure_engine.timers import deadlines
from app.services.procedure_engine.view import session_changes
from app.services.records import RecordSink
from app.services.tts import Speaker

log = logging.getLogger(__name__)


class EngineService:
    def __init__(self, db: ManualDB, clock: Clock, events: EventSink, speaker: Speaker, devices: DeviceController,
                 records: RecordSink, *, location: Callable[[], Location | None] = lambda: None,
                 device_timeout_s: float = 2.0):
        self.db = db
        self.clock = clock
        self.events = events
        self._location = location  # GPS 스냅샷. GPS 모듈이 없으면 None
        self.state: SessionState | None = None
        self.sequencer = Sequencer(db, events, speaker, devices, records, self.submit, lambda: self.state,
                                   device_timeout_s)
        self._queue: asyncio.Queue[tuple[EngineInput, bool, asyncio.Future]] = asyncio.Queue()
        self._pending = 0  # 넣었지만 아직 리듀스하지 않은 입력
        self._skipped: set[float] = set()  # 처리하지 못한 마감. 같은 마감으로 계속 깨어나지 않게 건너뛴다
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """새 세션을 열고(T_WELCOME) 엔진 태스크를 띄운다. 준비가 끝난 뒤에 부른다."""
        self.sequencer.start()
        ctx = self._ctx()
        self._apply(None, new_session(ctx), ctx, speak=True)
        self._task = asyncio.create_task(self._run())
        self._task.add_done_callback(_log_crash)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self.sequencer.stop()

    @property
    def idle(self) -> bool:
        """넣은 입력을 모두 처리했고 지난 마감 시각도 없음. 남은 마감 시각은 기다리지 않는다."""
        deadline = self._deadline()
        return self._pending == 0 and not (deadline is not None and deadline <= self.clock.now())

    def put(self, inp: EngineInput, speak: bool = True) -> "asyncio.Future[tuple[EngineResult, Batch]]":
        future = asyncio.get_running_loop().create_future()
        self._pending += 1
        self._queue.put_nowait((inp, speak, future))
        return future

    async def submit(self, inp: EngineInput, *, speak: bool = True) -> EngineResult:
        """입력을 넣고 리듀스 결과를 받는다. speak=False면 그 결과의 낭독을 바로 끝난 것으로 처리한 뒤 돌려준다."""
        result, batch = await self.put(inp, speak)
        if not speak:
            await batch.done
        return result

    def _ctx(self) -> Ctx:
        return Ctx(db=self.db, now=self.clock.now(), wall=self.clock.wall(), location=self._location())

    def _deadline(self) -> float | None:
        if self.state is None:
            return None
        live = deadlines(self.state)
        self._skipped &= set(live)
        rest = [d for d in live if d not in self._skipped]
        return min(rest) if rest else None

    async def _run(self) -> None:
        while True:
            deadline = self._deadline()
            if deadline is not None and deadline <= self.clock.now():
                self._step(TickInput(), True, None)
                await asyncio.sleep(0)  # 마감이 몰려도 다른 입력·낭독에 차례를 준다
                continue
            getter = asyncio.ensure_future(self._queue.get())
            waiters = {getter}
            if deadline is not None:
                waiters.add(asyncio.ensure_future(self.clock.sleep_until(deadline)))
            try:
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for waiter in waiters:
                    if not waiter.done():
                        waiter.cancel()
            if getter.done() and not getter.cancelled():
                inp, speak, future = getter.result()
                try:
                    self._step(inp, speak, future)
                finally:
                    self._pending -= 1

    def _step(self, inp: EngineInput, speak: bool, future: asyncio.Future | None) -> None:
        before, ctx = self.state, self._ctx()
        due = {d for d in deadlines(before) if d <= ctx.now} if inp.kind == "tick" else set()
        try:
            result = reduce(before, inp, ctx)
            batch = self._apply(before, result, ctx, speak)
        except Exception as e:
            log.exception("엔진 입력 처리 실패: %s", inp.kind)
            self._skipped |= due
            if inp.kind != "tick":
                self._recover(inp)
            if future is not None and not future.done():
                future.set_exception(e)
            return
        if due and result.state == before:
            # 지난 마감을 처리했는데 아무것도 바뀌지 않았으면 다음에도 같다. 엔진 버그로 보고 그 마감만 건너뛴다
            log.error("지난 마감을 처리하지 못함: %s", sorted(due))
            self._skipped |= due
        if future is not None and not future.done():
            future.set_result((result, batch))

    def _recover(self, inp: EngineInput) -> None:
        try:
            self.state = recover(self.state, inp, self._ctx()).state
        except Exception:
            log.exception("실패한 입력 뒤 정리도 실패: %s", inp.kind)

    def _apply(self, before: SessionState | None, result: EngineResult, ctx: Ctx, speak: bool) -> Batch:
        self.state = result.state
        # 시퀀서에 먼저 넘긴다. 시퀀서는 이 함수가 끝나 차례가 와야 돌므로 이벤트 순서는 그대로이고,
        # 이벤트 발행이 실패해도 낭독이 빠지지 않는다(빠지면 남은 문장 때문에 마감이 걸리지 않는다)
        batch = self.sequencer.submit(result, speak)
        events = result.events
        for e in (e for e in events if e.type == "turn.result"):
            self._publish(e.type, result.turn_id, e.data)
        changes = session_changes(before, result.state, ctx)
        if changes:
            self._publish("session.updated", result.turn_id, changes)
        for e in (e for e in events if e.type != "turn.result"):
            self._publish(e.type, result.turn_id, e.data)
        return batch

    def _publish(self, type: str, turn_id: str | None, data: dict) -> None:
        try:
            self.events.publish(type, turn_id, data)
        except Exception:
            log.exception("이벤트 발행 실패: %s", type)


def _log_crash(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.critical("엔진 태스크가 멈춤", exc_info=task.exception())
