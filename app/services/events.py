"""화면 이벤트 발행 (명세서 GET /api/events). 버스는 seq를 매기고 구독자별 큐로 나눠 준다. SSE 전송은 app/api/events.py."""
import asyncio
from typing import Protocol

from app.schemas.event import Event, EventType
from app.services.procedure_engine.clock import Clock, iso


class EventSink(Protocol):
    def publish(self, type: EventType, turn_id: str | None, data: dict) -> Event:
        """seq를 하나 올리고 봉투를 씌워 구독자에게 보낸다."""
        ...


class SubscriptionClosed(Exception):
    """큐가 넘쳐 버스가 끊은 구독. 화면은 다시 붙어 GET /api/session으로 복구한다."""


class Subscription:
    def __init__(self, bus: "EventBus", maxsize: int):
        self._bus = bus
        self.queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize)  # None: close()가 기다리는 쪽을 깨우는 표시
        self.closed = False

    async def get(self) -> Event:
        if self.closed:
            raise SubscriptionClosed
        event = await self.queue.get()
        if event is None:
            raise SubscriptionClosed
        return event

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._bus._subscriptions.discard(self)
        if not self.queue.full():  # 가득 찼으면 기다리는 쪽이 없고, 다음 get()이 closed를 본다
            self.queue.put_nowait(None)


class EventBus:
    """같은 이벤트 루프 안에서만 publish한다. 스레드에서는 call_soon_threadsafe로 넘긴다."""

    # TODO(미정: 임시값) 명세서는 "크기 제한이 있는 송신 큐"만 정함
    QUEUE_SIZE = 256

    def __init__(self, clock: Clock):
        self._clock = clock
        self._subscriptions: set[Subscription] = set()
        self.seq = 0  # 마지막으로 낸 이벤트 번호(GET /api/session의 seq)

    def publish(self, type: EventType, turn_id: str | None, data: dict) -> Event:
        self.seq += 1
        event = Event(type=type, seq=self.seq, turn_id=turn_id, at=iso(self._clock.wall()), data=data)
        for sub in list(self._subscriptions):
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                sub.close()  # 느린 연결 하나가 엔진을 붙잡지 않게 끊는다
        return event

    def subscribe(self, maxsize: int = QUEUE_SIZE) -> Subscription:
        sub = Subscription(self, maxsize)
        self._subscriptions.add(sub)
        return sub

    def close_all(self) -> None:
        """서버를 끌 때 열린 구독을 모두 닫는다(기다리던 SSE 스트림이 끝난다)."""
        for sub in list(self._subscriptions):
            sub.close()
