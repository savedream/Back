"""화면 이벤트 발행 자리. 버스(seq 매기기, 구독자별 큐)는 절차 엔진 덩어리에서, SSE 전송은 세션·기록 덩어리에서 끼운다."""
from typing import Protocol

from app.schemas.event import Event, EventType


class EventSink(Protocol):
    def publish(self, type: EventType, turn_id: str | None, data: dict) -> Event:
        """seq를 하나 올리고 봉투를 씌워 구독자에게 보낸다."""
        ...
