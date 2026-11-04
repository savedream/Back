"""SQLite 기록 저장소 (명세서 6-13): 쓰기 스레드 저장, 실패·재시도(엔진 시계 지수 간격), 이벤트, 다시 열어 이전 세션 읽기."""
import asyncio
import sqlite3
import threading

import pytest

from app.schemas.record import UserReportRecord
from app.services.events import EventBus
from app.services.procedure_engine.clock import FakeClock
from app.services.records import SqliteRecordStore


def _record(n: int, session_id: str = "s_1", at: str | None = None) -> UserReportRecord:
    return UserReportRecord(record_id=f"r_{n:04d}", session_id=session_id, at=at or f"2026-10-13T14:00:0{n}+09:00",
                            turn_id=None, text="네", report="네", step_id="EM01-15", basis="wait_answer",
                            from_record="r_0001")


class Rig:
    def __init__(self, path):
        self.path = path
        self.clock = FakeClock()
        self.bus = EventBus(self.clock)
        self._sub = self.bus.subscribe(maxsize=0)  # 0: 크기 제한 없음
        self.store = SqliteRecordStore(path, self.clock, self.bus)

    def events(self) -> list[tuple[str, dict]]:
        out = []
        while not self._sub.queue.empty():
            e = self._sub.queue.get_nowait()
            out.append((e.type, e.data))
        return out

    def rows(self) -> list[tuple]:
        with sqlite3.connect(self.path) as conn:
            return conn.execute("SELECT record_id, session_id, kind, saved FROM records ORDER BY rowid").fetchall()

    def sessions(self) -> list[str]:
        with sqlite3.connect(self.path) as conn:
            return [s for (s,) in conn.execute("SELECT session_id FROM sessions ORDER BY rowid")]


async def until(check, timeout: float = 2.0) -> None:
    """쓰기 스레드 결과가 이벤트 루프로 넘어올 때까지 기다린다."""
    for _ in range(int(timeout / 0.005)):
        if check():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("기다려도 되지 않음")


@pytest.fixture
async def rig(tmp_path):
    r = Rig(tmp_path / "records.db")
    r.store.start()
    yield r
    await r.store.close()


def _failed(n: int) -> tuple[str, dict]:
    return "record.save_failed", {"unsaved": n, "message": f"기록 {n}건을 저장하지 못했습니다"}


def _storage(n: int) -> tuple[str, dict]:
    return "status.changed", {"storage": {"unsaved_records": n}}


async def test_save_writes_sqlite_and_marks_saved(rig):
    assert await rig.store.save(_record(1)) is True
    assert [(r.record_id, r.saved) for r in rig.store.records] == [("r_0001", True)]
    assert rig.rows() == [("r_0001", "s_1", "user_report", 1)]
    assert rig.store.unsaved() == [] and rig.events() == []


async def test_failed_save_stays_unsaved_and_publishes(rig):
    rig.store.fail_next_save()
    assert await rig.store.save(_record(1)) is False
    assert await rig.store.save(_record(2)) is True
    assert [(r.record_id, r.saved) for r in rig.store.records] == [("r_0001", False), ("r_0002", True)]
    assert [r.record_id for r in rig.store.unsaved()] == ["r_0001"]
    assert rig.rows() == [("r_0002", "s_1", "user_report", 1)]
    assert rig.events() == [_failed(1), _storage(1)]
    # 이 프로세스의 세션은 메모리에서 발생 순서대로(미저장 포함)
    assert [r.record_id for r in await rig.store.session_records("s_1")] == ["r_0001", "r_0002"]


async def test_fail_next_save_of_one_kind(rig):
    rig.store.fail_next_save("device_result")
    assert await rig.store.save(_record(1)) is True


async def test_retry_backs_off_on_engine_clock(rig):
    rig.store.fail_next_save()
    rig.store.fail_next_save()  # 첫 재시도도 실패
    assert await rig.store.save(_record(1)) is False
    assert rig.events() == [_failed(1), _storage(1)]
    rig.clock.advance(0.5)
    await asyncio.sleep(0.05)
    assert rig.events() == []
    rig.clock.advance(0.5)
    await until(lambda: rig.bus.seq == 3)
    assert rig.events() == [_failed(1)]  # 건수가 그대로면 status.changed는 없다
    rig.clock.advance(1.5)  # 다음 재시도는 2초 뒤
    await asyncio.sleep(0.05)
    assert rig.store.records[0].saved is False and rig.rows() == []
    rig.clock.advance(0.5)
    await until(lambda: rig.store.records[0].saved)
    assert rig.store.unsaved() == [] and rig.rows() == [("r_0001", "s_1", "user_report", 1)]
    assert rig.events() == [_storage(0)]


async def test_retry_now(rig):
    assert await rig.store.retry_now() == (0, 0)
    rig.store.fail_next_save()
    await rig.store.save(_record(1))
    rig.events()
    assert await rig.store.retry_now() == (1, 0)
    assert rig.store.records[0].saved is True and rig.store.unsaved() == []
    assert rig.events() == [_storage(0)]


async def test_reopen_reads_previous_session_by_time(tmp_path):
    first = Rig(tmp_path / "records.db")
    first.store.start()
    await first.store.save_session("s_1", None, "2026-10-13T14:00:00+09:00")
    first.store.fail_next_save()
    await first.store.save(_record(1))
    await first.store.save(_record(2))
    await first.store.retry_now()  # r_0001이 r_0002보다 나중에 들어간다
    await first.store.close()

    second = Rig(tmp_path / "records.db")
    second.store.start()
    try:
        records = await second.store.session_records("s_1")
        assert [(r.record_id, r.kind, r.saved) for r in records] == [("r_0001", "user_report", True),
                                                                    ("r_0002", "user_report", True)]
        assert records[0] == _record(1).model_copy(update={"saved": True})
        assert await second.store.session_info("s_1") == {"session_id": "s_1", "previous_session_id": None,
                                                          "started_at": "2026-10-13T14:00:00+09:00"}
        assert second.store.records == []
    finally:
        await second.store.close()


async def test_unknown_session_is_none_and_empty_session_is_empty(rig):
    assert await rig.store.session_records("s_x") is None
    assert await rig.store.session_info("s_x") is None
    await rig.store.save_session("s_2", "s_1", "2026-10-13T14:05:00+09:00")
    assert await rig.store.session_records("s_2") == []
    assert (await rig.store.session_info("s_2"))["previous_session_id"] == "s_1"


async def test_close_flushes_queued_writes(rig):
    tasks = [asyncio.create_task(rig.store.save(_record(n))) for n in (1, 2, 3)]
    await asyncio.sleep(0)  # 세 기록이 쓰기 큐에 들어간다
    await rig.store.close()
    assert [row[0] for row in rig.rows()] == ["r_0001", "r_0002", "r_0003"]
    assert await asyncio.gather(*tasks) == [True, True, True]


async def test_writes_run_on_writer_thread_without_blocking_loop(rig):
    release, threads = threading.Event(), []

    def slow(conn: sqlite3.Connection) -> int:
        threads.append(threading.get_ident())
        release.wait(2)
        return conn.execute("SELECT count(*) FROM records").fetchone()[0]

    job = asyncio.ensure_future(rig.store.run(slow))
    await until(lambda: threads)
    await asyncio.sleep(0.02)  # 쓰기 스레드가 붙잡혀 있어도 이벤트 루프는 돈다
    assert not job.done() and threads[0] != threading.get_ident()
    release.set()
    assert await job == 0


async def test_connection_uses_wal_and_full_sync(rig):
    mode, sync = await rig.store.run(lambda c: (c.execute("PRAGMA journal_mode").fetchone()[0],
                                                c.execute("PRAGMA synchronous").fetchone()[0]))
    assert (mode, sync) == ("wal", 2)  # 2: FULL


async def test_failed_session_row_is_retried(rig):
    rig.store.fail_next_save("session")
    with pytest.raises(RuntimeError):
        await rig.store.save_session("s_1", None, "2026-10-13T14:00:00+09:00")
    assert rig.sessions() == []
    await asyncio.sleep(0)  # 재시도 태스크가 엔진 시계로 기다리기 시작한다
    rig.clock.advance(1)  # 기록과 같은 재시도 간격
    await until(lambda: rig.sessions() == ["s_1"])
    assert rig.events() == []  # 세션 행은 미저장 기록 건수에 넣지 않는다


async def test_close_tries_unsaved_once_more(tmp_path):
    r = Rig(tmp_path / "records.db")
    r.store.start()
    r.store.fail_next_save("session")
    r.store.fail_next_save()
    await asyncio.gather(r.store.save_session("s_1", None, "2026-10-13T14:00:00+09:00"), return_exceptions=True)
    assert await r.store.save(_record(1)) is False
    await r.store.close()  # 재시도 간격(1초)이 지나지 않았어도 닫기 전에 한 번 더 저장해 본다
    assert r.rows() == [("r_0001", "s_1", "user_report", 1)] and r.sessions() == ["s_1"]
    assert r.store.unsaved() == [] and r.store.records[0].saved is True
