"""설정값: 기본값과 환경변수 덮어쓰기 (명세서 "공통 규칙", "미정 사항" 임시값)."""
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_defaults():
    s = Settings(_env_file=None)
    assert s.host == "127.0.0.1"
    assert s.port == 8000
    assert s.vad_silence_s == 0.8
    assert s.max_utterance_s == 15
    assert s.model_timeout_s == 5
    assert s.post_tts_guard_ms == 300
    assert s.device_timeout_s == 2
    assert s.prompt_prefix_im_start is False
    assert s.manual_db_path.name == "manual_db.json"
    assert s.manual_db_path.exists()
    assert all([s.fake_model, s.fake_devices, s.fake_audio, s.fake_gps, s.fake_auto_ready])
    assert s.enable_docs is False


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("SALYEO_HOST", "0.0.0.0")
    monkeypatch.setenv("SALYEO_PORT", "9000")
    monkeypatch.setenv("SALYEO_VAD_SILENCE_S", "1.2")
    monkeypatch.setenv("SALYEO_FAKE_MODEL", "false")
    monkeypatch.setenv("SALYEO_MANUAL_DB_PATH", "/tmp/x.json")
    s = Settings(_env_file=None)
    assert s.host == "0.0.0.0"
    assert s.port == 9000
    assert s.vad_silence_s == 1.2
    assert s.fake_model is False
    assert s.manual_db_path == Path("/tmp/x.json")


def test_docs_off_by_default_openapi_on():
    """기본 Swagger는 CDN 스크립트를 쓰므로 기본으로 끈다(CLAUDE.md 꼭 지킬 것 5)."""
    with TestClient(create_app(Settings(_env_file=None))) as c:
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 200


def test_docs_on_when_enabled():
    with TestClient(create_app(Settings(_env_file=None, enable_docs=True))) as c:
        assert c.get("/docs").status_code == 200
