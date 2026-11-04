import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture(autouse=True)
def _records_db_in_tmp(tmp_path, monkeypatch):
    # Settings를 직접 만드는 시험도 기록 DB를 저장소 폴더가 아닌 tmp에 둔다
    monkeypatch.setenv("SALYEO_RECORDS_DB_PATH", str(tmp_path / "records.db"))
    # 스냅샷 이어가기는 그 시험에서만 켠다(Linux에서는 boot_id 파일이 있어 같은 DB를 다시 열면 이어 갈 수 있다)
    monkeypatch.setenv("SALYEO_RESUME_FROM_SNAPSHOT", "false")


@pytest.fixture
def settings(tmp_path) -> Settings:
    # 준비 전 동작(503 NOT_READY 등)을 시험할 수 있게 기본은 자동 준비를 끈다. 시나리오 시험만 켠다
    return Settings(_env_file=None, fake_auto_ready=False, records_db_path=tmp_path / "records.db")


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    # raise_server_exceptions=False: 500 처리기가 만든 응답을 그대로 받기 위해
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
