import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    # 준비 전 동작(503 NOT_READY 등)을 시험할 수 있게 기본은 자동 준비를 끈다. 시나리오 시험만 켠다
    return Settings(_env_file=None, fake_auto_ready=False)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    # raise_server_exceptions=False: 500 처리기가 만든 응답을 그대로 받기 위해
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
