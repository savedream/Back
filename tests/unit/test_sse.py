"""SSE 이벤트 스트림 (명세서 GET /api/events): 프레임 생성기와 엔드포인트."""
import asyncio
import json

import pytest

from app.api.events import sse_frames
from app.config import Settings
from app.main import create_app
from app.services.events import EventBus
from app.services.procedure_engine.clock import FakeClock


async def next_frame(frames) -> str:
    return await asyncio.wait_for(anext(frames), 1)


async def rest(frames) -> list[str]:
    return [frame async for frame in frames]


async def test_first_frame_is_retry():
    frames = sse_frames(EventBus(FakeClock()).subscribe(), ping_s=60)
    assert await next_frame(frames) == "retry: 1000\n\n"
    await frames.aclose()


async def test_event_frame_has_id_and_one_line_data_without_event_line():
    bus = EventBus(FakeClock())
    frames = sse_frames(bus.subscribe(), ping_s=60)
    await next_frame(frames)
    bus.publish("guide.say", "t_0001", {"text": "안녕\n하세요"})
    frame = await next_frame(frames)
    id_line, data_line, end_a, end_b = frame.split("\n")
    assert id_line == "id: 1"
    assert data_line.startswith("data: ")
    assert json.loads(data_line.removeprefix("data: ")) == {
        "type": "guide.say", "seq": 1, "turn_id": "t_0001", "at": "2026-10-13T14:00:00+09:00",
        "data": {"text": "안녕\n하세요"}}
    assert (end_a, end_b) == ("", "")  # 빈 줄로 끝남
    assert "event:" not in frame
    await frames.aclose()


async def test_ping_when_no_event():
    bus = EventBus(FakeClock())
    frames = sse_frames(bus.subscribe(), ping_s=0.01)
    await next_frame(frames)
    assert await next_frame(frames) == ": ping\n\n"
    bus.publish("ui.notice", None, {})
    assert (await next_frame(frames)).startswith("id: 1\n")
    await frames.aclose()


async def test_ends_when_queue_overflows():
    bus = EventBus(FakeClock())
    sub = bus.subscribe(maxsize=1)
    frames = sse_frames(sub, ping_s=60)
    await next_frame(frames)
    bus.publish("timer.tick", None, {})
    bus.publish("timer.tick", None, {})  # 큐가 넘쳐 버스가 구독을 끊는다
    assert await asyncio.wait_for(rest(frames), 1) == []
    assert sub.closed


async def test_close_all_ends_waiting_generator():
    bus = EventBus(FakeClock())
    sub = bus.subscribe()
    frames = sse_frames(sub, ping_s=60)
    await next_frame(frames)
    tail = asyncio.create_task(rest(frames))
    await asyncio.sleep(0)  # 생성기가 이벤트를 기다리는 자리까지 보낸다
    bus.close_all()
    assert await asyncio.wait_for(tail, 1) == []
    assert sub.closed


async def test_cancel_or_close_unsubscribes():
    bus = EventBus(FakeClock())
    cancelled, closed = bus.subscribe(), bus.subscribe()
    waiting = asyncio.create_task(rest(sse_frames(cancelled, ping_s=60)))
    await asyncio.sleep(0)
    waiting.cancel()  # 기다리는 중에 클라이언트가 끊겨 취소됨
    with pytest.raises(asyncio.CancelledError):
        await waiting
    frames = sse_frames(closed, ping_s=60)
    await next_frame(frames)
    await frames.aclose()  # 보내는 중에 닫힘
    assert cancelled.closed and closed.closed


class Stream:
    """앱을 ASGI로 직접 불러 응답이 끝나기 전에 메시지를 받는다(TestClient·httpx는 응답이 끝나야 돌려주므로 끝나지 않는 스트림에 못 쓴다)."""

    def __init__(self, app, path: str = "/api/events"):
        self._to_app: asyncio.Queue = asyncio.Queue()
        self._from_app: asyncio.Queue = asyncio.Queue()
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
                 "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
                 "query_string": b"", "headers": [], "client": ("test", 1), "server": ("test", 80)}
        self.task = asyncio.create_task(app(scope, self._to_app.get, self._from_app.put))

    async def message(self) -> dict:
        return await asyncio.wait_for(self._from_app.get(), 1)

    async def disconnect(self) -> None:
        self._to_app.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(self.task, 1)


async def test_stream_opens_200_even_before_ready(app):
    assert not app.state.readiness.ready  # conftest의 app은 준비 전 상태
    async with app.router.lifespan_context(app):
        stream = Stream(app)
        start = await stream.message()
        headers = {k.decode(): v.decode() for k, v in start["headers"]}
        assert start["status"] == 200
        assert headers["content-type"].startswith("text/event-stream")
        assert headers["cache-control"] == "no-cache"
        assert "content-encoding" not in headers
        assert (await stream.message())["body"] == b"retry: 1000\n\n"
        bus = app.state.parts.events
        assert len(bus._subscriptions) == 1
        await stream.disconnect()
        assert len(bus._subscriptions) == 0  # 끊기면 구독 해제


async def test_event_reaches_stream_and_shutdown_ends_it(app):
    async with app.router.lifespan_context(app):
        stream = Stream(app)
        await stream.message()
        await stream.message()
        app.state.parts.events.publish("ui.notice", None, {"code": "STT_EMPTY"})
        body = (await stream.message())["body"].decode()
        assert body.startswith("id: 1\ndata: ") and '"code":"STT_EMPTY"' in body
    # 서버를 끌 때 열린 스트림을 먼저 닫아 응답이 끝난다
    assert await stream.message() == {"type": "http.response.body", "body": b"", "more_body": False}
    await asyncio.wait_for(stream.task, 1)


async def test_ping_interval_comes_from_settings(tmp_path):
    app = create_app(Settings(_env_file=None, fake_auto_ready=False, records_db_path=tmp_path / "records.db",
                              sse_ping_s=0.01))
    async with app.router.lifespan_context(app):
        stream = Stream(app)
        await stream.message()
        await stream.message()
        assert (await stream.message())["body"] == b": ping\n\n"
        await stream.disconnect()
