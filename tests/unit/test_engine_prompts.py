"""확인 질문과 기기 요청·버튼이 겹칠 때 (명세서 6-7, 6-10)."""
import pytest

from app.schemas.device import DeviceCommand
from app.services.procedure_engine.inputs import DeviceButtonInput
from tests.unit.engine_kit import EngineKit, ids


@pytest.fixture
def kit() -> EngineKit:
    k = EngineKit()
    k.model.add("켜 줘", "<maum_4>(object=-1, type=1)<maum_end>")
    k.model.add("타이머 시작해 줘", "<maum_4>(object=3, type=1)<maum_end>")
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
