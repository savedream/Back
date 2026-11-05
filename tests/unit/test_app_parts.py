"""부품 조립과 준비: 가짜 모드는 켜자마자 준비 완료이고 엔진이 새 세션을 열어 T_WELCOME을 읽는다. 세션·기록은 SQLite에 남는다."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.readiness import Component
from app.fakes.fake_model import FakeModel
from app.main import create_app
from app.services.procedure_engine.clock import FakeClock
from app.services.procedure_engine.inputs import SessionResetInput
from tests.scenario.kit import Kit


def wait_until(check, timeout: float = 2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return
        time.sleep(0.01)
    raise AssertionError("기다려도 되지 않음")


def test_fake_mode_is_ready_and_welcomes():
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        assert client.get("/api/health").json()["data"]["ready"] is True
        parts = app.state.parts
        wait_until(lambda: parts.speaker.spoken)
        assert parts.speaker.spoken[0].template_id == "T_WELCOME"
        assert parts.engine.state.session_id.startswith("s_")


def test_engine_waits_until_ready(app, client):
    assert client.get("/api/health").json()["data"]["ready"] is False
    assert app.state.parts.engine.state is None and app.state.parts.speaker.spoken == []


def test_real_parts_are_not_available_yet():
    with pytest.raises(RuntimeError, match="fake_devices=false"):
        with TestClient(create_app(Settings(_env_file=None, fake_devices=False))):
            pass


def test_real_model_needs_model_dir():
    with pytest.raises(RuntimeError, match="SALYEO_MODEL_DIR"):
        with TestClient(create_app(Settings(_env_file=None, fake_model=False))):
            pass


def test_real_model_needs_torch(monkeypatch, tmp_path):
    """torch·transformers 없이(.[dev]만 설치) 실제 모델을 고르면 설치 방법을 알려 준다."""
    monkeypatch.setattr("app.core.parts.HfModelRunner", None)
    with pytest.raises(RuntimeError, match=r"\.\[model\]"):
        with TestClient(create_app(Settings(_env_file=None, fake_model=False, model_dir=tmp_path))):
            pass


class LoadingModel(FakeModel):
    """적재를 시험에서 풀어 주는 실제 모델 자리(torch 없이 서버 기동 순서만 본다)."""

    def __init__(self, fail: bool = False):
        super().__init__()
        self.loaded = asyncio.Event()
        self.fail = fail
        self.warmed = False
        self.closed = False

    async def load(self) -> None:
        await self.loaded.wait()
        if self.fail:
            raise OSError("모델 파일을 읽지 못함")

    async def warm_up(self) -> None:
        self.warmed = True

    async def close(self) -> None:
        self.closed = True


def real_model_app(monkeypatch, tmp_path, model: LoadingModel):
    monkeypatch.setattr("app.core.parts.HfModelRunner", lambda model_dir: model)
    return create_app(Settings(_env_file=None, fake_model=False, model_dir=tmp_path), clock=FakeClock())


async def test_real_model_loads_after_start_then_engine_welcomes(monkeypatch, tmp_path):
    """실제 모델은 서버를 켠 뒤 백그라운드로 적재·예열하고(그동안 준비 전), 끝나야 준비 완료와 새 세션(명세 ④ 예열)."""
    model = LoadingModel()
    app = real_model_app(monkeypatch, tmp_path, model)
    async with app.router.lifespan_context(app):
        readiness, parts = app.state.readiness, app.state.parts
        assert not readiness.ready and parts.engine.state is None
        assert not readiness.is_ready(Component.LLM)
        model.loaded.set()
        await app.state.model_loading
        assert model.warmed and readiness.ready
        assert parts.engine.state.session_id.startswith("s_")
    assert model.closed  # 끌 때 모델을 내려놓는다


async def test_ready_turns_on_only_after_engine_started(monkeypatch, tmp_path):
    """엔진 시작이 양보하는 동안(스냅샷 이어가기 등) 준비 완료로 보이면 요청이 엔진 없이 들어와 500이 된다."""
    model = LoadingModel()
    app = real_model_app(monkeypatch, tmp_path, model)
    async with app.router.lifespan_context(app):
        readiness, parts = app.state.readiness, app.state.parts
        seen, start = [], parts.start_engine

        async def start_engine():
            seen.append(readiness.ready)
            await start()

        parts.start_engine = start_engine
        model.loaded.set()
        await app.state.model_loading
        assert seen == [False] and readiness.ready


async def test_engine_start_failure_stays_not_ready(monkeypatch, tmp_path):
    """적재 뒤 엔진 시작이 실패해도 준비 전(503)으로 남고, 서버를 끌 때 기록 저장 마무리를 건너뛰지 않는다."""
    model = LoadingModel()
    app = real_model_app(monkeypatch, tmp_path, model)
    async with app.router.lifespan_context(app):
        parts = app.state.parts

        async def broken():
            raise RuntimeError("엔진 시작 실패")

        parts.start_engine = broken
        model.loaded.set()
        await app.state.model_loading
        assert not app.state.readiness.ready
    assert model.closed


async def test_real_model_load_failure_stays_not_ready(monkeypatch, tmp_path):
    model = LoadingModel(fail=True)
    app = real_model_app(monkeypatch, tmp_path, model)
    async with app.router.lifespan_context(app):
        model.loaded.set()
        await app.state.model_loading
        assert not app.state.readiness.ready and not model.warmed
        assert app.state.parts.engine.state is None


async def test_shutdown_while_model_loading(monkeypatch, tmp_path):
    """적재가 끝나기 전에 서버를 꺼도 멈추지 않는다."""
    model = LoadingModel()
    app = real_model_app(monkeypatch, tmp_path, model)
    async with app.router.lifespan_context(app):
        loading = app.state.model_loading
    assert loading.cancelled() and model.closed


async def test_new_sessions_and_records_are_saved_to_sqlite():
    async with Kit() as kit:
        first = kit.state.session_id
        await kit.advance(60)  # 세션 ID는 분 단위라 1분 뒤에 새 세션을 연다
        await kit.parts.engine.submit(SessionResetInput(turn_id="t_reset"), speak=False)
        await kit.settle()
        records = kit.parts.records
        sessions = await records.run(lambda c: c.execute("SELECT session_id, previous_session_id FROM sessions "
                                                         "ORDER BY rowid").fetchall())
        assert sessions == [(first, None), (kit.state.session_id, first)]
        saved = await records.run(lambda c: c.execute("SELECT record_id FROM records ORDER BY rowid").fetchall())
        assert [r for (r,) in saved] == [r.record_id for r in kit.records]
        assert records.unsaved() == []
