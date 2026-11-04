"""부품 조립과 준비: 가짜 모드는 켜자마자 준비 완료이고 엔진이 새 세션을 열어 T_WELCOME을 읽는다."""
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


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
    with pytest.raises(RuntimeError, match="fake_model=false"):
        with TestClient(create_app(Settings(_env_file=None, fake_model=False))):
            pass
