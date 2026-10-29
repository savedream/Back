"""공통 응답 형식과 오류 처리 (명세서 "공통 응답", "오류 처리")."""
import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.common.error_code import ErrorCode
from app.common.exceptions import AppError
from app.common.response import ok
from app.config import Settings
from app.core.readiness import Component, require_ready
from app.main import create_app


class _Body(BaseModel):
    scope: int


def _test_router() -> APIRouter:
    r = APIRouter()

    @r.post("/_t/body")
    def body(b: _Body):
        return ok(b.model_dump())

    @r.get("/_t/ready", dependencies=[Depends(require_ready)])
    def needs_ready():
        return ok()

    @r.get("/_t/boom")
    def boom():
        raise RuntimeError("secret internal detail")

    @r.get("/_t/manual")
    def manual():
        raise AppError(ErrorCode.MANUAL_NOT_FOUND, "EM-99 매뉴얼이 없습니다")

    return r


@pytest.fixture
def app():
    a = create_app(Settings(_env_file=None, fake_auto_ready=False))
    a.include_router(_test_router(), prefix="/api")
    return a


@pytest.fixture
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _assert_error(res, status, code):
    assert res.status_code == status
    body = res.json()
    assert body["ok"] is False
    assert set(body) == {"ok", "error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code
    return body["error"]["message"]


def test_validation_error_becomes_400_invalid_request(client):
    """FastAPI 기본 422 → 400 INVALID_REQUEST, 메시지는 "scope: 정수여야 합니다" 모양."""
    res = client.post("/api/_t/body", json={"scope": "abc"})
    msg = _assert_error(res, 400, "INVALID_REQUEST")
    assert msg == "scope: 정수여야 합니다"


def test_missing_field_is_invalid_request(client):
    res = client.post("/api/_t/body", json={})
    msg = _assert_error(res, 400, "INVALID_REQUEST")
    assert msg.startswith("scope: ")


def test_broken_json_is_invalid_request(client):
    res = client.post("/api/_t/body", content=b"{", headers={"content-type": "application/json"})
    _assert_error(res, 400, "INVALID_REQUEST")


def test_not_ready_is_503(client):
    res = client.get("/api/_t/ready")
    msg = _assert_error(res, 503, "NOT_READY")
    assert msg == "준비 중입니다"


def test_ready_passes(app, client):
    for c in Component:
        app.state.readiness.mark_ready(c)
    res = client.get("/api/_t/ready")
    assert res.status_code == 200
    assert res.json() == {"ok": True, "data": None}


def test_unhandled_exception_is_500_internal_error(client):
    """내부 메시지는 응답에 나오지 않고 로그에만 남는다."""
    res = client.get("/api/_t/boom")
    msg = _assert_error(res, 500, "INTERNAL_ERROR")
    assert msg == "잠시 후 다시 시도해 주세요"
    assert "secret" not in res.text


def test_app_error_uses_code_and_status(client):
    res = client.get("/api/_t/manual")
    msg = _assert_error(res, 404, "MANUAL_NOT_FOUND")
    assert msg == "EM-99 매뉴얼이 없습니다"


def test_unknown_path_is_404_invalid_request(client):
    _assert_error(client.get("/api/nope"), 404, "INVALID_REQUEST")


def test_wrong_method_is_405_invalid_request(client):
    _assert_error(client.post("/api/health"), 405, "INVALID_REQUEST")


def test_error_codes_match_spec_table():
    """오류 코드는 명세서 "오류 처리" 표에 있는 것만."""
    assert {c.value for c in ErrorCode} == {
        "STT_EMPTY", "STT_FAILED", "MODEL_TIMEOUT", "MODEL_FAILED", "MODEL_PARSE_ERROR",
        "MODEL_OUT_OF_RANGE", "MIC_UNAVAILABLE", "SPEAKER_UNAVAILABLE", "TTS_FAILED",
        "DEVICE_UNAVAILABLE", "GPS_NO_FIX", "AUDIO_BUSY", "NOT_LISTENING", "NOT_READY",
        "INVALID_REQUEST", "CONFIRM_REQUIRED", "MANUAL_NOT_FOUND", "SESSION_NOT_FOUND",
        "SAVE_FAILED", "INTERNAL_ERROR",
    }


def test_http_status_matches_spec_table():
    """명세서 "오류 처리" 표의 HTTP 열. "—"인 내부 코드는 None."""
    assert {c.value: c.http_status for c in ErrorCode if c.http_status is not None} == {
        "MIC_UNAVAILABLE": 503, "DEVICE_UNAVAILABLE": 503, "AUDIO_BUSY": 409, "NOT_LISTENING": 409,
        "NOT_READY": 503, "INVALID_REQUEST": 400, "CONFIRM_REQUIRED": 400, "MANUAL_NOT_FOUND": 404,
        "SESSION_NOT_FOUND": 404, "SAVE_FAILED": 500, "INTERNAL_ERROR": 500,
    }


def test_default_messages_from_spec_examples():
    """기본 메시지는 명세서 응답 예시 중 늘 같은 문구만."""
    assert {c.value: c.default_message for c in ErrorCode if c.default_message is not None} == {
        "NOT_READY": "준비 중입니다",
        "INTERNAL_ERROR": "잠시 후 다시 시도해 주세요",
        "SESSION_NOT_FOUND": "세션이 없습니다",
        "NOT_LISTENING": "녹음 중이 아닙니다",
        "MIC_UNAVAILABLE": "마이크가 연결되지 않았습니다",
        "CONFIRM_REQUIRED": "confirm: true가 필요합니다",
    }


def test_app_error_uses_default_message():
    e = AppError(ErrorCode.SESSION_NOT_FOUND)
    assert (e.status, e.message) == (404, "세션이 없습니다")


@pytest.mark.parametrize("code", ["MANUAL_NOT_FOUND", "DEVICE_UNAVAILABLE", "SAVE_FAILED", "INVALID_REQUEST", "AUDIO_BUSY"])
def test_app_error_requires_message_without_default(code):
    with pytest.raises(ValueError):
        AppError(ErrorCode[code])
