"""키트 끄기 (명세서 POST /api/system/shutdown, 계획 PR③ AC-D1~D3): 동결, 저장, 소리 기능 끄기, 종료 훅."""
import pytest

from tests.scenario.kit import Kit

SHUTTING_DOWN = {"code": "NOT_READY", "message": "종료 중입니다"}


@pytest.fixture
async def kit():
    async with Kit() as k:
        yield k


async def test_frozen_engine_refuses_ready_apis(kit):
    """동결 중에는 준비가 필요한 API가 503 NOT_READY "종료 중입니다"(계획 기본값 6). 조회 API는 그대로."""
    kit.parts.engine.freeze()
    for path, body in (("/api/utterances", {"text": "네", "speak": False}), ("/api/rescue-signal/on", None),
                       ("/api/speech/start", None)):
        res = await kit.http.post(path, json=body)
        assert res.status_code == 503 and res.json()["error"] == SHUTTING_DOWN, path
    assert (await kit.http.get("/api/session")).status_code == 200
    kit.parts.engine.unfreeze()
    assert (await kit.post("사람이 쓰러졌는데 숨을 안 쉬어")).status_code == 200
