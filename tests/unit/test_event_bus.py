"""이벤트 버스: seq, 봉투, 구독자별 큐 (명세서 GET /api/events)."""
import pytest

from app.services.events import EventBus, SubscriptionClosed
from app.services.procedure_engine.clock import FakeClock


async def test_publish_numbers_events_and_wraps_envelope():
    clock = FakeClock()
    bus = EventBus(clock)
    sub = bus.subscribe()
    bus.publish("turn.result", "t_0001", {"error_code": None})
    clock.advance(2)
    bus.publish("guide.say", "t_0001", {"text": "안녕"})
    a, b = await sub.get(), await sub.get()
    assert (a.seq, a.type, a.turn_id, a.at) == (1, "turn.result", "t_0001", "2026-10-13T14:00:00+09:00")
    assert (b.seq, b.at, b.data) == (2, "2026-10-13T14:00:02+09:00", {"text": "안녕"})
    assert bus.seq == 2


async def test_every_subscriber_gets_every_event():
    bus = EventBus(FakeClock())
    first, second = bus.subscribe(), bus.subscribe()
    bus.publish("ui.notice", None, {"code": "STT_EMPTY"})
    assert (await first.get()).seq == (await second.get()).seq == 1


async def test_full_queue_drops_only_that_subscriber():
    bus = EventBus(FakeClock())
    slow, fast = bus.subscribe(maxsize=1), bus.subscribe()
    bus.publish("timer.tick", None, {})
    bus.publish("timer.tick", None, {})
    assert slow.closed and not fast.closed
    with pytest.raises(SubscriptionClosed):
        await slow.get()
    assert [(await fast.get()).seq for _ in range(2)] == [1, 2]
