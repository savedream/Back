from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

WEB_ORIGIN = "http://localhost:8081"


def test_preflight_allows_web_origin(client):
    res = client.options("/api/health", headers={
        "Origin": WEB_ORIGIN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,cache-control",
    })
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == WEB_ORIGIN


def test_simple_request_has_allow_origin(client):
    res = client.get("/api/health", headers={"Origin": WEB_ORIGIN})
    assert res.headers["access-control-allow-origin"] == WEB_ORIGIN


def test_unknown_origin_not_allowed(client):
    res = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in res.headers


def test_empty_origins_disables_cors(tmp_path):
    settings = Settings(_env_file=None, fake_auto_ready=False, records_db_path=tmp_path / "records.db", cors_origins=[])
    with TestClient(create_app(settings)) as c:
        res = c.get("/api/health", headers={"Origin": WEB_ORIGIN})
    assert "access-control-allow-origin" not in res.headers
