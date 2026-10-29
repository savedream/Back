"""처치 기록 저장 (명세서 6-13). 서버는 SQLite 저장소(전용 쓰기 스레드)를, 시험은 메모리 저장소를 같은 Protocol로 끼운다."""
import asyncio
import logging
import queue
import sqlite3
import threading
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import TypeAdapter

from app.schemas.record import Record
from app.services.events import EventSink
from app.services.procedure_engine.clock import Clock

log = logging.getLogger(__name__)

T = TypeVar("T")

_RECORD = TypeAdapter(Record)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    previous_session_id TEXT,
    started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
    record_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    at TEXT NOT NULL,
    saved INTEGER NOT NULL,
    json TEXT NOT NULL,
    UNIQUE (session_id, record_id)
);
"""


class RecordSink(Protocol):
    async def save(self, record: Record) -> bool:
        """저장했으면 True. 실패해도 기능은 멈추지 않고 saved: false로 두고 재시도한다(명세서 6-13)."""
        ...


class MemoryRecordStore:
    """메모리 기록. 시험에서는 fail_next_save()로 저장 실패를 넣는다."""

    def __init__(self) -> None:
        self.records: list[Record] = []
        self._failures: list[str | None] = []

    def fail_next_save(self, kind: str | None = None) -> None:
        """다음 저장 하나를 실패시킨다. kind를 주면 그 종류의 다음 기록만."""
        self._failures.append(kind)

    async def save(self, record: Record) -> bool:
        ok = True
        for i, kind in enumerate(self._failures):
            if kind is None or kind == record.kind:
                del self._failures[i]
                ok = False
                break
        self.records.append(record.model_copy(update={"saved": ok}))
        return ok


class SqliteRecordStore:
    """SQLite 기록 저장소. 기록은 받는 즉시 메모리에 두고 전용 쓰기 스레드가 큐 순서대로 저장한다.

    실패하면 saved: false로 두고, 재시도 태스크가 엔진 시계로 retry_base_s부터 두 배씩(최대 retry_max_s) 기다렸다 다시 저장한다.
    sqlite3 연결은 쓰기 스레드 안에서만 만들고 쓴다. 상태 스냅샷(6-14)도 run()으로 같은 스레드에서 저장한다.
    """

    def __init__(self, path: Path, clock: Clock, events: EventSink, *, retry_base_s: float = 1,
                 retry_max_s: float = 60):
        self._path = path
        self._clock = clock
        self._events = events
        self._retry_base_s = retry_base_s
        self._retry_max_s = retry_max_s
        self._jobs: queue.SimpleQueue = queue.SimpleQueue()  # (작업 함수, Future). None이면 스레드를 끝낸다
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._closed = False
        self._failures: list[str | None] = []
        self._all: list[Record] = []  # 이 프로세스에서 받은 기록(발생 순서)
        self._sessions: dict[str, list[Record]] = {}  # 이 프로세스에서 연 세션별 기록(발생 순서)
        self._session_info: dict[str, dict] = {}
        self._unsaved: dict[tuple[str, str], Record] = {}  # 저장에 실패해 다시 저장할 기록
        self._retry_lock = asyncio.Lock()
        self._retry_task: asyncio.Task | None = None

    def start(self) -> None:
        """쓰기 스레드를 띄운다. 스레드 결과를 이 이벤트 루프로 넘기므로 루프 안에서 부른다."""
        self._loop = asyncio.get_running_loop()
        self._thread = threading.Thread(target=self._write_loop, name="record-writer", daemon=True)
        self._thread.start()

    async def close(self) -> None:
        """재시도를 멈추고 큐에 남은 쓰기를 마친 뒤 쓰기 스레드를 끝낸다."""
        if self._retry_task is not None:
            self._retry_task.cancel()
            await asyncio.gather(self._retry_task, return_exceptions=True)
        if self._thread is None or self._closed:
            return
        self._closed = True
        self._jobs.put(None)
        await asyncio.to_thread(self._thread.join)

    @property
    def records(self) -> list[Record]:
        """이 프로세스에서 받은 모든 기록(발생 순서)."""
        return list(self._all)

    def unsaved(self) -> list[Record]:
        """저장에 실패해 아직 다시 저장하지 못한 기록."""
        return list(self._unsaved.values())

    def fail_next_save(self, kind: str | None = None) -> None:
        """다음 저장 하나를 실패시킨다(재시도 포함). kind를 주면 그 종류의 다음 기록만. 쓰기 스레드가 SQL을 쓰기 전에 실패로 처리한다."""
        self._failures.append(kind)

    async def save(self, record: Record) -> bool:
        """메모리에 바로 두고 쓰기 큐로 넘긴 뒤 첫 시도 결과를 돌려준다. 재시도는 기다리지 않는다."""
        rec = record.model_copy(update={"saved": False})
        self._all.append(rec)
        self._sessions.setdefault(rec.session_id, []).append(rec)
        await asyncio.wait([self._attempt(rec, report=True)])  # 기다리던 쪽이 취소돼도 결과는 _settle이 반영한다
        return rec.saved

    async def retry_now(self) -> tuple[int, int]:
        """미저장 기록을 지금 다시 저장해 본다. (저장한 수, 남은 수)."""
        async with self._retry_lock:
            pending = list(self._unsaved.values())
            if not pending:
                return 0, 0
            before = len(self._unsaved)
            await asyncio.wait([self._attempt(rec, report=False) for rec in pending])
            saved = sum(rec.saved for rec in pending)
            self._report(before, failed=saved < len(pending))
            return saved, len(self._unsaved)

    def save_session(self, session_id: str, previous_session_id: str | None, started_at: str) -> asyncio.Future:
        """세션 표에 남긴다. 바로 쓰기 큐에 넣으므로(그 세션 기록보다 먼저) 기다리지 않아도 된다. 실패는 로그로 남긴다."""
        info = {"session_id": session_id, "previous_session_id": previous_session_id, "started_at": started_at}
        self._session_info[session_id] = info
        self._sessions.setdefault(session_id, [])
        future = self._enqueue(partial(_insert_session, info=info))
        future.add_done_callback(partial(_log_failure, f"세션 저장 실패: {session_id}"))
        return future

    async def session_records(self, session_id: str) -> list[Record] | None:
        """세션의 기록. 이 프로세스의 세션이면 메모리(발생 순서), 아니면 SQLite에서 시각순. 없는 세션이면 None."""
        if session_id in self._sessions:
            return list(self._sessions[session_id])
        return await self.run(partial(_load_records, session_id=session_id))

    async def session_info(self, session_id: str) -> dict | None:
        """세션 표의 한 줄(session_id, previous_session_id, started_at). 없는 세션이면 None."""
        if session_id in self._session_info:
            return dict(self._session_info[session_id])
        return await self.run(partial(_load_session, session_id=session_id))

    async def run(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """쓰기 스레드에서 fn(연결)을 다른 쓰기와 같은 순서로 실행하고 결과를 돌려준다."""
        return await self._enqueue(fn)

    def _enqueue(self, fn: Callable[[sqlite3.Connection], Any]) -> asyncio.Future:
        if self._loop is None or self._closed:
            raise RuntimeError("기록 저장소가 열려 있지 않음")
        future = self._loop.create_future()
        self._jobs.put((fn, future))
        return future

    def _write_loop(self) -> None:
        """쓰기 스레드. 연결을 열지 못하면 그 작업을 실패로 돌려주고 다음 작업에서 다시 연다."""
        conn: sqlite3.Connection | None = None
        while (job := self._jobs.get()) is not None:
            fn, future = job
            try:
                if conn is None:
                    conn = _connect(self._path)
                result, error = fn(conn), None
            except Exception as e:
                result, error = None, e
            self._loop.call_soon_threadsafe(_resolve, future, result, error)
        if conn is not None:
            conn.close()

    def _attempt(self, rec: Record, report: bool) -> asyncio.Future:
        """기록 하나를 한 번 저장해 본다. 실패 주입은 지금 정하고, 결과는 _settle이 메모리에 반영한다."""
        fail = self._take_failure(rec.kind)
        payload = rec.model_copy(update={"saved": True}).model_dump_json()
        row = (rec.record_id, rec.session_id, rec.kind, rec.at, payload)
        future = self._enqueue(partial(_insert_record, row=row, fail=fail))
        future.add_done_callback(partial(self._settle, rec, report))
        return future

    def _settle(self, rec: Record, report: bool, future: asyncio.Future) -> None:
        """한 번 저장해 본 결과를 메모리에 반영한다. 실패하면 재시도 태스크를 띄운다."""
        before = len(self._unsaved)
        error = future.exception()
        key = (rec.session_id, rec.record_id)
        if error is None:
            rec.saved = True
            self._unsaved.pop(key, None)
        else:
            log.error("기록 저장 실패: %s", rec.record_id, exc_info=error)
            self._unsaved[key] = rec
            if not self._closed and (self._retry_task is None or self._retry_task.done()):
                self._retry_task = self._loop.create_task(self._retry_loop())
        if report:
            self._report(before, failed=error is not None)

    async def _retry_loop(self) -> None:
        """미저장 기록이 없어질 때까지 엔진 시계로 기다렸다 다시 저장한다. 기다리는 동안 save를 붙잡지 않는다."""
        delay = self._retry_base_s
        while self._unsaved:
            await self._clock.sleep_until(self._clock.now() + delay)
            await self.retry_now()
            delay = min(delay * 2, self._retry_max_s)

    def _take_failure(self, kind: str) -> bool:
        for i, k in enumerate(self._failures):
            if k is None or k == kind:
                del self._failures[i]
                return True
        return False

    def _report(self, before: int, failed: bool) -> None:
        """저장 실패면 record.save_failed, 미저장 건수가 바뀌었으면 status.changed(GET /api/devices의 storage 부분)."""
        n = len(self._unsaved)
        if failed:
            self._publish("record.save_failed", {"unsaved": n, "message": f"기록 {n}건을 저장하지 못했습니다"})
        if n != before:
            self._publish("status.changed", {"storage": {"unsaved_records": n}})

    def _publish(self, type: str, data: dict) -> None:
        try:
            self._events.publish(type, None, data)
        except Exception:
            log.exception("이벤트 발행 실패: %s", type)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA)
    except Exception:
        conn.close()
        raise
    return conn


def _resolve(future: asyncio.Future, result: Any, error: Exception | None) -> None:
    if future.done():  # 기다리던 쪽이 취소함
        return
    if error is None:
        future.set_result(result)
    else:
        future.set_exception(error)


def _log_failure(what: str, future: asyncio.Future) -> None:
    if not future.cancelled() and future.exception() is not None:
        log.error(what, exc_info=future.exception())


def _insert_record(conn: sqlite3.Connection, row: tuple, fail: bool) -> None:
    if fail:
        raise RuntimeError("저장 실패 주입(시험)")
    with conn:
        conn.execute("INSERT INTO records (record_id, session_id, kind, at, saved, json) VALUES (?, ?, ?, ?, 1, ?)",
                     row)


def _insert_session(conn: sqlite3.Connection, info: dict) -> None:
    with conn:
        conn.execute("INSERT INTO sessions (session_id, previous_session_id, started_at) VALUES (?, ?, ?)",
                     (info["session_id"], info["previous_session_id"], info["started_at"]))


def _load_records(conn: sqlite3.Connection, session_id: str) -> list[Record] | None:
    rows = conn.execute("SELECT json FROM records WHERE session_id = ? ORDER BY at, rowid", (session_id,)).fetchall()
    if not rows and _load_session(conn, session_id) is None:
        return None
    return [_RECORD.validate_json(data) for (data,) in rows]


def _load_session(conn: sqlite3.Connection, session_id: str) -> dict | None:
    row = conn.execute("SELECT session_id, previous_session_id, started_at FROM sessions WHERE session_id = ?",
                       (session_id,)).fetchone()
    if row is None:
        return None
    return dict(zip(("session_id", "previous_session_id", "started_at"), row))
