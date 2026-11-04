"""엔진 서비스(셸): 입력 큐, 마감 시각 깨우기, 이벤트 순서, 낭독 끊기, 되먹임 (계획 3-2~3-5, 시나리오 S1·S5·S11·S12·S18).

리듀서 수준 시나리오(test_engine_rules.py)와 같은 결과가 비동기 셸을 거쳐서도 나오는지 본다.
"""
import asyncio

import pytest

from app.services.procedure_engine import service as service_module
from app.services.procedure_engine.actions import EngineResult
from app.schemas.device import DeviceCommand, Location
from app.services.procedure_engine.inputs import (
    BargeInInput, DeviceButtonInput, LocationUpdateInput, SpeechActivityInput,
)
from tests.unit.engine_kit import FIX, ids
from tests.unit.shell_kit import ShellKit, until_idle, until_speaking


RESCUE_ON = DeviceButtonInput(turn_id="t_btn", command=DeviceCommand(object=4, type=1, source="button"))


@pytest.fixture
async def kit():
    k = ShellKit()
    await k.start()
    yield k
    await k.stop()


async def at_s1b(kit: ShellKit) -> None:
    await kit.say("등산하다 넘어졌는데 다리를 움직이기 어려워요")
    await kit.say("아니요 피는 안 나요")


async def at_s5b(kit: ShellKit) -> None:
    await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    await kit.say("아니요 대답 없어요")
    await kit.say("아니요")


def kinds(events) -> list[tuple]:
    out = []
    for e in events:
        if e.type == "guide.say":
            out.append((e.type, e.data["template_id"] or e.data["step_id"] or e.data["manual_id"]))
        elif e.type == "guide.step":
            out.append((e.type, e.data["step_id"]))
        elif e.type == "speech.state":
            out.append((e.type, e.data["state"]))
        else:
            out.append((e.type,))
    return out


async def test_start_welcomes_and_records_guidance(kit):
    assert ids(kit.spoken) == [("template", "T_WELCOME")]
    assert kinds(kit.events) == [("session.updated",), ("guide.say", "T_WELCOME"), ("speech.state", "speaking"),
                                 ("speech.state", "armed")]
    [guidance] = [r for r in kit.records.records if r.kind == "kit_guidance"]
    assert (guidance.utterance.template_id, guidance.played, guidance.saved) == ("T_WELCOME", "done", True)
    assert guidance.record_id == kit.events[1].data["record_id"]


async def test_guidance_record_keeps_turn_id(kit):
    await kit.say("등산하다 넘어졌는데 다리를 움직이기 어려워요")
    said = [e for e in kit.events if e.type == "guide.say"][-1]
    [record] = [r for r in kit.records.records if r.kind == "kit_guidance" and r.record_id == said.data["record_id"]]
    assert record.turn_id == said.turn_id == "t_0001"


async def test_s1_flow_through_shell(kit):
    assert ids(await kit.say("등산하다 넘어졌는데 다리를 움직이기 어려워요")) == [("intro", "EM-03"), ("step", "EM03-01")]
    assert ids(await kit.say("아니요 피는 안 나요")) == [("step", "EM03-04")]
    assert ids(await kit.say("네 많이 휘었어요")) == [("step", "EM03-05"), ("step", "EM03-06"), ("step", "EM03-07")]
    assert kit.state.step_id == "EM03-07"
    played = [r.played for r in kit.records.records if r.kind == "kit_guidance"]
    assert played == ["done"] * 7  # 되먹임(append)은 같은 묶음의 다음 문장을 끊지 않는다


async def test_turn_event_order(kit):
    mark = len(kit.events)
    await kit.say("등산하다 넘어졌는데 다리를 움직이기 어려워요")
    assert kinds(kit.events[mark:]) == [
        ("turn.result",), ("session.updated",), ("guide.say", "EM-03"), ("speech.state", "speaking"),
        ("guide.step", "EM03-01"), ("guide.say", "EM03-01"), ("speech.state", "armed"),
    ]
    updated = kit.events[mark + 1]
    assert updated.data["step_id"] == "EM03-01" and updated.data["active_manual_id"] == "EM-03"
    assert "devices" not in updated.data and updated.turn_id == "t_0001"
    seqs = [e.seq for e in kit.events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


async def test_new_position_cuts_speech_in_progress(kit):
    kit.speaker.hold()
    already = len(kit.speaker.spoken)
    await kit.engine.submit(kit.turn("등산하다 넘어졌는데 다리를 움직이기 어려워요"))
    await until_speaking(kit.speaker, already)  # 시작 안내(intro)를 읽는 중
    await kit.engine.submit(kit.turn("아니요 피는 안 나요"))
    kit.speaker.release()
    await kit.settle()
    played = [(r.utterance.step_id or r.utterance.manual_id, r.played) for r in kit.records.records
              if r.kind == "kit_guidance" and r.utterance.template_id is None]
    assert played == [("EM-03", "cut"), ("EM03-01", "cut"), ("EM03-04", "done")]
    assert ids(kit.spoken)[-2:] == [("intro", "EM-03"), ("step", "EM03-04")]  # EM03-01은 화면에도 내지 않음


async def test_button_during_guidance_does_not_cut_it(kit):
    await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    kit.speaker.hold()
    already = len(kit.speaker.spoken)
    await kit.engine.submit(kit.turn("아니요 대답 없어요"))
    await until_speaking(kit.speaker, already)  # EM01-02를 읽는 중
    button = DeviceButtonInput(turn_id="t_btn", command=DeviceCommand(object=5, type=1, source="button"))
    result = await kit.engine.submit(button)
    assert result.mode == "append"
    kit.speaker.release()
    await kit.settle()
    played = [(r.utterance.template_id or r.utterance.step_id, r.played) for r in kit.records.records
              if r.kind == "kit_guidance"][-6:]
    assert played == [("EM01-02", "done"), ("EM01-03", "done"), ("T_GPS_OK", "done"), ("EM01-05", "done"),
                      ("T_DEV_VOL_UP", "done"), ("EM01-05", "done")]  # 119·좌표 안내를 끝까지 읽고 음량 확인 뒤 다시 묻는다
    assert kit.state.volume == 80


async def test_barge_in_resumes_from_unread_step_and_records_cut(kit):
    await at_s1b(kit)
    kit.speaker.hold()
    already = len(kit.speaker.spoken)
    await kit.engine.submit(kit.turn("네 많이 휘었어요"))
    await until_speaking(kit.speaker, already)  # EM03-05를 읽는 중
    await kit.engine.submit(BargeInInput(turn_id="t_barge"))
    kit.speaker.release()
    await kit.settle()
    assert kit.state.step_id == "EM03-05"
    played = [(r.utterance.step_id, r.played) for r in kit.records.records if r.kind == "kit_guidance"][-3:]
    assert played == [("EM03-05", "cut"), ("EM03-06", "cut"), ("EM03-07", "cut")]
    out = await kit.say("어 그게 아니고")
    assert ids(out) == [("template", "T_RETRY"), ("step", "EM03-05"), ("step", "EM03-06"), ("step", "EM03-07")]
    assert ids(await kit.advance(15)) == [("template", "T_NO_SPEECH"), ("step", "EM03-07")]


async def test_barge_in_on_call_step_does_not_double_return(kit):
    kit.model.add("상황이 잘 모르겠어요",
                  "<maum_0>(situation=-1, conscious=-1, breathing=-1, bleeding=-1, movement=-1, scene_danger=-1)<maum_end>")
    await kit.say("상황이 잘 모르겠어요")
    await kit.say("네")
    await kit.say("아니요")
    assert kit.state.step_id == "EM00-06"
    kit.speaker.hold()
    already = len(kit.speaker.spoken)
    await kit.engine.submit(kit.turn("네"))  # EM00-07(119 안내로 call) → COM-119
    await until_speaking(kit.speaker, already)
    await kit.engine.submit(BargeInInput(turn_id="t_barge"))
    kit.speaker.release()
    await kit.settle()
    assert kit.state.step_id == "EM00-07" and kit.state.call_stack == []
    await kit.say("어 그게 아니고")
    assert len(kit.state.call_stack) == 1  # 다시 읽으며 돌아올 곳을 한 번만 쌓는다


async def test_engine_device_runs_and_result_comes_back(kit):
    await at_s5b(kit)
    assert [(c.object, c.type, c.source) for c in kit.devices.commands] == [(2, 1, "engine")]
    assert kit.state.devices["metronome"].status == "on"
    states = [e.data["status"] for e in kit.events if e.type == "device.state"]
    assert states == ["pending", "on"]


async def test_s11_no_speech_wakes_engine(kit):
    await at_s1b(kit)
    assert await kit.advance(14) == []
    assert ids(await kit.advance(1)) == [("template", "T_NO_SPEECH"), ("step", "EM03-04")]
    assert ids(await kit.advance(15)) == [("template", "T_NO_SPEECH"), ("step", "EM03-04")]
    assert ids(await kit.advance(15)) == [("template", "T_UNKNOWN"), ("step", "EM03-05"), ("step", "EM03-06"),
                                          ("step", "EM03-07")]


async def test_s12_compression_alert_wakes_engine(kit):
    await at_s5b(kit)
    started = [e for e in kit.events if e.type == "alert.started"]
    assert [e.data["step_id"] for e in started] == ["EM01-09"]
    assert await kit.advance(119) == []
    mark = len(kit.events)
    assert ids(await kit.advance(1)) == [("step", "EM01-10"), ("step", "EM01-11")]
    assert [e.data["reason"] for e in kit.events[mark:] if e.type == "alert.ended"] == ["timeout"]


async def test_alert_during_speech_does_not_freeze_engine(kit):
    await at_s5b(kit)
    await kit.engine.submit(SpeechActivityInput(state="started"))
    assert await kit.advance(130) == []  # 엔진이 같은 마감으로 계속 깨어나면 settle이 끝나지 않는다
    assert ids(await kit.say("네")) == [("step", "EM01-10"), ("step", "EM01-11")]


async def test_late_gps_is_not_cut_by_no_speech():
    kit = ShellKit(location=Location(fix=False))  # 위치를 아직 못 잡음(None은 GPS 모듈 없음)
    await kit.start()
    try:
        await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
        assert ("template", "T_GPS_WAIT") in ids(await kit.say("아니요 대답 없어요"))
        await kit.advance(12)
        kit.location = FIX
        kit.speaker.hold()
        already = len(kit.speaker.spoken)
        await kit.engine.submit(LocationUpdateInput(location=FIX))
        await until_speaking(kit.speaker, already)  # 늦게 잡힌 좌표를 읽는 중
        kit.clock.advance(3)  # 질문 낭독 끝에서 15초가 되는 시각
        for _ in range(50):
            await asyncio.sleep(0)
        kit.speaker.release()
        await kit.settle()
        played = [(r.utterance.template_id or r.utterance.step_id, r.played) for r in kit.records.records
                  if r.kind == "kit_guidance"]
        assert played[-1] == ("T_GPS_OK", "done")  # 낭독 중에는 무응답 타이머가 멈춘다
        assert ids(await kit.advance(15)) == [("template", "T_NO_SPEECH"), ("step", "EM01-05")]
    finally:
        await kit.stop()


async def test_speak_false_returns_after_alert_started(kit):
    await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    await kit.say("아니요 대답 없어요")
    spoken_before = len(kit.speaker.spoken)
    result = await kit.engine.submit(kit.turn("아니요"), speak=False)
    assert [a.utterance.step_id for a in result.actions if a.type == "say"] == [f"EM01-0{i}" for i in (6, 7, 8, 9)]
    assert kit.state.alerts[0].deadline is not None  # 낭독 끝 되먹임까지 반영한 뒤 돌아옴
    await kit.settle()
    assert len(kit.speaker.spoken) == spoken_before  # 스피커로는 읽지 않음


async def test_s18_timer_reset_after_record_saved(kit):
    await at_s5b(kit)
    await kit.say("타이머 초기화해 줘")
    result = await kit.engine.submit(kit.turn("네"))
    assert [a for a in result.actions if a.type == "say"] == []  # 응답 직후에는 아직 초기화 전
    assert kit.state.timer.status == "running"
    mark = len(kit.spoken)
    await kit.settle()
    assert ids(kit.spoken[mark:]) == [("template", "T_DEV_TIMER_RESET")]
    assert kit.state.timer.status == "idle" and kit.state.pending_prompt is None


async def test_s18b_save_failure_keeps_timer(kit):
    await at_s5b(kit)
    await kit.say("타이머 초기화해 줘")
    kit.records.fail_next_save("device_result")
    mark = len(kit.events)
    assert ids(await kit.say("네")) == [("template", "T_DEV_TIMER_KEEP")]
    assert kit.state.timer.status == "running"
    assert [e.data["code"] for e in kit.events[mark:] if e.type == "ui.notice"] == ["SAVE_FAILED"]


async def test_failed_input_does_not_stop_engine(kit, monkeypatch):
    real_reduce = service_module.reduce

    def broken(state, inp, ctx):
        if getattr(inp, "normalized", None) == "어 그게 아니고":
            raise RuntimeError("시험용 오류")
        return real_reduce(state, inp, ctx)

    monkeypatch.setattr(service_module, "reduce", broken)
    with pytest.raises(RuntimeError):
        await kit.engine.submit(kit.turn("어 그게 아니고"))
    assert ids(await kit.say("등산하다 넘어졌는데 다리를 움직이기 어려워요")) == [("intro", "EM-03"), ("step", "EM03-01")]


async def test_failed_tick_skips_only_that_deadline(kit, monkeypatch):
    await at_s1b(kit)
    real_reduce = service_module.reduce
    failures = []

    def broken_once(state, inp, ctx):
        if inp.kind == "tick" and not failures:
            failures.append(ctx.now)
            raise RuntimeError("시험용 오류")
        return real_reduce(state, inp, ctx)

    monkeypatch.setattr(service_module, "reduce", broken_once)
    assert await kit.advance(15) == []  # 같은 마감으로 계속 깨어나면 settle이 끝나지 않는다
    assert failures == [15]
    await kit.say("다시 말해 줘")
    assert ids(await kit.advance(15)) == [("template", "T_NO_SPEECH"), ("step", "EM03-04")]  # 다른 마감은 그대로


def fail_once(monkeypatch, kind: str, event: str | None = None) -> list:
    real_reduce = service_module.reduce
    failures = []

    def broken(state, inp, ctx):
        if inp.kind == kind and (event is None or inp.event == event) and not failures:
            failures.append(inp)
            raise RuntimeError("시험용 오류")
        return real_reduce(state, inp, ctx)

    monkeypatch.setattr(service_module, "reduce", broken)
    return failures


async def test_failed_playback_feedback_does_not_stop_timers(kit, monkeypatch):
    failures = fail_once(monkeypatch, "playback", "finished")  # 끝 되먹임(시작 되먹임 실패는 아래 시험)
    await kit.say("등산하다 넘어졌는데 다리를 움직이기 어려워요")
    assert failures and kit.state.pending_says == {}  # 실패한 낭독 끝 되먹임도 읽은 것으로 정리
    assert ids(await kit.advance(15)) == [("template", "T_NO_SPEECH"), ("step", "EM03-01")]


async def test_failed_started_feedback_still_speaks_and_leaves_no_hold(kit, monkeypatch):
    """시작 되먹임 처리가 실패해도 문장은 읽고 kit_guidance를 남기며, 구조신호 멈춤(held_by)이 남지 않는다."""
    await at_s1b(kit)
    await kit.engine.submit(RESCUE_ON)
    await kit.settle()
    failures = fail_once(monkeypatch, "playback", "started")
    mark = len(kit.records.records)
    assert ids(await kit.say("다시 말해 줘")) == [("step", "EM03-04")]
    assert failures
    played = [(r.utterance.step_id, r.played) for r in kit.records.records[mark:] if r.kind == "kit_guidance"]
    assert played == [("EM03-04", "done")]
    rescue = kit.state.devices["rescue_signal"]
    assert kit.state.pending_says == {} and not kit.state.announcing
    assert rescue.held_by is None and kit.devices.held == set()


async def test_hold_gate_closes_once_per_batch(kit):
    """문장 세 개 묶음 동안 구조신호 관문은 한 번 닫고 한 번 연다(문장 사이에 열지 않음). held_by 변화도 두 번."""
    await at_s1b(kit)
    await kit.engine.submit(RESCUE_ON)
    await kit.settle()
    assert kit.devices.holds == [(4, True), (4, False)]  # 켜는 묶음(확인 문장·다시 묻기)에도 멈췄다가 울린다
    events, holds = len(kit.events), len(kit.devices.holds)
    assert ids(await kit.say("네 많이 휘었어요")) == [("step", "EM03-05"), ("step", "EM03-06"), ("step", "EM03-07")]
    changes = [e.data["held_by"] for e in kit.events[events:]
               if e.type == "device.state" and e.data["name"] == "rescue_signal"]
    assert changes == ["tts", None]
    assert kit.devices.holds[holds:] == [(4, True), (4, False)] and kit.devices.held == set()


async def test_failed_turn_after_speech_does_not_freeze_alerts(kit, monkeypatch):
    await at_s5b(kit)
    await kit.engine.submit(SpeechActivityInput(state="started"))
    fail_once(monkeypatch, "function_call")
    with pytest.raises(RuntimeError):
        await kit.engine.submit(kit.turn("네"))
    assert kit.state.speech_active is False  # 그 턴은 끝난 것으로
    assert ids(await kit.advance(120)) == [("step", "EM01-10"), ("step", "EM01-11")]


async def test_failed_event_publish_still_speaks(kit, monkeypatch):
    real = kit.bus.publish

    def broken(type, turn_id, data):
        if type == "session.updated":
            raise RuntimeError("시험용 오류")
        return real(type, turn_id, data)

    monkeypatch.setattr(kit.bus, "publish", broken)
    assert ids(await kit.say("등산하다 넘어졌는데 다리를 움직이기 어려워요")) == [("intro", "EM-03"), ("step", "EM03-01")]


async def test_tick_that_changes_nothing_is_not_repeated(kit, monkeypatch):
    await at_s1b(kit)
    real_reduce = service_module.reduce

    def stuck(state, inp, ctx):
        if inp.kind == "tick":
            return EngineResult(turn_id=None, state=state)
        return real_reduce(state, inp, ctx)

    monkeypatch.setattr(service_module, "reduce", stuck)
    assert await kit.advance(15) == []
    await until_idle(kit.engine, kit.engine.sequencer)


def timer_button(typ: int) -> DeviceButtonInput:
    return DeviceButtonInput(turn_id="t_timer", command=DeviceCommand(object=3, type=typ, source="button"))


async def ticks(kit: ShellKit, seconds: float) -> list[dict]:
    """시계를 넘기고 timer.tick 주기 작업이 돌 만큼 이벤트 루프를 돌린 뒤, 그사이 나온 timer.tick data."""
    mark = len(kit.events)
    kit.clock.advance(seconds)
    for _ in range(50):
        await asyncio.sleep(0)
    await kit.settle()
    return [e.data for e in kit.events[mark:] if e.type == "timer.tick"]


async def running_timer(kit: ShellKit) -> str:
    await kit.engine.submit(timer_button(1))
    await kit.settle()  # 확인 문장이 끝나 시작(AC-E1)
    assert kit.state.timer.status == "running"
    return kit.state.timer.started_at


async def test_timer_tick_every_second_while_running(kit):
    """타이머가 도는 동안 1초마다 timer.tick. data는 화면용 TimerState(엔진 내부 칸 없음)."""
    started_at = await running_timer(kit)
    for n in (1, 2, 3):
        assert await ticks(kit, 1) == [{"object": 3, "status": "running", "elapsed_s": n, "started_at": started_at}]


async def test_late_wake_sends_one_tick(kit):
    """늦게 깨어나도 밀린 tick을 몰아 내지 않는다(구독 큐가 차지 않게)."""
    await running_timer(kit)
    assert [d["elapsed_s"] for d in await ticks(kit, 10)] == [10]
    assert [d["elapsed_s"] for d in await ticks(kit, 1)] == [11]


@pytest.mark.parametrize("typ", [3, 4])
async def test_no_tick_after_pause_or_reset(kit, typ):
    await running_timer(kit)
    assert [d["elapsed_s"] for d in await ticks(kit, 2)] == [2]
    await kit.engine.submit(timer_button(typ))  # 일시정지 또는 초기화(버튼은 확인 창을 거친 것)
    await kit.settle()
    assert kit.state.timer.status == ("paused" if typ == 3 else "idle")
    for _ in range(3):
        assert await ticks(kit, 1) == []


async def test_engine_stop_cancels_ticker():
    kit = ShellKit()
    await kit.start()
    await running_timer(kit)
    await kit.stop()
    assert kit.engine._ticker.cancelled()
