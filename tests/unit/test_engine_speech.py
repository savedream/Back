"""음성 턴의 엔진 쪽 (명세서 이벤트 스트림 speech.final·음성 턴 순서, 6-4 인식 실패, 계획 PR②).

음성 턴(trigger button·vad)은 사용자 발화 기록을 만든 뒤 알아들은 문장(speech.final {text, normalized, record_id})을 내고,
서비스는 그것을 turn.result보다 먼저 발행한다. 인식 실패(STT_*)는 말하지 않고 ui.notice만 낸다.
"""
import pytest

from app.common.error_code import ErrorCode
from app.services.procedure_engine.inputs import ModelErrorInput
from tests.unit.engine_kit import EngineKit, ids, user_turn
from tests.unit.shell_kit import ShellKit

FALLEN = "등산하다 넘어졌는데 다리를 움직이기 어려워요"


def voice(state, text: str, model, trigger: str = "button"):
    return user_turn(state, text, model, "t_voice").model_copy(update={"trigger": trigger})


def stt_failure(state, code: ErrorCode) -> ModelErrorInput:
    return ModelErrorInput(turn_id="t_voice", epoch=state.epoch, step_id=state.step_id, text="", normalized="",
                           error_code=code, trigger="button")


def finals(events) -> list[dict]:
    return [e.data for e in events if e.type == "speech.final"]


@pytest.mark.parametrize("trigger", ["button", "vad"])
def test_voice_turn_sends_heard_sentence_with_record_id(trigger):
    kit = EngineKit()
    mark = len(kit.events)
    kit.feed(voice(kit.state, "등산하다, 넘어졌는데 다리를 움직이기 어려워요.", kit.model, trigger))
    user = next(r for r in kit.records if r.kind == "user_utterance")
    assert finals(kit.events[mark:]) == [{"text": "등산하다, 넘어졌는데 다리를 움직이기 어려워요.", "normalized": FALLEN,
                                          "record_id": user.record_id}]


def test_text_turn_has_no_speech_final():
    kit = EngineKit()
    kit.say(FALLEN)
    assert finals(kit.events) == []


def test_voice_turn_model_error_still_sends_heard_sentence():
    """알아들었는데 해석에 실패하면(MODEL_*) 알아들은 문장은 내고 T_RETRY로 다시 묻는다."""
    kit = EngineKit()
    kit.say(FALLEN)
    mark, spoken = len(kit.events), len(kit.spoken)
    kit.feed(voice(kit.state, "어 그게 아니고", kit.model))
    assert [f["text"] for f in finals(kit.events[mark:])] == ["어 그게 아니고"]
    assert ids(kit.spoken[spoken:]) == [("template", "T_RETRY"), ("step", "EM03-01")]


@pytest.mark.parametrize("code", [ErrorCode.STT_EMPTY, ErrorCode.STT_FAILED])
def test_stt_failure_only_notices(code):
    """인식 실패(6-4): 말하지 않고 ui.notice만. 무응답 횟수에 넣지 않고 기록·speech.final도 없다. 무응답은 다시 잰다."""
    kit = EngineKit()
    kit.say(FALLEN)
    kit.advance(15)
    kit.advance(15)
    assert kit.state.no_speech.count == 2
    events, spoken, records = len(kit.events), len(kit.spoken), len(kit.records)
    kit.feed(stt_failure(kit.state, code))
    assert kit.spoken[spoken:] == [] and kit.records[records:] == []
    assert [(e.type, e.data.get("code")) for e in kit.events[events:]] == [("turn.result", None),
                                                                           ("ui.notice", code.value)]
    assert kit.state.no_speech.count == 2 and kit.state.no_speech.deadline is not None


@pytest.mark.parametrize("text", [FALLEN, "어 그게 아니고"])
async def test_service_publishes_speech_final_before_turn_result(text):
    """음성 턴 이벤트 순서: speech.final → turn.result → session.updated. 해석 실패는 리듀서가 turn.result를 먼저 모아도 같다."""
    kit = ShellKit()
    await kit.start()
    try:
        mark = len(kit.events)
        await kit.engine.submit(voice(kit.state, text, kit.model))
        await kit.settle()
        types = [e.type for e in kit.events[mark:] if e.type in ("speech.final", "turn.result", "session.updated")]
        assert types[:2] == ["speech.final", "turn.result"]
        assert all(e.turn_id == "t_voice" for e in kit.events[mark:] if e.type == "speech.final")
    finally:
        await kit.stop()
