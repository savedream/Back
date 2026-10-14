import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    # raise_server_exceptions=False: 500 처리기가 만든 응답을 그대로 받기 위해
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
