"""위치 API (명세서 "위치"): GET /api/location, POST /api/location/announce.

GPS는 백그라운드에서 계속 읽고(지금은 가짜 GPS), 조회 API는 마지막 값만 돌려준다. 이후 갱신은 location.updated 이벤트로 나간다.
"""
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.common.error_code import ErrorCode
from app.common.exceptions import AppError
from app.common.response import OkResponse, ok
from app.core.readiness import require_ready
from app.schemas.device import Location
from app.services.procedure_engine.actions import SayAction
from app.services.procedure_engine.inputs import LocationButtonInput

router = APIRouter()

# 엔진이 고른 좌표 낭독 문장(6-11) → 버튼 아래 상태. 낭독 문장이 없으면 GPS 모듈이 없는 것(unavailable)
_ANNOUNCE_BY_TEMPLATE = {"T_GPS_OK": "fix", "T_GPS_LAST": "last", "T_GPS_WAIT": "wait"}
Announce = Literal["fix", "last", "wait", "unavailable"]


@router.get("/location", response_model=OkResponse[Location])
def get_location(request: Request):
    """현재 위치. 못 잡았으면 200 + fix: false + last_known(없으면 null), GPS 모듈이 없으면 503. 준비 전에도 응답한다."""
    location = request.app.state.parts.gps.location()
    if location is None:
        raise AppError(ErrorCode.DEVICE_UNAVAILABLE, "GPS가 연결되지 않았습니다")
    return ok(location)


class AnnounceOut(BaseModel):
    turn_id: str
    announce: Announce


@router.post("/location/announce", status_code=202, response_model=OkResponse[AnnounceOut],
             dependencies=[Depends(require_ready)])
async def announce_location(request: Request):
    """메인 화면의 119 신고 버튼. 음성 "119 불러 줘"(기타 type 2)와 같은 처리: T_OOS_2(휴대폰 신고 안내) 후 좌표 낭독,
    질문 중이면 질문 다시. GPS 모듈이 없어도 오류로 응답하지 않고 안내만 읽은 뒤 202 + unavailable.
    버튼 하나 = 턴 하나. 턴 잠금은 쓰지 않고 턴 번호만 받는다."""
    state = request.app.state
    turn_id = state.turn_lock.next_id()
    result = await state.parts.engine.submit(LocationButtonInput(turn_id=turn_id))
    said = [a.utterance.template_id for a in result.actions if isinstance(a, SayAction)]
    announce = next((_ANNOUNCE_BY_TEMPLATE[t] for t in said if t in _ANNOUNCE_BY_TEMPLATE), "unavailable")
    return ok(AnnounceOut(turn_id=turn_id, announce=announce))
