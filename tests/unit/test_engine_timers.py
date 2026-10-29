"""무응답·대기·확인 질문 마감 다시 걸기 (명세서 6-7·6-8·6-9, dispatch '무응답'·'대기'). 어떤 입력 뒤에도 멈춰 서지 않는지 본다."""
import pytest

from app.common.error_code import ErrorCode
from app.services.procedure_engine.inputs import ModelErrorInput
from tests.unit.engine_kit import EngineKit, ids

NO_SPEECH_AGAIN = [("template", "T_NO_SPEECH"), ("step", "EM03-04")]


@pytest.fixture
def kit() -> EngineKit:
    k = EngineKit()
    k.model.add("상황이 잘 모르겠어요",
                "<maum_0>(situation=-1, conscious=-1, breathing=-1, bleeding=-1, movement=-1, scene_danger=-1)<maum_end>")
    return k


def at_s1b(kit: EngineKit) -> EngineKit:
    kit.say("등산하다 넘어졌는데 다리를 움직이기 어려워요")
    kit.say("아니요 피는 안 나요")
    return kit


def test_noted_during_compression_question_keeps_listening(kit):
    kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    kit.say("아니요 대답 없어요")
    kit.say("아니요")
    assert ids(kit.advance(120))[-1] == ("step", "EM01-11")
    assert ids(kit.say("팔에 화상을 입었어요")) == [("template", "T_NOTED")]
    assert ids(kit.advance(15)) == [("template", "T_NO_SPEECH"), ("step", "EM01-11")]


def test_repeat_ask_keeps_listening(kit):
    at_s1b(kit)
    assert ids(kit.say("다시")) == [("template", "T_REPEAT_ASK")]
    assert ids(kit.advance(15)) == NO_SPEECH_AGAIN


def test_speech_pauses_until_turn_arrives(kit):
    at_s1b(kit)
    kit.speech("started")
    assert kit.advance(20) == []
    kit.speech("ended")
    assert kit.advance(20) == []  # 끝났어도 턴 처리가 끝날 때까지는 멈춘 채로
    assert ids(kit.say("어 그게 아니고")) == [("template", "T_RETRY"), ("step", "EM03-04")]
    assert ids(kit.advance(15)) == NO_SPEECH_AGAIN


def test_cancelled_speech_restarts_no_speech(kit):
    at_s1b(kit)
    kit.speech("started")
    assert kit.advance(20) == []
    kit.speech("cancelled")
    assert kit.advance(14) == []
    assert ids(kit.advance(1)) == NO_SPEECH_AGAIN


def test_stt_empty_keeps_listening(kit):
    at_s1b(kit)
    kit.speech("started")
    kit.speech("ended")
    s = kit.state
    kit.feed(ModelErrorInput(turn_id="t_x", epoch=s.epoch, step_id=s.step_id, text="", normalized="",
                             error_code=ErrorCode.STT_EMPTY))
    assert ids(kit.advance(15)) == NO_SPEECH_AGAIN


def test_wait_timer_restarts_after_voice_turn(kit):
    kit.say("상황이 잘 모르겠어요")
    kit.say("네")
    kit.say("아니요")
    kit.say("아니요")
    assert kit.state.step_id == "EM00-09"
    kit.speech("started")
    assert ids(kit.say("여기 어디야")) == [("template", "T_GPS_OK")]
    nxt = kit.db_step("EM00-09")["next"]
    assert ids(kit.advance(30))[0] == ("step", nxt)


def test_leaving_question_for_wait_clears_no_speech(kit):
    kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    kit.say("아니요 대답 없어요")
    assert kit.state.no_speech.deadline is not None
    kit.say("아니요")
    assert kit.state.step_id == "EM01-15" and kit.state.no_speech.deadline is None
