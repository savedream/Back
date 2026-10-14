"""FastAPI 앱.

실행: uvicorn app.main:app --host 127.0.0.1 --port 8000 (워커 1개, --reload 없이)
"""
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.api import system
from app.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.readiness import Readiness


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # TODO(1단계): 매뉴얼 DB 적재·검사. 실패하면 시작하지 않는다
        # 서버가 먼저 응답하고, 모델 적재·예열은 백그라운드에서 한다 (뒤 단계)
        yield

    app = FastAPI(
        title="살려드림 백엔드",
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.readiness = Readiness()
    install_error_handlers(app)

    # TODO(미정: 아이패드 연결) 접근 제한이 정해지기 전까지 인증 없음
    api = APIRouter(prefix="/api")
    api.include_router(system.router)
    app.include_router(api)
    return app


app = create_app()
