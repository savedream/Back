"""POST /api/utterances: 응답 모양, 400, 503, 409, 이벤트 (명세서 "[시험] 문장으로 한 턴 처리(내부 API)")."""
import asyncio

import pytest

from tests.scenario.kit import Kit, ids


@pytest.fixture
async def kit():
    async with Kit() as k:
        yield k


async def test_response_shape_matches_spec(kit):
    res = await kit.post("사람이 쓰러졌는데 숨을 안 쉬어", speak=True)
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    data = body["data"]
    assert set(data) == {"turn_id", "normalized", "raw", "function_call", "error_code", "say", "step_id",
                         "latency_ms"}
    assert data["turn_id"] == "t_0001" and data["normalized"] == "사람이 쓰러졌는데 숨을 안 쉬어"
    assert data["raw"].startswith("<maum_0>(situation=1") and data["function_call"]["func"] == 0
    assert data["error_code"] is None and data["step_id"] == "EM01-01"
    assert ids(data["say"]) == [("intro", "EM-01"), ("step", "EM01-01")]
    assert set(data["say"][1]) == {"utterance_id", "role", "text", "screen", "source", "manual_id", "step_id",
                                   "template_id", "speed", "audio"}
    assert set(data["latency_ms"]) == {"normalize", "model", "parse", "engine"}


async def test_parse_error_is_200_with_error_code(kit):
    await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    data = await kit.say("어 그게 아니고")
    assert data["error_code"] == "MODEL_PARSE_ERROR" and data["function_call"] is None and data["raw"] == "<maum_9>"
    assert ids(data["say"]) == [("template", "T_RETRY"), ("step", "EM01-01")]


async def test_missing_text_is_400(kit):
    res = await kit.http.post("/api/utterances", json={"speak": False})
    assert res.status_code == 400 and res.json()["error"]["code"] == "INVALID_REQUEST"


async def test_second_turn_while_first_is_processing_is_409(kit):
    gate = asyncio.Event()
    run = kit.model.run

    async def held(text: str) -> str:
        await gate.wait()
        return await run(text)

    kit.model.run = held
    first = asyncio.create_task(kit.post("등산하다 넘어졌는데 다리를 움직이기 어려워요"))
    while not kit.app.state.turn_lock.busy:
        await asyncio.sleep(0)
    second = await kit.post("사람이 쓰러졌는데 숨을 안 쉬어")
    assert second.status_code == 409
    assert second.json()["error"] == {"code": "AUDIO_BUSY", "message": "다른 턴을 처리 중입니다"}
    gate.set()
    assert (await first).status_code == 200
    assert kit.state.step_id == "EM03-01"


async def test_events_for_a_text_turn(kit):
    mark = len(kit.events)
    await kit.say("등산하다 넘어졌는데 다리를 움직이기 어려워요", speak=True)
    kinds = [(e.type, e.data.get("state")) for e in kit.events[mark:]
             if e.type in ("speech.state", "turn.result", "session.updated", "turn.latency")]
    assert kinds == [("speech.state", "processing"), ("turn.result", None), ("session.updated", None),
                     ("speech.state", "speaking"), ("turn.latency", None), ("speech.state", "armed")]
    latency = next(e for e in kit.events[mark:] if e.type == "turn.latency")
    assert latency.turn_id == "t_0001"
    assert set(latency.data) == {"vad_tail", "stt", "normalize", "model", "parse", "engine", "tts_first", "e2e"}
    assert latency.data["vad_tail"] is None and latency.data["stt"] is None


async def test_turn_without_speech_goes_back_to_listening(kit):
    mark = len(kit.events)
    await kit.say("오늘 날씨 좋네")  # 안내 전 잡담: 말하지 않고 화면 알림만
    states = [e.data["state"] for e in kit.events[mark:] if e.type == "speech.state"]
    assert states == ["processing", "armed"]


def test_not_ready_is_503(client):
    res = client.post("/api/utterances", json={"text": "네"})
    assert res.status_code == 503 and res.json()["error"]["code"] == "NOT_READY"
