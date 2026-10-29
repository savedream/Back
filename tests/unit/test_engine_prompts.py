"""확인 질문과 기기 요청·버튼이 겹칠 때 (명세서 6-7, 6-10)."""
import pytest

from app.schemas.device import DeviceCommand
from app.services.procedure_engine.inputs import (
    DeviceButtonInput, DeviceResultInput, LocationButtonInput, RepeatButtonInput,
)
from tests.unit.engine_kit import EngineKit, ids


@pytest.fixture
def kit() -> EngineKit:
    k = EngineKit()
    k.model.add("켜 줘", "<maum_4>(object=-1, type=1)<maum_end>")
    k.model.add("타이머 시작해 줘", "<maum_4>(object=3, type=1)<maum_end>")
    k.model.add("메트로놈 켜 줘", "<maum_4>(object=2, type=1)<maum_end>")
    k.model.add("메트로놈 꺼 줘", "<maum_4>(object=2, type=2)<maum_end>")
    k.say("등산하다 넘어졌는데 다리를 움직이기 어려워요")
    k.say("아니요 피는 안 나요")
    return k


def button(kit: EngineKit, obj: int, typ: int) -> list:
    start = len(kit.spoken)
    kit.feed(DeviceButtonInput(turn_id="t_btn", command=DeviceCommand(object=obj, type=typ, source="button")))
    return ids(kit.spoken[start:])


def test_button_does_what_it_says_during_which_on(kit):
    assert ids(kit.say("켜 줘")) == [("template", "T_WHICH_ON")]
    assert button(kit, 2, 2) == [("template", "T_DEV_ALREADY_OFF"), ("step", "EM03-04")]  # 끄기 버튼은 끄기
    assert kit.devices == [] and kit.state.pending_prompt is None


def test_voice_answers_which_on(kit):
    kit.model.add("메트로놈", "<maum_4>(object=2, type=2)<maum_end>")
    kit.say("켜 줘")
    assert ids(kit.say("메트로놈")) == [("template", "T_DEV_METRO_ON"), ("step", "EM03-04")]  # 음성은 그 확인의 답


def test_button_keeps_switch_confirm_and_asks_it_again(kit):
    assert ids(kit.say("팔에 화상을 입었어요")) == [("template", "T_SWITCH_ASK")]
    assert button(kit, 5, 1) == [("template", "T_DEV_VOL_UP"), ("template", "T_SWITCH_ASK")]
    assert kit.state.pending_prompt.type == "switch_confirm"
    assert ids(kit.say("네"))[:2] == [("template", "T_SWITCH"), ("intro", "EM-06")]


def test_device_confirm_takes_over_switch_confirm(kit):
    kit.say("타이머 시작해 줘")
    kit.say("팔에 화상을 입었어요")
    assert ids(kit.say("타이머 초기화해 줘")) == [("template", "T_TIMER_RESET_ASK")]  # 전환 확인을 겹쳐 묻지 않는다
    assert kit.state.pending_prompt.type == "timer_reset"
    assert ids(kit.say("네")) == [("template", "T_DEV_TIMER_RESET"), ("step", "EM03-04")]
    assert kit.state.timer.status == "idle" and kit.state.active_manual_id == "EM-03"


def test_button_timer_reset_keeps_switch_confirm(kit):
    kit.say("타이머 시작해 줘")
    kit.say("팔에 화상을 입었어요")
    assert button(kit, 3, 4) == [("template", "T_DEV_TIMER_RESET"), ("template", "T_SWITCH_ASK")]
    assert kit.state.timer.status == "idle" and kit.state.pending_prompt.type == "switch_confirm"


def test_failed_device_can_be_requested_again(kit):
    kit.device_status = "timeout"
    assert ids(kit.say("메트로놈 켜 줘"))[0] == ("template", "T_DEV_METRO_ON")
    assert kit.state.devices["metronome"].status == "unknown"
    kit.device_status = "success"
    assert ids(kit.say("메트로놈 켜 줘"))[0] == ("template", "T_DEV_METRO_ON")  # '이미 켜져 있어요'가 아니라 다시 켠다
    assert len(kit.devices) == 2 and kit.state.devices["metronome"].status == "on"


def test_late_on_response_does_not_show_on_after_off_request(kit):
    kit.device_status = None
    kit.say("메트로놈 켜 줘")
    kit.say("메트로놈 꺼 줘")
    on, off = kit.device_actions
    kit.feed(DeviceResultInput(action_id=on.action_id, object=2, status="success"))
    assert kit.state.devices["metronome"].status == "pending"  # 끄기 응답을 기다리는 중
    kit.feed(DeviceResultInput(action_id=off.action_id, object=2, status="success"))
    assert kit.state.devices["metronome"].status == "off"


def at_compression_recovery_check() -> EngineKit:
    k = EngineKit()
    k.say("사람이 쓰러졌는데 숨을 안 쉬어")
    k.say("아니요 대답 없어요")
    k.say("아니요")
    k.say("숨 쉬어요")  # 압박 중 회복 신호 → EM01-13
    assert k.state.step_id == "EM01-13"
    return k


@pytest.mark.parametrize("text", ["다시 말해 줘", "처음부터 다시", "다시"])
def test_repeat_during_timer_reset_reads_prompt_again(text):
    k = at_compression_recovery_check()
    k.say("타이머 초기화해 줘")
    assert ids(k.say(text)) == [("template", "T_TIMER_RESET_ASK")]
    assert k.state.pending_prompt.type == "timer_reset"
    out = ids(k.say("네"))  # 확인 질문의 답: 압박을 멈추지 않고 타이머만 초기화한 뒤 원래 질문을 다시 묻는다
    assert out == [("template", "T_DEV_TIMER_RESET"), ("step", "EM01-13")]
    assert k.state.step_id == "EM01-13" and "cpr" in k.state.flags and k.state.timer.status == "idle"


def test_repeat_button_during_switch_confirm_reads_it_once(kit):
    kit.say("팔에 화상을 입었어요")
    start = len(kit.spoken)
    kit.feed(RepeatButtonInput(turn_id="t_btn"))
    assert ids(kit.spoken[start:]) == [("template", "T_SWITCH_ASK")]  # 원래 질문이 아니라 확인 질문을 한 번
    assert ids(kit.say("아니요")) == [("step", "EM03-04")]


def test_slow_repeat_of_prompt(kit):
    kit.say("팔에 화상을 입었어요")
    out = kit.say("천천히 다시")
    assert ids(out) == [("template", "T_SWITCH_ASK")] and out[0].speed == 1


def location_button(kit: EngineKit) -> list:
    start = len(kit.spoken)
    kit.feed(LocationButtonInput(turn_id="t_btn"))
    return ids(kit.spoken[start:])


def test_location_button_keeps_switch_confirm(kit):
    kit.say("팔에 화상을 입었어요")
    assert location_button(kit) == [("template", "T_OOS_2"), ("template", "T_GPS_OK"), ("template", "T_SWITCH_ASK")]
    assert kit.state.pending_prompt.type == "switch_confirm"


def test_location_button_cancels_timer_reset(kit):
    kit.say("타이머 시작해 줘")
    kit.say("타이머 초기화해 줘")
    assert location_button(kit) == [("template", "T_OOS_2"), ("template", "T_GPS_OK"), ("step", "EM03-04")]
    assert kit.state.pending_prompt is None and kit.state.timer.status == "running"


def test_progress_phrase_cancels_timer_reset_in_wait():
    k = at_compression_recovery_check()
    k.say("아니요")  # 아직 회복 안 됨 → 압박 계속(EM01-15 대기)
    assert k.state.step_id == "EM01-15"
    k.say("타이머 초기화해 줘")
    mark = len(k.events)
    assert ids(k.say("다음")) == [("template", "T_WAITING")]
    assert k.state.pending_prompt is None and k.state.timer.status == "running"
    results = [e.data for e in k.events[mark:] if e.type == "turn.result"]
    assert results == [{"function_call": None, "error_code": None}]  # 진행 표현 턴도 해석 결과를 알린다
