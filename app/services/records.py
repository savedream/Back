"""처치 기록 저장 자리. 메모리 구현은 절차 엔진 덩어리에서, SQLite 쓰기 스레드는 세션·기록 덩어리에서 끼운다."""
from typing import Protocol

from app.schemas.record import Record


class RecordSink(Protocol):
    async def save(self, record: Record) -> bool:
        """저장했으면 True. 실패해도 기능은 멈추지 않고 saved: false로 두고 재시도한다(명세서 6-13)."""
        ...
