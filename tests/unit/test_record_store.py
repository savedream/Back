"""메모리 기록 저장소: 저장, 저장 실패 넣기."""
from app.schemas.record import UserReportRecord
from app.services.records import MemoryRecordStore


def _record(n: int) -> UserReportRecord:
    return UserReportRecord(record_id=f"r_{n:04d}", session_id="s_1", at="t", turn_id=None, text="네", report="네",
                            step_id="EM01-15", basis="wait_answer", from_record="r_0001")


async def test_save_marks_saved():
    store = MemoryRecordStore()
    assert await store.save(_record(1)) is True
    assert [(r.record_id, r.saved) for r in store.records] == [("r_0001", True)]


async def test_fail_next_save_fails_once_then_recovers():
    store = MemoryRecordStore()
    store.fail_next_save()
    assert await store.save(_record(1)) is False
    assert await store.save(_record(2)) is True
    assert [(r.record_id, r.saved) for r in store.records] == [("r_0001", False), ("r_0002", True)]
