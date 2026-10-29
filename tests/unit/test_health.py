"""GET /api/health (명세서 "GET /health — 준비 상태")."""
import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.core.readiness import Component
from app.main import create_app


def test_health_shape_matches_spec(client, settings):
    """명세서 예시: {"ok": true, "data": {"ready", "version", "manual_db", "model"}}."""
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert set(body["data"]) == {"ready", "version", "manual_db", "model"}
    assert body["data"]["version"] == "1.3.0"
    db = json.loads(settings.manual_db_path.read_text(encoding="utf-8"))
    assert body["data"]["manual_db"] == db["meta"]["version"].split()[0]
    assert body["data"]["model"] == "v2.2"


def test_health_manual_db_version_comes_from_loaded_db(tmp_path, settings):
    """manual_db는 설정값이 아니라 적재한 DB의 meta.version 첫 토큰이다."""
    db = json.loads(settings.manual_db_path.read_text(encoding="utf-8"))
    db["meta"]["version"] = "v9.9 (시험용)"
    path = tmp_path / "manual_db.json"
    path.write_text(json.dumps(db, ensure_ascii=False), encoding="utf-8")
    with TestClient(create_app(Settings(_env_file=None, manual_db_path=path))) as c:
        assert c.get("/api/health").json()["data"]["manual_db"] == "v9.9"


def test_health_not_ready_before_loading(client):
    """적재·예열 전에는 ready: false, 그래도 200으로 응답한다."""
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["data"]["ready"] is False


def test_health_ready_when_all_components_done(app, client):
    """매뉴얼 DB·VAD·STT·TTS·LLM 적재와 LLM 예열이 모두 끝나야 ready: true."""
    readiness = app.state.readiness
    components = list(Component)
    for c in components[:-1]:
        readiness.mark_ready(c)
    assert client.get("/api/health").json()["data"]["ready"] is False
    readiness.mark_ready(components[-1])
    assert client.get("/api/health").json()["data"]["ready"] is True
