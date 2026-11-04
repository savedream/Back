"""위치 API (명세서 "위치"): GET /api/location.

GPS는 백그라운드에서 계속 읽고(지금은 가짜 GPS), 이 API는 마지막 값만 돌려준다. 이후 갱신은 location.updated 이벤트로 나간다.
"""
from fastapi import APIRouter, Request

from app.common.error_code import ErrorCode
from app.common.exceptions import AppError
from app.common.response import OkResponse, ok
from app.schemas.device import Location

router = APIRouter()


@router.get("/location", response_model=OkResponse[Location])
def get_location(request: Request):
    """현재 위치. 못 잡았으면 200 + fix: false + last_known(없으면 null), GPS 모듈이 없으면 503. 준비 전에도 응답한다."""
    location = request.app.state.parts.gps.location()
    if location is None:
        raise AppError(ErrorCode.DEVICE_UNAVAILABLE, "GPS가 연결되지 않았습니다")
    return ok(location)
