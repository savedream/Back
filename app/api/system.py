"""시스템 API: GET /health."""
from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.common.response import OkResponse, ok

router = APIRouter()


class HealthOut(BaseModel):
    ready: bool
    version: str
    manual_db: str
    model: str


@router.get("/health", response_model=OkResponse[HealthOut])
def health(request: Request):
    """준비 상태. 적재 중에도 응답하고, 장치는 열지 않는다."""
    settings = request.app.state.settings
    return ok(
        HealthOut(
            ready=request.app.state.readiness.ready,
            version=settings.app_version,
            manual_db=settings.manual_db_version,
            model=settings.model_version,
        )
    )
