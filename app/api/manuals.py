"""매뉴얼 API: GET /manuals.

응급 매뉴얼 화면(보기 전용)용. 매뉴얼 DB 값을 그대로 내보내고 엔진 상태는 바꾸지 않는다.
"""
from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.common.response import OkResponse, ok
from app.services.manual_db import ManualDB

router = APIRouter()

UNKNOWN_MANUAL_ID = "EM-00"  # 상황 불명확. 목록 맨 끝


class ManualSummary(BaseModel):
    manual_id: str
    situation_code: int
    name: str
    summary: str
    featured: int | None
    icon: str


def _situation_manuals(db: ManualDB) -> list[dict]:
    """상황 매뉴얼만. featured가 있는 것부터 그 순서대로, 나머지는 priority 순, EM-00은 맨 끝."""
    manuals = [m for m in db.manuals_by_id.values() if m["type"] == "situation"]
    return sorted(manuals, key=lambda m: (m["manual_id"] == UNKNOWN_MANUAL_ID,
                                          m.get("featured") is None,
                                          m.get("featured") or 0,
                                          m["priority"]))


def _icon(manual_id: str) -> str:
    return f"/static/manuals/{manual_id}.svg"  # 파일은 UI가 제공


@router.get("/manuals", response_model=OkResponse[list[ManualSummary]])
def list_manuals(request: Request, featured: bool = False):
    """매뉴얼 목록. featured=true면 featured가 있는 것만. 준비 전에도 응답한다."""
    manuals = _situation_manuals(request.app.state.manual_db)
    if featured:
        manuals = [m for m in manuals if m.get("featured") is not None]
    return ok([
        ManualSummary(manual_id=m["manual_id"], situation_code=m["situation_code"], name=m["name"],
                      summary=m["summary"], featured=m.get("featured"), icon=_icon(m["manual_id"]))
        for m in manuals
    ])

