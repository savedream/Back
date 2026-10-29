"""HTTP 시나리오 (계획 시나리오 표 S1~S18b). /api/utterances 응답의 say·step_id와 상태·기록·이벤트를 본다.

기대값은 매뉴얼 DB v1.7 단계 ID다. 모든 say.text는 DB 원문이거나 템플릿을 채운 결과여야 한다.
"""
import pytest

from tests.scenario.kit import Kit, ids
from tests.unit.engine_kit import assert_from_db


@pytest.fixture
async def kit():
    async with Kit() as k:
        yield k


async def at_s1b(kit: Kit) -> None:
    await kit.say("등산하다 넘어졌는데 다리를 움직이기 어려워요")
    await kit.say("아니요 피는 안 나요")


async def at_s5b(kit: Kit) -> None:
    await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    await kit.say("아니요 대답 없어요")
    await kit.say("아니요")


async def test_s1_fracture(kit):
    s1a = await kit.say("등산하다 넘어졌는데 다리를 움직이기 어려워요")
    assert ids(s1a["say"]) == [("intro", "EM-03"), ("step", "EM03-01")] and s1a["step_id"] == "EM03-01"
    s1b = await kit.say("아니요 피는 안 나요")
    assert ids(s1b["say"]) == [("step", "EM03-04")] and s1b["step_id"] == "EM03-04"
    s1c = await kit.say("네 많이 휘었어요")
    assert ids(s1c["say"]) == [("step", "EM03-05"), ("step", "EM03-06"), ("step", "EM03-07")]
    assert s1c["step_id"] == "EM03-07"


async def test_s2_location_reads_coordinates(kit):
    await at_s1b(kit)
    data = await kit.say("여기 어디야")
    assert ids(data["say"]) == [("template", "T_GPS_OK"), ("step", "EM03-04")] and data["step_id"] == "EM03-04"
    assert data["say"][0]["text"] == "현재 위치는 위도 삼십칠 점 사 공 이 일 팔 공, 경도 백이십칠 점 육 칠 구 사 이 공입니다."


async def test_s3_cardiac_start(kit):
    s3 = await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    assert ids(s3["say"]) == [("intro", "EM-01"), ("step", "EM01-01")] and s3["step_id"] == "EM01-01"


async def test_s4_i1_from_fracture(kit):
    await at_s1b(kit)
    data = await kit.say("불러도 반응이 없고 숨을 안 쉬어요")
    assert ids(data["say"]) == [("intro", "EM-01"), ("step", "EM01-02"), ("step", "EM01-03"),
                                ("template", "T_GPS_OK"), ("step", "EM01-05")]
    assert data["step_id"] == "EM01-05"


async def test_s5_compression_and_recovery(kit):
    await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    s5a = await kit.say("아니요 대답 없어요")
    assert ids(s5a["say"]) == [("step", "EM01-02"), ("step", "EM01-03"), ("template", "T_GPS_OK"),
                               ("step", "EM01-05")]
    s5b = await kit.say("아니요")
    assert ids(s5b["say"]) == [("step", f"EM01-0{i}") for i in (6, 7, 8, 9)] and s5b["step_id"] == "EM01-15"
    assert kit.state.devices["metronome"].status == "on" and kit.parts.devices.on == {2}
    assert kit.state.timer.status == "running" and "cpr" in kit.state.flags
    s5c = await kit.say("숨 쉬어요")
    assert ids(s5c["say"]) == [("step", "EM01-13")] and kit.parts.devices.on == {2} and kit.state.alerts == []
    s5d = await kit.say("네")
    assert ids(s5d["say"]) == [("step", "EM01-16"), ("step", "MON-02")] and s5d["step_id"] == "MON-02"
    assert kit.parts.devices.on == set() and "cpr" not in kit.state.flags


async def test_s6_switch_confirm(kit):
    await at_s1b(kit)
    s6a = await kit.say("팔에 화상을 입었어요")
    assert ids(s6a["say"]) == [("template", "T_SWITCH_ASK")] and s6a["say"][0]["text"] == "화상 안내로 바꿀까요?"
    assert s6a["step_id"] == "EM03-04" and kit.state.pending_prompt.type == "switch_confirm"
    s6b = await kit.say("아니요")
    assert ids(s6b["say"]) == [("step", "EM03-04")] and kit.state.pending_prompt is None


async def test_s6c_switch_confirm_yes(kit):
    await at_s1b(kit)
    await kit.say("팔에 화상을 입었어요")
    data = await kit.say("네")
    assert ids(data["say"]) == [("template", "T_SWITCH"), ("intro", "EM-06"), ("step", "EM06-01"),
                                ("step", "EM06-02")]
    assert data["step_id"] == "EM06-02"


async def test_s7_cardiac_switch_without_confirm(kit):
    await at_s1b(kit)
    data = await kit.say("심장이 멈춘 것 같아요")
    assert ids(data["say"]) == [("template", "T_SWITCH"), ("intro", "EM-01"), ("step", "EM01-01")]


async def test_s8_correction_during_compression(kit):
    await at_s5b(kit)
    data = await kit.say("아까 잘못 말했어 숨 쉬어")
    assert ids(data["say"]) == [("template", "T_CORRECT"), ("step", "MON-05")] and data["step_id"] == "MON-06"
    assert kit.parts.devices.on == set() and "cpr" not in kit.state.flags
    assert [a.step_id for a in kit.state.alerts] == ["MON-07"]


@pytest.mark.parametrize("text, expected, speed", [
    ("다시 말해 줘", [("step", "EM03-04")], 0),
    ("처음부터 다시", [("step", "EM03-04")], 0),
    ("다시", [("template", "T_REPEAT_ASK")], 0),
    ("천천히 다시", [("step", "EM03-04")], 1),
])
async def test_s9_repeat(kit, text, expected, speed):
    await at_s1b(kit)
    epoch = kit.state.epoch
    data = await kit.say(text)
    assert ids(data["say"]) == expected and data["say"][0]["speed"] == speed
    assert data["step_id"] == "EM03-04" and kit.state.epoch == epoch


async def test_s10_parse_failure(kit):
    await at_s1b(kit)
    data = await kit.say("어 그게 아니고")
    assert ids(data["say"]) == [("template", "T_RETRY"), ("step", "EM03-04")]
    assert data["error_code"] == "MODEL_PARSE_ERROR" and data["step_id"] == "EM03-04"


async def test_s11_no_speech_three_times(kit):
    await at_s1b(kit)
    assert ids(await kit.advance(15)) == [("template", "T_NO_SPEECH"), ("step", "EM03-04")]
    assert ids(await kit.advance(15)) == [("template", "T_NO_SPEECH"), ("step", "EM03-04")]
    assert ids(await kit.advance(15)) == [("template", "T_UNKNOWN"), ("step", "EM03-05"), ("step", "EM03-06"),
                                          ("step", "EM03-07")]
    assert kit.state.step_id == "EM03-07"


async def test_s12_compression_alert(kit):
    await at_s5b(kit)
    assert await kit.advance(119) == []
    assert ids(await kit.advance(1)) == [("step", "EM01-10"), ("step", "EM01-11")]
    assert kit.state.step_id == "EM01-11"


async def test_s14_no_switch_during_compression(kit):
    await at_s5b(kit)
    ends = kit.state.alerts[0].deadline
    data = await kit.say("팔에 화상을 입었어요")
    assert ids(data["say"]) == [("template", "T_NOTED")] and data["step_id"] == "EM01-15"
    s = kit.state
    assert s.active_manual_id == "EM-01" and s.pending_prompt is None and kit.parts.devices.on == {2}
    assert s.alerts[0].deadline == ends
    assert any(r.kind == "user_utterance" and r.text == "팔에 화상을 입었어요" for r in kit.records)  # 발화는 기록


async def test_s15_yes_in_wait_is_report(kit):
    await at_s5b(kit)
    data = await kit.say("네")
    assert data["say"] == [] and data["step_id"] == "EM01-15" and len(kit.state.alerts) == 1
    report = next(r for r in reversed(kit.records) if r.kind == "user_report")
    assert (report.basis, report.report, report.step_id) == ("wait_answer", "네", "EM01-15")


async def test_s16_progress_phrase_skips_model(kit):
    await at_s5b(kit)
    calls = kit.model.calls
    data = await kit.say("다음")
    assert ids(data["say"]) == [("template", "T_WAITING")] and data["step_id"] == "EM01-15"
    assert data["raw"] is None and data["function_call"] is None and kit.model.calls == calls
    assert len(kit.state.alerts) == 1


async def test_s17_chit_chat_before_guidance(kit):
    mark = len(kit.events)
    data = await kit.say("오늘 날씨 좋네")
    assert data["say"] == [] and data["step_id"] is None and kit.state.mode == "idle"
    notices = [e.data for e in kit.events[mark:] if e.type == "ui.notice"]
    assert notices == [{"code": "OUT_OF_SCOPE", "template_id": "T_OOS_1",
                        "text": kit.parts.engine.db.templates_by_id["T_OOS_1"]["tts"]}]


async def test_s18_voice_timer_reset(kit):
    await at_s5b(kit)
    ask = await kit.say("타이머 초기화해 줘")
    assert ids(ask["say"]) == [("template", "T_TIMER_RESET_ASK")] and ask["step_id"] == "EM01-15"
    assert kit.state.pending_prompt.type == "timer_reset" and kit.state.timer.status == "running"
    mark = len(kit.spoken)
    data = await kit.say("네", settle=False)
    assert data["say"] == []  # 초기화는 기록 저장 성공 되먹임 뒤라 응답에는 아직 없다
    await kit.settle()
    assert ids(kit.spoken_since(mark)) == [("template", "T_DEV_TIMER_RESET")]
    assert kit.state.timer.status == "idle" and kit.state.timer.accumulated_s == 0
    assert kit.state.pending_prompt is None and kit.state.step_id == "EM01-15"
    saved = [r for r in kit.records if r.kind == "device_result" and r.object == 3 and r.type == 4]
    assert len(saved) == 1 and saved[0].saved is True


async def test_s18b_timer_reset_save_failure(kit):
    await at_s5b(kit)
    await kit.say("타이머 초기화해 줘")
    kit.parts.records.fail_next_save("device_result")
    mark, events = len(kit.spoken), len(kit.events)
    await kit.say("네")
    assert ids(kit.spoken_since(mark)) == [("template", "T_DEV_TIMER_KEEP")]
    assert kit.state.timer.status == "running" and kit.state.pending_prompt is None
    assert [e.data["code"] for e in kit.events[events:] if e.type == "ui.notice"] == ["SAVE_FAILED"]


async def test_every_spoken_sentence_comes_from_db(kit):
    await at_s5b(kit)
    await kit.say("여기 어디야")
    await kit.say("타이머 초기화해 줘")
    await kit.say("아니요")
    await kit.advance(120)
    await kit.say("어 그게 아니고")
    assert len(kit.spoken) > 10
    for u in kit.spoken:
        assert_from_db(u)


async def test_device_timeout_shows_unknown(kit):
    await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    await kit.say("아니요 대답 없어요")
    kit.parts.devices.fail_next("timeout")
    await kit.say("아니요", settle=False)  # 메트로놈 켜기 명령에 장치가 답하지 않음
    await kit.advance(2)
    metronome = kit.state.devices["metronome"]
    assert (metronome.status, metronome.last_result) == ("unknown", "timeout")  # 성공으로 표시하지 않는다
