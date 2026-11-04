"""FastAPI 앱.

실행: python -m app.run (app/run.py, 워커 1개, reload 없이. host·port는 SALYEO_HOST·SALYEO_PORT)
uvicorn app.main:app으로도 뜨지만, 그때는 스트림을 연 채 끄면 --timeout-graceful-shutdown까지(안 주면 끝없이) 기다린다.
"""
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import devices, events, location, manuals, records, session, speech, system, utterances
from app.common.handlers import install_error_handlers
from app.config import Settings, get_settings
from app.core.parts import build_parts
from app.core.readiness import Component, Readiness
from app.core.speech import SpeechCapture
from app.core.turn import TurnLock
from app.services.manual_db import load_manual_db
from app.services.procedure_engine.clock import Clock, SystemClock


def create_app(settings: Settings | None = None, *, clock: Clock | None = None) -> FastAPI:
    """clock: 시험에서 엔진 시계를 바꿔 끼운다(명세서 /api/utterances "시험용으로 엔진 시계를 바꿔 끼울 수 있게")."""
    settings = settings or get_settings()
    clock = clock or SystemClock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 매뉴얼 DB 적재·검사. 실패하면 ManualDBError로 서버가 시작되지 않는다
        app.state.manual_db = load_manual_db(settings.manual_db_path)
        readiness = app.state.readiness
        readiness.mark_ready(Component.MANUAL_DB)
        parts = app.state.parts = build_parts(settings, app.state.manual_db, clock)
        speech_capture = app.state.speech = SpeechCapture(parts, app.state.turn_lock, settings.max_utterance_s,
                                                          settings.model_timeout_s)
        parts.records.start()
        if settings.fake_auto_ready:
            for component in (Component.VAD, Component.STT, Component.TTS, Component.LLM, Component.LLM_WARMUP):
                readiness.mark_ready(component)
        # 준비가 끝났으면 새 세션(T_WELCOME)이나 같은 부팅의 스냅샷에서 이어 간다(T_RESUME).
        # 실제 부품을 백그라운드로 적재하게 되면(키트 단계) 끝난 뒤에 시작한다
        if readiness.ready:
            await parts.start_engine()
        yield
        parts.events.close_all()  # 남은 구독 정리(python -m app.run이면 연결을 기다리기 전에 이미 닫음)
        await speech_capture.close()  # 녹음·처리 중인 말하기 버튼 턴을 멈춘 뒤 엔진을 멈춘다
        await parts.engine.stop()
        await parts.records.close()  # 엔진을 멈춘 뒤 남은 기록 쓰기를 마친다

    app = FastAPI(
        title="살려드림 백엔드",
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.readiness = Readiness()
    app.state.turn_lock = TurnLock()
    install_error_handlers(app)

    # 인증 없음: 화면은 키트가 만든 전용 Wi-Fi(AP)에서만 접속하고, 접근은 Wi-Fi 비밀번호로 막는다
    api = APIRouter(prefix="/api")
    api.include_router(system.router)
    api.include_router(events.router)
    api.include_router(utterances.router)
    api.include_router(speech.router)
    api.include_router(session.router)
    api.include_router(records.router)
    api.include_router(devices.router)
    api.include_router(manuals.router)
    api.include_router(location.router)
    app.include_router(api)
    # 정적 파일 폴더가 없어도 서버는 뜨고 /static 아래는 404
    if settings.static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")
    return app


app = create_app()
