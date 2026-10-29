"""화면으로 보내는 이벤트 봉투 (명세서 GET /api/events 이벤트 명세)."""
from typing import Literal

from pydantic import BaseModel

EventType = Literal[
    "speech.state",
    "speech.final",
    "turn.result",
    "turn.latency",
    "guide.step",
    "guide.say",
    "session.updated",
    "device.state",
    "timer.tick",
    "alert.started",
    "alert.ended",
    "location.updated",
    "status.changed",
    "record.save_failed",
    "ui.notice",
    "error",
]


class Event(BaseModel):
    type: EventType
    seq: int  # 서버가 1씩 올림(SSE id:와 같은 값)
    turn_id: str | None  # 연결·상태 이벤트는 None
    at: str  # ISO 8601
    data: dict
