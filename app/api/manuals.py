"""매뉴얼 API: GET /manuals, GET /manuals/{manual_id}.

응급 매뉴얼 화면(보기 전용)용. 매뉴얼 DB 값을 그대로 내보내고 엔진 상태는 바꾸지 않는다.
"""
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.common.error_code import ErrorCode
from app.common.exceptions import AppError
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


class ViewStep(BaseModel):
    no: int
    kind: Literal["check", "call", "action"]
    title: str
    text: str
    image: str | None


class ManualCard(BaseModel):
    manual_id: str
    situation_code: int
    name: str
    summary: str
    call_119: str
    view_steps: list[ViewStep]


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


@router.get("/manuals/{manual_id}", response_model=OkResponse[ManualCard])
def get_manual(request: Request, manual_id: str):
    """매뉴얼 보기용 카드. 엔진 단계(steps)는 내보내지 않는다. 없는 ID나 공통 절차(COM-*)는 404.

    카드가 no 순서(1부터 빠짐없이)인지는 매뉴얼 DB를 적재할 때 검사한다.
    """
    manual = request.app.state.manual_db.manuals_by_id.get(manual_id)
    if manual is None or manual["type"] != "situation":
        raise AppError(ErrorCode.MANUAL_NOT_FOUND, f"{manual_id} 매뉴얼이 없습니다")
    return ok(ManualCard(
        manual_id=manual["manual_id"], situation_code=manual["situation_code"], name=manual["name"],
        summary=manual["summary"], call_119=manual["call_119"],
        view_steps=manual["view_steps"],
    ))
