"""처치 기록 저장 (명세서 6-13). 서버는 SQLite 저장소(전용 쓰기 스레드)를, 시험은 메모리 저장소를 같은 Protocol로 끼운다."""
import asyncio
import logging
import queue
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import TypeAdapter

from app.schemas.record import Record
from app.schemas.session import SessionState
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
CREATE TABLE IF NOT EXISTS snapshots (
    session_id TEXT PRIMARY KEY,
    saved_wall REAL NOT NULL,
    saved_now REAL NOT NULL,
    boot_id TEXT,
    json TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Snapshot:
    """상태 스냅샷(6-14). saved_wall: 저장한 벽시계(POSIX 초), saved_now: 저장한 엔진 시계(단조 초)."""

    state: SessionState
    saved_wall: float
    saved_now: float
    boot_id: str | None


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
    세션 표의 행도 실패하면 같은 재시도 대상이다.
    sqlite3 연결은 쓰기 스레드 안에서만 만들고 쓴다(시작할 때 스레드를 띄우기 전에 지난 세션을 읽는 것만 따로).
    상태 스냅샷(6-14)도 run()으로 같은 스레드에서 저장한다.
    """

    def __init__(self, path: Path, clock: Clock, events: EventSink, *, retry_base_s: float = 1,
                 retry_max_s: float = 60, boot_id: str | None = None):
        self._path = path
        self._boot_id = boot_id  # 스냅샷에 남기는 지금 부팅의 ID
        self._clock = clock
        self._events = events
        self._retry_base_s = retry_base_s
        self._retry_max_s = retry_max_s
        self._jobs: queue.SimpleQueue = queue.SimpleQueue()  # (작업 함수, Future). None이면 스레드를 끝낸다
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._closing = False  # close()를 시작함. 새 재시도 태스크를 띄우지 않는다
        self._closed = False
        self._failures: list[str | None] = []
        self._all: list[Record] = []  # 이 프로세스에서 받은 기록(발생 순서)
        self._sessions: dict[str, list[Record]] = {}  # 이 프로세스에서 연 세션별 기록(발생 순서)
        self._session_info: dict[str, dict] = {}
        self._session_ids: set[str] = set()  # 세션 표의 세션 ID(SQLite + 이 프로세스에서 연 세션)
        self.last_session_id: str | None = None  # 시작할 때 세션 표의 마지막 세션(전원을 켤 때의 previous_session_id)
        self.last_snapshot: Snapshot | None = None  # 시작할 때 마지막으로 저장된 상태 스냅샷
        self._unsaved: dict[tuple[str, str], Record] = {}  # 저장에 실패해 다시 저장할 기록
        self._unsaved_sessions: dict[str, dict] = {}  # 저장에 실패해 다시 저장할 세션 표의 행
        self._snapshot: tuple | None = None  # 아직 쓰지 않은 마지막 스냅샷 행. 쓰기 스레드와 함께 쓰므로 잠금으로 바꾼다
        self._snapshot_lock = threading.Lock()
        self._snapshot_job: asyncio.Future | None = None
        self._retry_lock = asyncio.Lock()
        self._retry_task: asyncio.Task | None = None

    def start(self) -> None:
        """지난 세션을 읽어 두고 쓰기 스레드를 띄운다. 스레드 결과를 이 이벤트 루프로 넘기므로 루프 안에서 부른다."""
        self._read_previous()
        self._loop = asyncio.get_running_loop()
        self._thread = threading.Thread(target=self._write_loop, name="record-writer", daemon=True)
        self._thread.start()

    async def close(self) -> None:
        """재시도를 멈추고 미저장 기록·세션을 마지막으로 한 번 더 저장해 본 뒤, 큐에 남은 쓰기를 마치고 쓰기 스레드를 끝낸다."""
        if self._thread is None or self._closing:
            return
        self._closing = True
        if self._retry_task is not None:
            self._retry_task.cancel()
            await asyncio.gather(self._retry_task, return_exceptions=True)
        # 앞서 넣은 쓰기 결과가 모두 반영된 뒤에(같은 기록을 두 번 넣지 않게) 다시 저장한다
        await asyncio.gather(self._enqueue(lambda conn: None), return_exceptions=True)
        await self.retry_now()
        self._closed = True
        self._jobs.put(None)
        await asyncio.to_thread(self._thread.join)

    def _read_previous(self) -> None:
        """세션 표의 세션 ID들과 마지막 상태 스냅샷을 읽는다. 읽지 못해도 서버는 시작한다(새 세션)."""
        try:
            conn = _connect(self._path)
            try:
                ids = [s for (s,) in conn.execute("SELECT session_id FROM sessions ORDER BY rowid")]
                snap = conn.execute("SELECT json, saved_wall, saved_now, boot_id FROM snapshots "
                                    "ORDER BY rowid DESC LIMIT 1").fetchone()
            finally:
                conn.close()
        except Exception:
            log.exception("지난 세션을 읽지 못함")
            return
        self._session_ids.update(ids)
        self.last_session_id = ids[-1] if ids else None
        if snap is not None:
            try:
                self.last_snapshot = Snapshot(SessionState.model_validate_json(snap[0]), *snap[1:])
            except ValueError:
                log.exception("상태 스냅샷을 읽지 못함")

    async def resume_session(self, session_id: str) -> list[Record]:
        """이어 가는 세션(6-14)의 지난 기록을 SQLite에서 읽어 이 프로세스의 세션으로 두고 돌려준다(세션 기록이 끊기기 전부터 이어지게)."""
        try:
            earlier = await self.run(partial(_load_records, session_id=session_id)) or []
        except Exception:
            log.exception("이어 가는 세션의 지난 기록을 읽지 못함: %s", session_id)
            return []
        self._sessions[session_id] = earlier + self._sessions.get(session_id, [])
        return earlier

    def session_ids(self) -> frozenset[str]:
        """이미 쓴 세션 ID. 엔진이 새 세션 ID를 겹치지 않게 고르는 데 쓴다."""
        return frozenset(self._session_ids)

    @property
    def records(self) -> list[Record]:
        """이 프로세스에서 받은 모든 기록(발생 순서)."""
        return list(self._all)

    def unsaved(self) -> list[Record]:
        """저장에 실패해 아직 다시 저장하지 못한 기록."""
        return list(self._unsaved.values())

    def fail_next_save(self, kind: str | None = None) -> None:
        """다음 저장 하나를 실패시킨다(재시도 포함). kind를 주면 그 종류의 다음 기록만(세션 표의 행은 "session").
        쓰기 스레드가 SQL을 쓰기 전에 실패로 처리한다."""
        self._failures.append(kind)

    async def save(self, record: Record) -> bool:
        """메모리에 바로 두고 쓰기 큐로 넘긴 뒤 첫 시도 결과를 돌려준다. 재시도는 기다리지 않는다."""
        rec = record.model_copy(update={"saved": False})
        self._all.append(rec)
        self._sessions.setdefault(rec.session_id, []).append(rec)
        await asyncio.wait([self._attempt(rec, report=True)])  # 기다리던 쪽이 취소돼도 결과는 _settle이 반영한다
        return rec.saved

    async def retry_now(self) -> tuple[int, int]:
        """미저장 기록·세션을 지금 다시 저장해 본다. (저장한 기록 수, 남은 기록 수)."""
        async with self._retry_lock:
            sessions = [self._attempt_session(info) for info in list(self._unsaved_sessions.values())]
            pending = list(self._unsaved.values())
            if not pending:
                if sessions:
                    await asyncio.wait(sessions)
                return 0, 0
            before = len(self._unsaved)
            await asyncio.wait(sessions + [self._attempt(rec, report=False) for rec in pending])
            saved = sum(rec.saved for rec in pending)
            self._report(before, failed=saved < len(pending))
            return saved, len(self._unsaved)

    def save_session(self, session_id: str, previous_session_id: str | None, started_at: str) -> asyncio.Future:
        """세션 표에 남긴다. 바로 쓰기 큐에 넣으므로(그 세션 기록보다 먼저) 기다리지 않아도 된다. 실패하면 기록처럼 재시도한다."""
        info = {"session_id": session_id, "previous_session_id": previous_session_id, "started_at": started_at}
        self._session_info[session_id] = info
        self._session_ids.add(session_id)
        self._sessions.setdefault(session_id, [])
        return self._attempt_session(info)

    def save_snapshot(self, state: SessionState) -> None:
        """상태 스냅샷(6-14)을 쓰기 스레드로 저장한다(세션마다 최신 하나). 쓰기가 밀리면 마지막 것만 쓴다.

        이어 갈 때 단조 시계의 마감을 남은 시간으로 바꿀 수 있게 저장 시각(벽시계 POSIX 초·엔진 시계)과 boot_id를 함께 둔다.
        """
        row = (state.session_id, self._clock.wall().timestamp(), self._clock.now(), self._boot_id,
               state.model_dump_json())
        with self._snapshot_lock:
            self._snapshot = row
        if self._snapshot_job is None or self._snapshot_job.done():
            self._queue_snapshot()

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
            self._start_retry()
        if report:
            self._report(before, failed=error is not None)

    def _attempt_session(self, info: dict) -> asyncio.Future:
        future = self._enqueue(partial(_insert_session, info=info, fail=self._take_failure("session")))
        future.add_done_callback(partial(self._settle_session, info))
        return future

    def _settle_session(self, info: dict, future: asyncio.Future) -> None:
        if future.cancelled():
            return
        if future.exception() is None:
            self._unsaved_sessions.pop(info["session_id"], None)
        else:
            log.error("세션 저장 실패: %s", info["session_id"], exc_info=future.exception())
            self._unsaved_sessions[info["session_id"]] = info
            self._start_retry()

    def _queue_snapshot(self) -> None:
        self._snapshot_job = self._enqueue(self._write_snapshot)
        self._snapshot_job.add_done_callback(self._snapshot_written)

    def _write_snapshot(self, conn: sqlite3.Connection) -> None:
        """쓰기 스레드. 그사이 들어온 스냅샷 중 마지막 것 하나만 쓴다."""
        with self._snapshot_lock:
            row, self._snapshot = self._snapshot, None
        if row is not None:
            with conn:
                conn.execute("INSERT OR REPLACE INTO snapshots (session_id, saved_wall, saved_now, boot_id, json) "
                             "VALUES (?, ?, ?, ?, ?)", row)

    def _snapshot_written(self, future: asyncio.Future) -> None:
        """쓰는 동안 새 스냅샷이 들어왔으면 이어서 쓴다. 실패하면 로그만 남기고 다음 상태 변화 때 다시 쓴다."""
        if future.exception() is not None:
            log.error("상태 스냅샷 저장 실패", exc_info=future.exception())
        elif self._snapshot is not None and not self._closed:
            self._queue_snapshot()

    def _start_retry(self) -> None:
        if not self._closing and (self._retry_task is None or self._retry_task.done()):
            self._retry_task = self._loop.create_task(self._retry_loop())

    async def _retry_loop(self) -> None:
        """미저장 기록·세션이 없어질 때까지 엔진 시계로 기다렸다 다시 저장한다. 기다리는 동안 save를 붙잡지 않는다."""
        delay = self._retry_base_s
        while self._unsaved or self._unsaved_sessions:
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
    """WAL과 synchronous=FULL: 키트 전원이 갑자기 끊겨도 저장했다고 답한 쓰기는 남게 한다."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
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


def _insert_record(conn: sqlite3.Connection, row: tuple, fail: bool) -> None:
    if fail:
        raise RuntimeError("저장 실패 주입(시험)")
    with conn:
        conn.execute("INSERT INTO records (record_id, session_id, kind, at, saved, json) VALUES (?, ?, ?, ?, 1, ?)",
                     row)


def _insert_session(conn: sqlite3.Connection, info: dict, fail: bool) -> None:
    if fail:
        raise RuntimeError("저장 실패 주입(시험)")
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
