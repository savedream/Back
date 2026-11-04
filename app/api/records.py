"""처치 기록 API (명세서 "처치 기록"): POST /api/records/retry."""
from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.common.error_code import ErrorCode
from app.common.exceptions import AppError
from app.common.response import OkResponse, ok

router = APIRouter()


class RetryOut(BaseModel):
    saved: int
    unsaved: int


@router.post("/records/retry", response_model=OkResponse[RetryOut])
async def retry_records(request: Request):
    """미저장 기록을 지금 저장해 본다. 결과는 늘 status.changed로도 보내고, 그래도 남으면 500 SAVE_FAILED."""
    parts = request.app.state.parts
    saved, left = await parts.records.retry_now()
    parts.events.publish("status.changed", None, {"storage": {"unsaved_records": left}})
    if left:
        raise AppError(ErrorCode.SAVE_FAILED, f"기록 {left}건을 저장하지 못했습니다")
    return ok(RetryOut(saved=saved, unsaved=left))
