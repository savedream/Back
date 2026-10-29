"""처치 기록 저장. 지금은 메모리 저장소만 있고, SQLite 쓰기 스레드는 세션·기록 덩어리에서 같은 Protocol로 끼운다."""
from typing import Protocol

from app.schemas.record import Record


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
