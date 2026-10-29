"""세션 API (명세서 "세션"): GET /api/session."""
import pytest

from tests.scenario.kit import Kit
from tests.unit.engine_kit import DB

SPEC_KEYS = ["seq", "session_id", "previous_session_id", "started_at", "mode", "active_manual_id", "step_id",
             "spoken_step_id", "current_prompt", "pending_prompt", "observation", "flags", "alerts", "devices",
             "timer", "mic", "conversation"]
DEBUG_KEYS = ["routing_no", "call_stack", "answer_log", "no_speech", "last_utterance_id", "last_off_device", "epoch"]


@pytest.fixture
async def kit():
    async with Kit() as k:
        yield k


async def at_s5b(kit: Kit) -> None:
    """가슴압박 대기(EM01-15): 메트로놈 켜짐, 타이머 돎, 2분 알림."""
    await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    await kit.say("아니요 대답 없어요")
    await kit.say("아니요")


async def get_session(kit: Kit, **params) -> dict:
    res = await kit.http.get("/api/session", params=params)
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True
    return res.json()["data"]


async def test_session_shape_matches_spec(kit):
    await at_s5b(kit)
    await kit.advance(30)
    data = await get_session(kit)
    s = kit.state
    assert list(data) == SPEC_KEYS  # 엔진 내부 칸은 debug일 때만
    assert data["seq"] == kit.parts.events.seq
    assert (data["session_id"], data["previous_session_id"], data["started_at"]) == (s.session_id, None, s.started_at)
    assert (data["mode"], data["active_manual_id"], data["step_id"]) == ("guiding", s.active_manual_id, "EM01-15")
    assert data["spoken_step_id"] == "EM01-09" and data["flags"] == s.flags and "cpr" in data["flags"]
    assert data["current_prompt"] is None and data["pending_prompt"] is None  # 대기 중이면 질문 없음
    assert data["observation"]["breathing"]["allowed"] == s.observation["breathing"].allowed
    alert = s.alerts[0]
    assert data["alerts"] == [{"alert_id": alert.alert_id, "step_id": "EM01-09", "seconds": 120,
                               "ends_at": "2026-10-13T14:02:00+09:00", "on_timeout": "EM01-10"}]  # 남은 90초
    assert data["devices"]["metronome"] == s.devices["metronome"].model_dump()
    assert data["devices"]["metronome"]["status"] == "on" and data["devices"]["rescue_signal"]["status"] == "off"
    assert data["timer"] == {"object": 3, "status": "running", "elapsed_s": 30, "started_at": s.timer.started_at}
    assert data["mic"] == "armed"


async def test_debug_adds_engine_fields(kit):
    await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    data = await get_session(kit, debug="true")
    s = kit.state
    assert list(data) == SPEC_KEYS + DEBUG_KEYS
    assert data["routing_no"] == s.routing_no and data["call_stack"] == [] and data["epoch"] == s.epoch
    assert data["answer_log"] == [e.model_dump() for e in s.answer_log]
    assert data["no_speech"] == {"count": 0, "running": True}  # 질문을 다 읽어 무응답을 잰다
    assert data["last_utterance_id"] == s.last_utterance.utterance_id and data["last_off_device"] is None
    assert list(await get_session(kit, debug="false")) == SPEC_KEYS


async def test_current_prompt_is_question_of_ask_step(kit):
    await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    step = DB.steps_by_id[kit.state.step_id]
    assert step["kind"] == "ask"
    question = kit.spoken[-1]
    data = await get_session(kit)
    assert data["current_prompt"] == question.model_dump()
    assert data["current_prompt"]["text"] == step["tts"] and data["current_prompt"]["role"] == "question"
    # 질문 뒤에 다른 문장을 말했어도 말한 기록에서 그 질문을 찾는다
    kit.parts.engine.state = kit.state.model_copy(update={"last_utterance": None})
    assert (await get_session(kit))["current_prompt"] == question.model_dump()


async def test_current_prompt_is_confirm_question(kit):
    await kit.say("등산하다 넘어졌는데 다리를 움직이기 어려워요")
    await kit.say("아니요 피는 안 나요")
    await kit.say("팔에 화상을 입었어요")
    data = await get_session(kit)
    prompt = data["current_prompt"]
    assert (prompt["source"], prompt["template_id"], prompt["text"]) == ("template", "T_SWITCH_ASK",
                                                                         "화상 안내로 바꿀까요?")
    assert prompt["role"] == "question" and data["step_id"] == "EM03-04"
    pending = kit.state.pending_prompt
    assert data["pending_prompt"] == {"type": "switch_confirm", "template_id": "T_SWITCH_ASK",
                                      "origin_step_id": "EM03-04", "target_manual_id": pending.target_manual_id,
                                      "deferred_branch": pending.deferred_branch.model_dump(),
                                      "expires_at": "2026-10-13T14:00:15+09:00"}  # 다 읽은 뒤 15초


async def test_conversation_is_recent_dialogue(kit):
    await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    data = await get_session(kit)
    records = [r for r in kit.records if r.kind in ("user_utterance", "kit_guidance")]
    assert [c["record_id"] for c in data["conversation"]] == [r.record_id for r in records]
    first_user = next(c for c in data["conversation"] if c["kind"] == "user_utterance")
    assert {k: first_user[k] for k in ("text", "screen", "step_id")} == {
        "text": "사람이 쓰러졌는데 숨을 안 쉬어", "screen": None, "step_id": None}
    guide = data["conversation"][-1]
    rec = records[-1]
    assert guide == {"record_id": rec.record_id, "kind": "kit_guidance", "text": rec.utterance.text,
                     "screen": rec.utterance.screen, "step_id": rec.utterance.step_id, "at": rec.at}
    for _ in range(30):
        await kit.say("다시 말해 줘")
    conversation = (await get_session(kit))["conversation"]
    records = [r for r in kit.records if r.kind in ("user_utterance", "kit_guidance")]
    assert len(records) > 50 and [c["record_id"] for c in conversation] == [r.record_id for r in records[-50:]]


def test_before_ready_returns_empty_session(client):
    res = client.get("/api/session")
    assert res.status_code == 200
    data = res.json()["data"]
    assert list(data) == SPEC_KEYS
    assert all(data[k] is None for k in ("session_id", "previous_session_id", "started_at", "active_manual_id",
                                         "step_id", "spoken_step_id", "current_prompt", "pending_prompt"))
    assert data["mode"] == "idle" and data["mic"] == "blocked" and data["conversation"] == []
    assert data["flags"] == [] and data["alerts"] == [] and isinstance(data["seq"], int)
    assert data["timer"] == {"object": 3, "status": "idle", "elapsed_s": 0, "started_at": None}
    assert data["devices"]["metronome"]["status"] == "off"
