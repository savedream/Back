"""절차 엔진 규칙: 리듀서 수준 시나리오 (계획 시나리오 표 S1~S18b, 명세서 6-3~6-12).

기대값은 매뉴얼 DB v1.7 단계 ID다. 가짜 모델 표(app/fakes/fake_model_table.json)가 문장 → 모델 출력을 정한다.
"""
import pytest

from tests.unit.engine_kit import NO_FIX_LAST, EngineKit, assert_from_db, ids


@pytest.fixture
def kit() -> EngineKit:
    return EngineKit()


def at_s1b(kit: EngineKit) -> EngineKit:
    kit.say("등산하다 넘어졌는데 다리를 움직이기 어려워요")
    kit.say("아니요 피는 안 나요")
    return kit


def at_s5b(kit: EngineKit) -> EngineKit:
    kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    kit.say("아니요 대답 없어요")
    kit.say("아니요")
    return kit


def test_welcome_on_new_session(kit):
    assert ids(kit.spoken) == [("template", "T_WELCOME")]


def test_s1_fracture_flow(kit):
    assert ids(kit.say("등산하다 넘어졌는데 다리를 움직이기 어려워요")) == [("intro", "EM-03"), ("step", "EM03-01")]
    assert ids(kit.say("아니요 피는 안 나요")) == [("step", "EM03-04")]
    assert ids(kit.say("네 많이 휘었어요")) == [("step", "EM03-05"), ("step", "EM03-06"), ("step", "EM03-07")]
    assert kit.state.step_id == "EM03-07"


def test_s2_location_then_same_question(kit):
    at_s1b(kit)
    out = kit.say("여기 어디야")
    assert ids(out) == [("template", "T_GPS_OK"), ("step", "EM03-04")]
    assert out[0].text == "현재 위치는 위도 삼십칠 점 사 공 이 일 팔 공, 경도 백이십칠 점 육 칠 구 사 이 공입니다."
    assert kit.state.step_id == "EM03-04"


def test_s2_last_known_location():
    kit = at_s1b(EngineKit(location=NO_FIX_LAST))
    assert ids(kit.say("여기 어디야"))[0] == ("template", "T_GPS_LAST")


def test_s3_cardiac_start(kit):
    assert ids(kit.say("사람이 쓰러졌는데 숨을 안 쉬어")) == [("intro", "EM-01"), ("step", "EM01-01")]


def test_s4_i1_from_fracture(kit):
    at_s1b(kit)
    out = kit.say("불러도 반응이 없고 숨을 안 쉬어요")
    assert ids(out) == [("intro", "EM-01"), ("step", "EM01-02"), ("step", "EM01-03"), ("template", "T_GPS_OK"),
                        ("step", "EM01-05")]
    assert kit.state.active_manual_id == "EM-01" and "call119" in kit.state.flags


def test_old_unconscious_values_do_not_restart_cardiac(kit):
    at_s5b(kit)
    kit.advance(120)
    kit.say("아니요")  # 자동심장충격기 아직 → 회복 확인(EM01-13)
    kit.say("네")  # 회복 → 압박 중지(EM01-16), MON-02
    assert kit.state.step_id == "MON-02"
    out = ids(kit.say("네"))  # 대답해요: 예전 의식 0·호흡 0만으로는 심정지(I1)를 다시 걸지 않는다
    assert out[0] == ("step", "MON-04") and ("intro", "EM-01") not in out


def test_i1_skipped_when_situation_report_branches_into_cardiac(kit):
    kit.model.add("상황이 잘 모르겠어요",
                  "<maum_0>(situation=-1, conscious=-1, breathing=-1, bleeding=-1, movement=-1, scene_danger=-1)<maum_end>")
    kit.model.add("숨을 전혀 안 쉬는 것 같아요",
                  "<maum_0>(situation=-1, conscious=-1, breathing=0, bleeding=-1, movement=-1, scene_danger=-1)<maum_end>")
    kit.say("상황이 잘 모르겠어요")
    kit.say("아니요")  # 대답하지 않음 → 의식 [0]
    assert kit.state.step_id == "EM00-04"
    # 호흡 질문의 '아니요' 분기 목적지(EM01-05)가 심정지 매뉴얼이라 I1을 걸지 않고 그 분기로 간다(확인 응답과 같은 결과)
    assert ids(kit.say("숨을 전혀 안 쉬는 것 같아요")) == [("step", "EM01-05")]


def test_s5_compression_and_i2_recovery(kit):
    kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    assert ids(kit.say("아니요 대답 없어요")) == [("step", "EM01-02"), ("step", "EM01-03"), ("template", "T_GPS_OK"),
                                               ("step", "EM01-05")]
    assert ids(kit.say("아니요")) == [("step", f"EM01-0{i}") for i in (6, 7, 8, 9)]
    s = kit.state
    assert s.step_id == "EM01-15" and "cpr" in s.flags and s.timer.status == "running"
    assert s.devices["metronome"].status == "on"
    assert [a.step_id for a in s.alerts] == ["EM01-09"] and s.alerts[0].deadline is not None
    assert ids(kit.say("숨 쉬어요")) == [("step", "EM01-13")]
    assert kit.state.devices["metronome"].status == "on" and kit.state.alerts == []
    assert ids(kit.say("네")) == [("step", "EM01-16"), ("step", "MON-02")]
    assert kit.state.devices["metronome"].status == "off" and "cpr" not in kit.state.flags


def test_s6_switch_confirm(kit):
    at_s1b(kit)
    out = kit.say("팔에 화상을 입었어요")
    assert ids(out) == [("template", "T_SWITCH_ASK")] and out[0].text == "화상 안내로 바꿀까요?"
    assert kit.state.pending_prompt.type == "switch_confirm" and kit.state.step_id == "EM03-04"
    assert ids(kit.say("아니요")) == [("step", "EM03-04")]
    assert kit.state.pending_prompt is None


def test_s6_switch_confirm_yes(kit):
    at_s1b(kit)
    kit.say("팔에 화상을 입었어요")
    assert ids(kit.say("네")) == [("template", "T_SWITCH"), ("intro", "EM-06"), ("step", "EM06-01"),
                                 ("step", "EM06-02")]


def test_s7_cardiac_switch_without_confirm(kit):
    at_s1b(kit)
    assert ids(kit.say("심장이 멈춘 것 같아요")) == [("template", "T_SWITCH"), ("intro", "EM-01"), ("step", "EM01-01")]


def test_s8_correction_during_compression(kit):
    at_s5b(kit)
    assert ids(kit.say("아까 잘못 말했어 숨 쉬어")) == [("template", "T_CORRECT"), ("step", "MON-05")]
    s = kit.state
    assert s.step_id == "MON-06" and s.devices["metronome"].status == "off" and "cpr" not in s.flags
    assert [a.step_id for a in s.alerts] == ["MON-07"]  # EM01-09의 120초 알림은 취소, MON-07의 180초 알림


@pytest.mark.parametrize("text, expected, speed", [
    ("다시 말해 줘", [("step", "EM03-04")], 0),
    ("처음부터 다시", [("step", "EM03-04")], 0),
    ("다시", [("template", "T_REPEAT_ASK")], 0),
    ("천천히 다시", [("step", "EM03-04")], 1),
])
def test_s9_repeat(kit, text, expected, speed):
    at_s1b(kit)
    epoch = kit.state.epoch
    out = kit.say(text)
    assert ids(out) == expected and out[0].speed == speed
    assert kit.state.step_id == "EM03-04" and kit.state.epoch == epoch


def test_s10_parse_failure_reasks(kit):
    at_s1b(kit)
    assert ids(kit.say("어 그게 아니고")) == [("template", "T_RETRY"), ("step", "EM03-04")]


def test_s11_no_speech_three_times(kit):
    at_s1b(kit)
    assert ids(kit.advance(15)) == [("template", "T_NO_SPEECH"), ("step", "EM03-04")]
    assert ids(kit.advance(15)) == [("template", "T_NO_SPEECH"), ("step", "EM03-04")]
    assert ids(kit.advance(15)) == [("template", "T_UNKNOWN"), ("step", "EM03-05"), ("step", "EM03-06"),
                                    ("step", "EM03-07")]


def test_s12_compression_alert(kit):
    at_s5b(kit)
    assert kit.advance(119) == []
    assert ids(kit.advance(1)) == [("step", "EM01-10"), ("step", "EM01-11")]


def test_alert_waits_until_speech_turn_ends(kit):
    at_s5b(kit)
    kit.speech("started")
    assert kit.advance(130) == []  # 말소리를 받는 중에는 알림을 그 턴이 끝난 뒤로 미룬다
    kit.say("네")
    assert ids(kit.advance(0)) == [("step", "EM01-10"), ("step", "EM01-11")]


def test_s14_no_switch_during_compression(kit):
    at_s5b(kit)
    ends = kit.state.alerts[0].deadline
    assert ids(kit.say("팔에 화상을 입었어요")) == [("template", "T_NOTED")]
    s = kit.state
    assert s.step_id == "EM01-15" and s.active_manual_id == "EM-01" and s.pending_prompt is None
    assert s.devices["metronome"].status == "on" and s.alerts[0].deadline == ends


def test_s15_yes_in_wait_is_report(kit):
    at_s5b(kit)
    assert kit.say("네") == []
    assert kit.state.step_id == "EM01-15"
    report = kit.records[-1]
    assert (report.kind, report.basis, report.report, report.step_id) == ("user_report", "wait_answer", "네", "EM01-15")


def test_s16_progress_phrase_in_wait(kit):
    at_s5b(kit)
    calls = kit.model.calls
    assert ids(kit.say("다음")) == [("template", "T_WAITING")]
    assert kit.model.calls == calls and kit.state.step_id == "EM01-15" and len(kit.state.alerts) == 1


def test_s17_chit_chat_before_guidance(kit):
    before = len(kit.events)
    assert kit.say("오늘 날씨 좋네") == []
    notices = [e for e in kit.events[before:] if e.type == "ui.notice"]
    assert [n.data["template_id"] for n in notices] == ["T_OOS_1"] and kit.state.mode == "idle"


def test_s18_voice_timer_reset(kit):
    at_s5b(kit)
    assert ids(kit.say("타이머 초기화해 줘")) == [("template", "T_TIMER_RESET_ASK")]
    assert kit.state.pending_prompt.type == "timer_reset" and kit.state.timer.status == "running"
    assert ids(kit.say("네")) == [("template", "T_DEV_TIMER_RESET")]
    assert kit.state.timer.status == "idle" and kit.state.pending_prompt is None


def test_s18b_timer_reset_save_failure_keeps_timer(kit):
    at_s5b(kit)
    kit.say("타이머 초기화해 줘")
    kit.save_ok = False
    before = len(kit.events)
    assert ids(kit.say("네")) == [("template", "T_DEV_TIMER_KEEP")]
    assert kit.state.timer.status == "running"
    assert any(e.type == "ui.notice" and e.data["code"] == "SAVE_FAILED" for e in kit.events[before:])


def test_kit_guidance_record_keeps_say_record_id(kit):
    at_s1b(kit)
    guidance = {r.record_id: r for r in kit.records if r.kind == "kit_guidance"}
    assert [guidance[a.record_id].utterance for a in kit.say_actions] == kit.spoken


def test_all_spoken_text_comes_from_db(kit):
    at_s5b(kit)
    kit.say("여기 어디야")
    kit.say("타이머 초기화해 줘")
    kit.say("아니요")
    kit.advance(120)
    for u in kit.spoken:
        assert_from_db(u)
