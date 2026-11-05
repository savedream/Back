"""기기 규칙 후속 (명세서 ⑦ 소리 충돌, 내부 모듈 계약 6-10, 계획 PR①): 안내·녹음 중 소리 기능 잠시 멈춤(held_by, 결정 A3),
읽던 안내 뒤로 미룬 켜기(queued)와 대기 중 끄기 취소(결정 B1).

리듀서는 낭독 시작·끝 되먹임(started·finished)과 녹음 사실(capturing)로 held_by를 정한다. 장치 음소거 관문은
셸 시험(test_engine_service.py, tests/api/test_device_buttons.py)에서 본다.
"""
import pytest

from app.schemas.device import DeviceCommand
from app.schemas.session import SessionState
from app.services.procedure_engine.core import new_session, recover, resume_session
from app.services.procedure_engine.inputs import DeviceButtonInput, PlaybackInput
from app.services.procedure_engine.timers import hold_reason
from tests.unit.engine_kit import EngineKit, ids


@pytest.fixture
def kit() -> EngineKit:
    k = EngineKit()
    k.model.add("구조 신호 켜 줘", "<maum_4>(object=4, type=1)<maum_end>")
    k.model.add("메트로놈 켜 줘", "<maum_4>(object=2, type=1)<maum_end>")
    k.model.add("구조 신호 꺼 줘", "<maum_4>(object=4, type=2)<maum_end>")
    return k


def button(obj: int, typ: int) -> DeviceButtonInput:
    return DeviceButtonInput(turn_id="t_btn", command=DeviceCommand(object=obj, type=typ, source="button"))


def at_s1b(kit: EngineKit) -> EngineKit:
    kit.say("등산하다 넘어졌는데 다리를 움직이기 어려워요")
    kit.say("아니요 피는 안 나요")
    return kit


def at_s5b(kit: EngineKit) -> EngineKit:
    kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    kit.say("아니요 대답 없어요")
    kit.say("아니요")
    return kit


def held_by(kit: EngineKit, name: str = "rescue_signal") -> str | None:
    return kit.state.devices[name].held_by


def test_rescue_signal_held_while_reasking_in_same_batch(kit):
    """구조신호를 켠 묶음의 다시 묻기 질문 동안에도 멈춘다. 장치 응답 전이어도 켜기로 요청했으면(want) 멈춘다."""
    at_s1b(kit)
    kit.hold()
    kit.device_status = None  # 장치 응답은 아직 오지 않음
    assert ids(kit.say("구조 신호 켜 줘")) == [("template", "T_DEV_SOS_ON"), ("step", "EM03-04")]
    assert held_by(kit) is None  # 소리가 나기 전
    kit.started()
    assert held_by(kit) == "tts" and kit.state.devices["rescue_signal"].status == "pending"
    kit.finish()
    assert held_by(kit) == "tts"  # 문장 사이에 풀리지 않는다
    kit.started()  # 다시 묻는 질문
    assert held_by(kit) == "tts"
    kit.finish()
    assert held_by(kit) is None and not kit.state.announcing


def test_hold_changes_twice_per_three_sentence_batch(kit):
    """문장 세 개 묶음: 첫 소리에 tts, 마지막 문장이 끝나야 None. held_by 변화는 두 번이다."""
    at_s1b(kit)
    kit.say("구조 신호 켜 줘")
    assert kit.state.devices["rescue_signal"].status == "on"
    kit.hold()
    assert ids(kit.say("네 많이 휘었어요")) == [("step", "EM03-05"), ("step", "EM03-06"), ("step", "EM03-07")]
    mark = len(kit.events)
    for _ in range(3):
        kit.started()
        kit.finish()
    changes = [e.data["held_by"] for e in kit.events[mark:]
               if e.type == "device.state" and e.data["name"] == "rescue_signal"]
    assert changes == ["tts", None]


@pytest.mark.parametrize("cpr, expected", [(True, None), (False, "tts")])
def test_metronome_held_only_outside_compression(kit, cpr, expected):
    """압박 구간(cpr)에는 안내 중에도 메트로놈을 멈추지 않고, 그 밖에서는 안내 동안 멈춘다."""
    if cpr:
        at_s5b(kit)  # EM01-15: 메트로놈 켜짐, cpr
        text = "다음"
    else:
        at_s1b(kit)
        kit.say("메트로놈 켜 줘")
        text = "다시 말해 줘"
    assert kit.state.devices["metronome"].want == "on" and ("cpr" in kit.state.flags) == cpr
    kit.hold()
    kit.say(text)
    kit.started()
    assert held_by(kit, "metronome") == expected


def test_recording_comes_before_tts():
    """구조신호는 말하기 버튼 녹음 중이면 recording(안내 중이어도). 녹음은 메트로놈을 멈추지 않는다."""
    s = SessionState(session_id="s_1", started_at="t")
    s.devices["rescue_signal"].want = s.devices["metronome"].want = "on"
    s.capturing = True
    assert hold_reason(s, "rescue_signal") == "recording" and hold_reason(s, "metronome") is None
    s.announcing = True
    assert hold_reason(s, "rescue_signal") == "recording" and hold_reason(s, "metronome") == "tts"
    s.capturing = False
    assert hold_reason(s, "rescue_signal") == "tts"
    s.devices["rescue_signal"].want = "off"
    assert hold_reason(s, "rescue_signal") is None  # 끄기로 요청했으면 멈춤 표시도 없다


def test_dropped_say_does_not_start_announcing(kit):
    at_s1b(kit)
    kit.say("구조 신호 켜 줘")
    kit.hold()
    kit.say("네 많이 휘었어요")
    kit.say("불러도 반응이 없고 숨을 안 쉬어요")  # I1: 골절 안내는 버려짐(cut 되먹임은 아직)
    assert kit.state.pending_says[kit.held[0].action_id].dropped
    kit.started()  # 버려진 문장의 시작 되먹임
    assert not kit.state.announcing and held_by(kit) is None


def test_recover_after_failed_started_keeps_say(kit):
    """시작 되먹임 처리가 실패하면 시작 표시만 되돌리고 문장은 남긴다. 끝 되먹임에서 kit_guidance가 남고 멈춤도 풀린다."""
    at_s1b(kit)
    kit.say("구조 신호 켜 줘")
    kit.hold()
    kit.say("다시 말해 줘")
    kit.started()
    action = kit.held[0]
    assert kit.state.pending_says[action.action_id].started and held_by(kit) == "tts"
    kit.state = recover(kit.state, PlaybackInput(action_id=action.action_id, event="started"), kit.ctx()).state
    assert action.action_id in kit.state.pending_says and not kit.state.pending_says[action.action_id].started
    mark = len(kit.records)
    kit.finish()
    assert [(r.kind, r.record_id) for r in kit.records[mark:]] == [("kit_guidance", action.record_id)]
    assert held_by(kit) is None and not kit.state.announcing


def test_new_session_and_resume_drop_queued_and_hold(kit):
    """새 세션은 녹음 중 표시를 이어받고 안내 중 표시를 끈다. 이어가기는 둘 다 끈다. 둘 다 queued·held_by를 지운다."""
    s = kit.state.model_copy(deep=True)
    rescue = s.devices["rescue_signal"]
    rescue.want, rescue.status, rescue.held_by, rescue.queued = "on", "on", "tts", {"type": 1, "source": "button"}
    s.announcing = s.capturing = True
    fresh = new_session(kit.ctx(), previous=s).state
    assert fresh.capturing and not fresh.announcing
    assert (fresh.devices["rescue_signal"].queued, fresh.devices["rescue_signal"].held_by) == (None, None)
    resumed = resume_session(s, kit.ctx(), saved_now=kit.clock.now(), downtime_s=0).state
    assert not resumed.capturing and not resumed.announcing
    assert resumed.devices["rescue_signal"].want == "on"  # 켜져 있던 구조신호는 다시 켠다
    assert (resumed.devices["rescue_signal"].queued, resumed.devices["rescue_signal"].held_by) == (None, None)


@pytest.mark.parametrize("name, source", [("rescue_signal", "voice"), ("metronome", "button")])
def test_sound_on_while_reading_is_queued_until_its_confirm_ends(kit, name, source):
    """안내를 읽는 중에 켜기 요청이 오면 queued {type, source}. 그 확인 문장 낭독이 끝나면(장치 명령 직전) None."""
    at_s1b(kit)
    kit.hold()
    kit.device_status = None  # 시퀀서처럼 확인 문장이 끝나기 전에는 장치 응답이 오지 않게
    kit.say("다시 말해 줘")  # EM03-04를 읽는 중
    obj = kit.state.devices[name].object
    if source == "voice":
        kit.say("구조 신호 켜 줘")
    else:
        kit.feed(button(obj, 1))
    assert kit.state.devices[name].queued == {"type": 1, "source": source}
    kit.finish()  # 읽던 질문
    assert kit.state.devices[name].queued is not None
    mark = len(kit.events)
    kit.finish()  # 켜기 확인 문장
    assert kit.state.devices[name].queued is None
    states = [e.data for e in kit.events[mark:] if e.type == "device.state" and e.data["name"] == name]
    assert [d["queued"] for d in states] == [None]


def test_replace_does_not_queue_on_dropped_guidance(kit):
    """확인 질문을 취소해 위치가 바뀌면(replace) 읽던 확인 질문은 버려지므로 그것을 근거로 queued를 채우지 않는다."""
    at_s5b(kit)
    kit.hold()
    kit.say("타이머 초기화해 줘")  # T_TIMER_RESET_ASK를 읽는 중
    res = kit.feed(button(4, 1))
    assert res.mode == "replace" and not res.queued
    assert kit.state.devices["rescue_signal"].queued is None


@pytest.mark.parametrize("obj, typ", [(3, 1), (5, 1), (4, 2), (2, 1)])
def test_result_queued_for_any_request_while_reading(kit, obj, typ):
    """EngineResult.queued는 요청 종류(타이머·음량·끄기 포함)와 상관없이 읽던 안내가 남았는지로 정한다."""
    at_s1b(kit)
    assert not kit.feed(button(obj, typ)).queued
    kit.hold()
    kit.say("다시 말해 줘")
    assert kit.feed(button(obj, typ)).queued


def test_off_while_queued_cancels_the_on_request(kit):
    """대기 중인 켜기에 끄기가 오면 응답 대기에서 빼고 요청 전 값으로 되돌린다. 기록은 cancelled, 끄기 명령은 없다."""
    at_s1b(kit)
    kit.hold()
    kit.device_status = None
    kit.say("다시 말해 줘")
    before, last_off = kit.state.devices["rescue_signal"].model_copy(), kit.state.last_off_device
    kit.say("구조 신호 켜 줘")
    [on] = [a for a in kit.device_actions if a.command.object == 4]
    assert on.action_id in kit.state.pending_devices
    on_turn = kit.state.pending_devices[on.action_id]["turn_id"]
    mark = len(kit.records)
    assert ids(kit.say("구조 신호 꺼 줘")) == [("template", "T_DEV_SOS_OFF"), ("step", "EM03-04")]
    rescue = kit.state.devices["rescue_signal"]
    assert on.action_id not in kit.state.pending_devices
    assert (rescue.want, rescue.status, rescue.queued) == (before.want, before.status, None)
    assert kit.state.last_off_device == last_off
    assert [(r.object, r.type, r.source, r.result) for r in kit.records[mark:] if r.kind == "device_result"] == [
        (4, 1, "voice", "cancelled")]
    assert [a.action_id for a in kit.device_actions if a.command.object == 4] == [on.action_id]  # 끄기 명령 없음
    [cancelled] = [r for r in kit.records[mark:] if r.kind == "device_result"]
    [off_said] = [r for r in kit.records[mark:] if r.kind == "user_utterance"]
    assert cancelled.turn_id == on_turn != off_said.turn_id  # 끄기 턴이 아니라 원래 켜기 요청의 턴


def test_volume_status_changed_only_when_volume_changes(kit):
    """음량 status.changed는 엔진이 낸다(AC-E5). 끝값(100)에서 그대로면 내지 않는다."""
    kit.model.add("소리 키워 줘", "<maum_4>(object=5, type=1)<maum_end>")
    at_s1b(kit)
    for _ in range(4):
        kit.say("소리 키워 줘")
    assert kit.state.volume == 100
    changed = [e.data for e in kit.events if e.type == "status.changed"]
    assert changed == [{"speaker": {"volume": v}} for v in (80, 90, 100)]
