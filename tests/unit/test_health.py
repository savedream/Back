"""GET /api/health (명세서 "GET /health — 준비 상태")."""
from app.core.readiness import Component


def test_health_shape_matches_spec(client):
    """명세서 예시: {"ok": true, "data": {"ready", "version", "manual_db", "model"}}."""
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert set(body["data"]) == {"ready", "version", "manual_db", "model"}
    assert body["data"]["version"] == "1.3.0"
    assert body["data"]["manual_db"] == "v1.6"
    assert body["data"]["model"] == "v2.2"


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
