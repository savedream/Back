"""GET /api/events (명세서 "이벤트 스트림"): 화면으로 이벤트를 SSE로 보낸다. 준비 전에도 오류 없이 200으로 연다."""
import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.services.events import Subscription, SubscriptionClosed

router = APIRouter()

RETRY_MS = 1000  # 브라우저가 끊긴 뒤 다시 붙기까지 기다리는 시간


async def sse_frames(subscription: Subscription, ping_s: float) -> AsyncIterator[str]:
    """SSE 프레임을 낸다. 구독이 닫히거나(큐 넘침·서버 종료) 보내기가 끊기면 끝나고, 끝날 때 구독을 해제한다.

    event: 줄은 쓰지 않는다(화면이 봉투의 type으로 가른다). 쌓아 두었다가 다시 보내지도 않는다(Last-Event-ID 미사용).
    """
    try:
        yield f"retry: {RETRY_MS}\n\n"
        while True:
            try:
                event = await asyncio.wait_for(subscription.get(), ping_s)
            except TimeoutError:
                yield ": ping\n\n"  # 이벤트가 없는 동안 연결이 살아 있음을 알린다
                continue
            except SubscriptionClosed:
                return
            yield f"id: {event.seq}\ndata: {event.model_dump_json()}\n\n"
    finally:
        subscription.close()


@router.get("/events")
async def get_events(request: Request):
    """구독은 응답을 열 때 바로 시작한다. 끊기면 background가 구독을 해제한다(생성기는 끝나지 않았을 수 있다)."""
    state = request.app.state
    subscription = state.parts.events.subscribe()
    return StreamingResponse(
        sse_frames(subscription, state.settings.sse_ping_s),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
        background=BackgroundTask(subscription.close),
    )
