"""FastAPI 앱.

실행: uvicorn app.main:app --host 127.0.0.1 --port 8000 (워커 1개, --reload 없이)
"""
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.api import system
from app.common.handlers import install_error_handlers
from app.config import Settings, get_settings
from app.core.readiness import Component, Readiness
from app.services.manual_db import load_manual_db


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 매뉴얼 DB 적재·검사. 실패하면 ManualDBError로 서버가 시작되지 않는다
        app.state.manual_db = load_manual_db(settings.manual_db_path)
        app.state.readiness.mark_ready(Component.MANUAL_DB)
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

    # 인증 없음: 화면은 키트가 만든 전용 Wi-Fi(AP)에서만 접속하고, 접근은 Wi-Fi 비밀번호로 막는다
    api = APIRouter(prefix="/api")
    api.include_router(system.router)
    app.include_router(api)
    return app


app = create_app()
