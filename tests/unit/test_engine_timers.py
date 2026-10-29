"""무응답·대기·확인 질문 마감 다시 걸기 (명세서 6-7·6-8·6-9, dispatch '무응답'·'대기'). 어떤 입력 뒤에도 멈춰 서지 않는지 본다."""
import pytest

from app.common.error_code import ErrorCode
from app.services.procedure_engine.inputs import ModelErrorInput, TickInput
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


def test_which_on_timeout_asks_original_question_again(kit):
    at_s1b(kit)
    kit.model.add("켜 줘", "<maum_4>(object=-1, type=1)<maum_end>")
    assert ids(kit.say("켜 줘")) == [("template", "T_WHICH_ON")]
    assert ids(kit.advance(15)) == [("step", "EM03-04")]
    assert kit.state.pending_prompt is None


def at_s5b(kit: EngineKit) -> EngineKit:
    kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    kit.say("아니요 대답 없어요")
    kit.say("아니요")
    return kit


def test_alert_move_cancels_pending_timer_reset(kit):
    at_s5b(kit)
    kit.advance(110)
    assert ids(kit.say("타이머 초기화해 줘")) == [("template", "T_TIMER_RESET_ASK")]
    assert ids(kit.advance(10)) == [("step", "EM01-10"), ("step", "EM01-11")]
    assert kit.state.pending_prompt is None and kit.state.timer.status == "running"
    out = ids(kit.say("네"))  # 자동심장충격기 질문의 답으로 쓴다
    assert out[0] == ("step", "AED-01") and ("template", "T_DEV_TIMER_RESET") not in out  # EM01-12 → COM-AED
    assert kit.state.timer.status == "running"


def test_alert_move_keeps_listening_after_prompt_cancel(kit):
    at_s5b(kit)
    kit.advance(110)
    kit.say("타이머 초기화해 줘")
    kit.advance(10)
    assert ids(kit.advance(15)) == [("template", "T_NO_SPEECH"), ("step", "EM01-11")]


def test_barge_in_continues_from_unread_step_after_user_turn(kit):
    at_s1b(kit)
    kit.hold()
    kit.say("네 많이 휘었어요")
    kit.barge_in()
    assert kit.state.step_id == "EM03-05"
    mark = len(kit.records)
    kit.release("cut")  # 시퀀서가 끊긴 문장을 cut으로 되먹임
    cut = [(r.utterance.step_id, r.played) for r in kit.records[mark:] if r.kind == "kit_guidance"]
    assert cut == [("EM03-05", "cut"), ("EM03-06", "cut"), ("EM03-07", "cut")]
    assert ids(kit.say("어 그게 아니고")) == [("template", "T_RETRY"), ("step", "EM03-05"), ("step", "EM03-06"),
                                             ("step", "EM03-07")]


def test_one_move_per_tick_when_two_alerts_end_together(kit):
    at_s5b(kit)
    first = kit.state.alerts[0]
    kit.state.alerts.append(first.model_copy(update={"alert_id": "a_99", "step_id": "MON-07", "on_timeout": "MON-02"}))
    kit.clock.advance(120)
    kit.feed(TickInput())
    assert kit.state.step_id == "EM01-11" and [a.alert_id for a in kit.state.alerts] == ["a_99"]
    kit.feed(TickInput())
    assert kit.state.step_id == "MON-02" and kit.state.alerts == []


def test_barge_in_right_after_new_position_ignores_dropped_guidance(kit):
    at_s1b(kit)
    kit.hold()
    kit.say("네 많이 휘었어요")  # EM03-05~07 낭독 대기
    kit.say("불러도 반응이 없고 숨을 안 쉬어요")  # I1: 심정지 묶음으로 바뀜. 앞 묶음의 cut 되먹임은 아직 안 옴
    kit.barge_in()
    assert kit.state.step_id == "EM01-02" and kit.state.active_manual_id == "EM-01"  # 버려진 골절 안내로 돌아가지 않음
