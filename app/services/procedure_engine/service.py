"""절차 엔진 서비스 (계획 3-2·3-4): 엔진 태스크 하나가 입력을 하나씩 리듀스하고, 가장 이른 마감 시각에 깨어나 TickInput을 넣는다.

리듀스 직후 한 곳에서 이벤트를 내고(speech.final → turn.result → session.updated → 나머지) actions를 낭독 실행기로 넘긴다.
화면용 timer.tick은 엔진 입력이 아니라 이 서비스의 주기 작업이 낸다(매초 상태가 바뀌지 않게, 계획 결정 C2).
키트를 끄는 동안(동결)에는 마감 처리·위치 갱신·timer.tick을 미룬다(POST /api/system/shutdown).
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
from app.services.procedure_engine.core import new_session, recover, reduce, resume_session
from app.services.procedure_engine.inputs import EngineInput, TickInput
from app.services.procedure_engine.sequencer import Batch, Sequencer
from app.services.procedure_engine.timers import deadlines
from app.services.procedure_engine.view import session_changes, timer_view
from app.services.records import RecordSink, Snapshot
from app.services.tts import Speaker

log = logging.getLogger(__name__)

# 턴 결과 이벤트는 session.updated보다 먼저, 음성 턴의 알아들은 문장은 turn.result보다 먼저(이벤트 스트림 명세 음성 턴 순서)
_TURN_EVENTS = ("speech.final", "turn.result")


class EngineService:
    def __init__(self, db: ManualDB, clock: Clock, events: EventSink, speaker: Speaker, devices: DeviceController,
                 records: RecordSink, *, location: Callable[[], Location | None] = lambda: None,
                 device_timeout_s: float = 2.0, on_new_session: Callable[[SessionState], object] | None = None,
                 session_ids: Callable[[], frozenset[str]] = frozenset,
                 on_state: Callable[[SessionState], object] | None = None):
        self.db = db
        self.clock = clock
        self.events = events
        self._location = location  # GPS 스냅샷. GPS 모듈이 없으면 None
        self._on_new_session = on_new_session  # 새 세션을 열면(start, session_reset) 부른다. 세션 표에 남기는 데 쓴다
        self._session_ids = session_ids  # 이미 쓴 세션 ID(세션 표)
        self._on_state = on_state  # 상태가 바뀌면 부른다. 상태 스냅샷(6-14)을 저장하는 데 쓴다
        self.state: SessionState | None = None
        self._devices = devices
        self._held: set[int] = set()  # 음소거 관문을 닫아 둔 소리 기능(object)
        self.sequencer = Sequencer(db, events, speaker, devices, records, self.submit, lambda: self.state, clock,
                                   device_timeout_s)
        self._queue: asyncio.Queue[tuple[EngineInput, bool, asyncio.Future]] = asyncio.Queue()
        self._pending = 0  # 넣었지만 아직 리듀스하지 않은 입력
        self._skipped: set[float] = set()  # 처리하지 못한 마감. 같은 마감으로 계속 깨어나지 않게 건너뛴다
        self._task: asyncio.Task | None = None
        self._ticker: asyncio.Task | None = None  # timer.tick 주기 작업
        self._timer_changed = asyncio.Event()  # 타이머 상태가 바뀌면 주기 작업을 깨운다
        self._thawed = asyncio.Event()  # 동결(키트를 끄는 중)이 아님. freeze()가 끄고 unfreeze()가 켠다
        self._thawed.set()
        self._deferred: list[tuple[EngineInput, bool, asyncio.Future]] = []  # 동결 중 미룬 위치 갱신

    async def start(self, previous_session_id: str | None = None, snapshot: Snapshot | None = None) -> None:
        """엔진 태스크를 띄운다. 준비가 끝난 뒤에 부른다. snapshot이 있으면 그 세션을 이어 가고(T_RESUME, 6-14),
        없으면 새 세션을 연다(T_WELCOME, previous_session_id: 지난 세션)."""
        self.sequencer.start()
        ctx = self._ctx()
        result = None
        if snapshot is not None:
            # 끊긴 시간은 단조 시계 차이로 잰다. 같은 부팅이면 단조 시계가 이어지고, 벽시계는 GPS 동기화로 바뀔 수 있다
            downtime = max(0.0, ctx.now - snapshot.saved_now)
            try:
                result = resume_session(snapshot.state, ctx, snapshot.saved_now, downtime)
            except Exception:
                log.exception("스냅샷에서 이어 가지 못해 새 세션을 연다: %s", snapshot.state.session_id)
        if result is None:
            self._apply(None, new_session(ctx, previous_session_id=previous_session_id), ctx, speak=True)
        else:
            self._apply(None, result, ctx, speak=True, resumed=True)
        self._task = asyncio.create_task(self._run())
        self._task.add_done_callback(_log_crash)
        self._ticker = asyncio.create_task(self._tick_timer())
        self._ticker.add_done_callback(_log_crash)

    async def stop(self) -> None:
        tasks = [t for t in (self._task, self._ticker) if t is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.sequencer.stop()

    @property
    def idle(self) -> bool:
        """넣은 입력을 모두 처리했고 지난 마감 시각도 없음. 남은 마감 시각은 기다리지 않는다."""
        deadline = self._deadline()
        return self._pending == 0 and not (deadline is not None and deadline <= self.clock.now())

    @property
    def frozen(self) -> bool:
        """키트를 끄는 중(동결)."""
        return not self._thawed.is_set()

    def freeze(self) -> None:
        """키트를 끄는 동안 새 일을 멈춘다: 마감 처리(절차 알림·무응답·대기·확인 질문·GPS 재시도 끝)와 위치 갱신(늦게 잡힌
        좌표 낭독)을 미루고 timer.tick을 내지 않는다. 되먹임·종료 입력처럼 넣은 다른 입력은 그대로 처리한다."""
        self._thawed.clear()
        self._timer_changed.set()

    def unfreeze(self) -> None:
        """동결을 풀고 미룬 위치 갱신과 그사이 지난 마감을 처리한다(저장 실패, 가짜 종료 훅)."""
        self._thawed.set()
        self._timer_changed.set()
        for item in self._deferred:
            self._pending += 1
            self._queue.put_nowait(item)
        self._deferred.clear()

    def put(self, inp: EngineInput, speak: bool = True) -> "asyncio.Future[tuple[EngineResult, Batch]]":
        future = asyncio.get_running_loop().create_future()
        self._pending += 1
        self._queue.put_nowait((inp, speak, future))
        return future

    def post(self, inp: EngineInput) -> None:
        """결과를 기다리지 않고 넣는다. 처리 실패는 엔진이 로그로 남긴다."""
        self.put(inp).add_done_callback(_drop_result)

    async def submit(self, inp: EngineInput, *, speak: bool = True) -> EngineResult:
        """입력을 넣고 리듀스 결과를 받는다. speak=False면 그 결과의 낭독을 바로 끝난 것으로 처리한 뒤 돌려준다."""
        result, batch = await self.put(inp, speak)
        if not speak:
            await batch.done
        return result

    def _ctx(self) -> Ctx:
        return Ctx(db=self.db, now=self.clock.now(), wall=self.clock.wall(), location=self._location(),
                   session_ids=self._session_ids())

    def _deadline(self) -> float | None:
        if self.state is None or self.frozen:
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
            elif self.frozen:
                waiters.add(asyncio.ensure_future(self._thawed.wait()))  # 풀리면 그사이 지난 마감을 본다
            try:
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for waiter in waiters:
                    if not waiter.done():
                        waiter.cancel()
            if getter.done() and not getter.cancelled():
                inp, speak, future = getter.result()
                try:
                    if self.frozen and inp.kind == "location_update":
                        self._deferred.append((inp, speak, future))  # 풀리면 다시 넣는다(그때 위치를 읽는다)
                    else:
                        self._step(inp, speak, future)
                finally:
                    self._pending -= 1

    async def _tick_timer(self) -> None:
        """타이머가 도는 동안 경과 시간이 정수 초를 넘을 때마다 timer.tick(AC-E6). 늦게 깨어나도 밀린 tick을 몰아 내지 않고
        한 번만 낸 뒤 다음 정수 초에 맞춘다. 돌지 않거나 동결 중이면 타이머가 바뀌거나 동결이 풀릴 때까지 잔다."""
        while True:
            self._timer_changed.clear()
            timer = self.state.timer if self.state is not None else None
            if timer is None or timer.status != "running" or self.frozen:
                await self._timer_changed.wait()
                continue
            target = timer.elapsed_s(self.clock.now()) + 1  # 다음 정수 초(이번 측정 구간 시작 시각 기준)
            sleeper = asyncio.ensure_future(self.clock.sleep_until(timer.running_since + target - timer.accumulated_s))
            changed = asyncio.ensure_future(self._timer_changed.wait())
            try:
                await asyncio.wait({sleeper, changed}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for waiter in (sleeper, changed):
                    if not waiter.done():
                        waiter.cancel()
            now = self.clock.now()
            if not changed.done() and self.state.timer.elapsed_s(now) >= target:
                self._publish("timer.tick", None, timer_view(self.state.timer, now))

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
            before = self.state
            self.state = recover(before, inp, self._ctx()).state
            self._state_changed(before)
            self._sync_holds()
        except Exception:
            log.exception("실패한 입력 뒤 정리도 실패: %s", inp.kind)

    def _apply(self, before: SessionState | None, result: EngineResult, ctx: Ctx, speak: bool,
               resumed: bool = False) -> Batch:
        self.state = result.state
        # 시퀀서에 먼저 넘긴다. 시퀀서는 이 함수가 끝나 차례가 와야 돌므로 이벤트 순서는 그대로이고,
        # 이벤트 발행이 실패해도 낭독이 빠지지 않는다(빠지면 남은 문장 때문에 마감이 걸리지 않는다)
        batch = self.sequencer.submit(result, speak)
        opened = not resumed and result.state.session_id != (before.session_id if before else None)
        if self._on_new_session is not None and opened:  # 이어 가는 세션은 이미 세션 표에 있다
            try:
                self._on_new_session(result.state)
            except Exception:
                log.exception("새 세션 알림 실패: %s", result.state.session_id)
        self._state_changed(before)
        events = result.events
        for type in _TURN_EVENTS:
            for e in (e for e in events if e.type == type):
                self._publish(e.type, result.turn_id, e.data)
        changes = session_changes(before, result.state, ctx)
        if changes:
            self._publish("session.updated", result.turn_id, changes)
        for e in (e for e in events if e.type not in _TURN_EVENTS):
            self._publish(e.type, result.turn_id, e.data)
        self._sync_holds()
        return batch

    def _sync_holds(self) -> None:
        """held_by에 맞춰 소리 기능의 음소거 관문을 여닫는다(⑦ 소리 충돌). 멈춤이 풀려도 연결돼 있고 status가 확정값(on·off)일
        때만 연다. 멈춘 동안 끄기 요청이 왔거나 장치 오류면 다음 성공 결과로 status가 정해질 때까지 닫아 둔다."""
        for device in self.state.devices.values():
            obj = device.object
            if device.held_by is not None and obj not in self._held:
                held = True
            elif (device.held_by is None and obj in self._held and device.connected
                  and device.status in ("on", "off")):
                held = False
            else:
                continue
            try:
                self._devices.set_hold(obj, held)
            except Exception:
                log.exception("소리 기능 멈춤 처리 실패: %s", device.name)
                continue
            if held:
                self._held.add(obj)
            else:
                self._held.discard(obj)

    def _state_changed(self, before: SessionState | None) -> None:
        if before is None or self.state.timer != before.timer:
            self._timer_changed.set()
        if self._on_state is not None and self.state != before:
            try:
                self._on_state(self.state)
            except Exception:
                log.exception("상태 변화 알림 실패: %s", self.state.session_id)

    def _publish(self, type: str, turn_id: str | None, data: dict) -> None:
        try:
            self.events.publish(type, turn_id, data)
        except Exception:
            log.exception("이벤트 발행 실패: %s", type)


def _drop_result(future: asyncio.Future) -> None:
    if not future.cancelled():
        future.exception()  # 꺼내 두어야 "exception was never retrieved" 경고가 겹쳐 찍히지 않는다


def _log_crash(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.critical("엔진 태스크가 멈춤", exc_info=task.exception())
