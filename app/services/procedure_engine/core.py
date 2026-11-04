"""절차 엔진 리듀서: reduce(state, input, ctx) -> EngineResult (명세서 ⑥, 계획 3장).

순수 함수다. 상태를 바꾸지 않고 사본으로 새 상태를 만들며, 시계·장치·네트워크를 직접 쓰지 않는다.
"""
from app.schemas.session import SessionState
from app.services.procedure_engine import functions, timers
from app.services.procedure_engine.actions import EngineResult
from app.services.procedure_engine.context import Ctx, Run
from app.services.procedure_engine.devices_rules import device_result, request_device
from app.services.procedure_engine.flow import cancel_alerts, continue_from_say, goto, set_sound_device, start_alert
from app.services.procedure_engine.inputs import EngineInput
from app.services.procedure_engine.interrupts import clear_prompt

# 처리한 뒤 say 단계에 서 있으면 그 단계부터 이어 가는 입력(말하기 버튼으로 되돌아온 경우, 6-2)
_CONTINUE_AFTER = {"function_call", "model_error", "progress_phrase", "device_button", "repeat_button",
                   "location_button", "tick", "record_saved"}


def reduce(state: SessionState, inp: EngineInput, ctx: Ctx) -> EngineResult:
    run = Run(state, ctx, getattr(inp, "turn_id", None))
    kind = inp.kind
    if kind == "function_call":
        functions.handle_function_call(run, inp)
    elif kind == "model_error":
        functions.handle_model_error(run, inp)
    elif kind == "progress_phrase":
        functions.handle_progress_phrase(run, inp)
    elif kind == "device_button":
        request_device(run, inp.command.object, inp.command.type, "button")
    elif kind == "repeat_button":
        functions.repeat_request(run, inp.scope, inp.speed)
    elif kind == "location_button":
        functions.handle_location_button(run)
    elif kind == "playback":
        timers.handle_playback(run, inp.action_id, inp.event, inp.result)
    elif kind == "device_result":
        device_result(run, inp.action_id, inp.object, inp.status, inp.detail)
    elif kind == "record_saved":
        timers.handle_record_saved(run, inp.action_id, inp.ok)
    elif kind == "location_update":
        timers.handle_location(run)
    elif kind == "speech_activity":
        timers.handle_speech(run, inp.state)
    elif kind == "capture":
        run.s.capturing = inp.state == "started"  # held_by는 끝에서 sync_holds가 맞춘다
    elif kind == "mic":
        timers.handle_mic(run, inp.connected, inp.paused)
    elif kind == "tick":
        timers.handle_tick(run)
    elif kind == "barge_in":
        _barge_in(run)
    elif kind == "shutdown":
        _shut_down(run)
    elif kind == "session_reset":
        return new_session(ctx, previous=state, turn_id=inp.turn_id)
    else:
        raise ValueError(f"모르는 엔진 입력 {kind}")
    continues = kind in _CONTINUE_AFTER or (kind == "speech_activity" and inp.state == "cancelled")
    if continues and not run.s.speech_active:  # 사용자가 말하는 중이면 그 말을 덮어 읽지 않는다
        continue_from_say(run)
    timers.sync_timers(run)
    timers.sync_holds(run)
    return run.result()


def recover(state: SessionState, inp: EngineInput, ctx: Ctx) -> EngineResult:
    """입력 처리가 예외로 실패했을 때(엔진 버그) 멈추지 않게 최소한만 정리한다.

    그 문장은 다 읽은 것으로, 그 턴은 끝난 것으로 보고 마감을 다시 건다. 남은 문장이나 말소리 상태 하나 때문에
    무응답·확인 질문·알림 타이머가 모두 멈추는 것을 막는다. 낭독 시작 되먹임이 실패했으면 시작 표시만 되돌리고 문장은
    남긴다(끝 되먹임이 와야 빼고 kit_guidance를 남긴다).
    """
    run = Run(state, ctx, getattr(inp, "turn_id", None))
    if inp.kind == "playback" and inp.event == "started":
        pending = run.s.pending_says.get(inp.action_id)
        if pending is not None:
            pending.started = False
    elif inp.kind == "playback":
        run.s.pending_says.pop(inp.action_id, None)
        timers.confirm_finished(run, inp.action_id)
    if inp.kind in ("function_call", "model_error", "progress_phrase", "speech_activity"):
        run.s.speech_active = False
    timers.end_announcing(run)
    timers.sync_timers(run)
    timers.sync_holds(run)
    return run.result()


def _barge_in(run: Run) -> None:
    """낭독 중 말하기 버튼: 남은 낭독을 끊고 엔진 위치를 아직 다 읽지 않은 첫 단계로 되돌린다(6-2).

    끊긴 문장은 시퀀서의 cut 되먹임으로 kit_guidance(played: cut)가 남는다. 이어 가기는 사용자 입력을 처리한 뒤에 한다.
    """
    run.preempt()
    _back_to_unspoken(run)


def _shut_down(run: Run) -> None:
    """키트 끄기: 남은 안내를 끊고(replace, 아무것도 읽지 않음) 켜 둔 메트로놈·구조신호를 끈다. 위치·타이머·알림은 그대로
    둔다(가짜 종료 훅이 동결을 풀면 이어 간다)."""
    run.preempt()
    for name in ("metronome", "rescue_signal"):
        set_sound_device(run, name, on=False, source="engine")


def _back_to_unspoken(run: Run) -> str | None:
    """다 읽지 못한 첫 단계 문장(버려진 문장 제외)의 단계로 위치와 그때의 call_stack을 되돌린다. 그 단계 ID, 없으면 None."""
    unspoken = [p for p in run.s.pending_says.values()
                if not p.dropped and p.utterance.source == "step" and p.utterance.step_id]
    if not unspoken:
        return None
    run.s.call_stack = list(unspoken[0].call_stack)
    run.set_step(unspoken[0].utterance.step_id)
    return unspoken[0].utterance.step_id


def new_session(ctx: Ctx, previous: SessionState | None = None, turn_id: str | None = None,
                previous_session_id: str | None = None) -> EngineResult:
    """새 세션: 켜진 소리를 끄고 알림을 취소하고, 준비가 끝났으면 T_WELCOME(6-14).

    전원을 켤 때는 previous 없이 지난 세션 ID(previous_session_id)만 받는다. 같은 분의 세션 ID가 이미 있으면 _2, _3…을 붙인다.
    """
    taken = (ctx.session_ids | {previous.session_id}) if previous else ctx.session_ids
    base = session_id = "s_" + ctx.wall.strftime("%Y%m%d_%H%M")
    n = 1
    while session_id in taken:
        n += 1
        session_id = f"{base}_{n}"
    state = SessionState(session_id=session_id,
                         previous_session_id=previous.session_id if previous else previous_session_id,
                         started_at=ctx.iso())
    if previous is not None:
        state.counters = previous.counters.model_copy()
        # 읽던 안내 뒤로 미룬 요청·멈춤 표시는 이전 세션의 것이라 버린다(시퀀서는 이 상태에 없는 장치 명령을 건너뛴다)
        state.devices = {k: d.model_copy(update={"queued": None, "held_by": None}) for k, d in previous.devices.items()}
        state.volume = previous.volume
        state.mic = previous.mic.model_copy()
        state.capturing = previous.capturing  # 녹음 중에 세션을 바꿔도 녹음은 이어진다
        state.alerts = [a.model_copy() for a in previous.alerts]
    run = Run(state, ctx, turn_id)
    run.preempt()
    for name in ("metronome", "rescue_signal"):
        set_sound_device(run, name, on=False, source="engine")
    cancel_alerts(run)
    run.say_template("T_WELCOME")
    timers.sync_holds(run)
    return run.result()


def resume_session(state: SessionState, ctx: Ctx, saved_now: float, downtime_s: float) -> EngineResult:
    """같은 부팅에서 백엔드만 다시 떴을 때 스냅샷(저장 시각 saved_now)에서 그 세션을 이어 간다(6-14).

    엔진 시계의 시각(알림 마감, 타이머 구간 시작, GPS 재시도 마감)은 저장 시각 기준 남은 시간에서 끊긴 시간(downtime_s)을
    뺀 만큼으로 옮긴다. 끊긴 동안 끝난 알림은 마감이 이미 지나 다음 Tick에서 바로 처리된다. 읽던 문장·말소리·확인 질문·
    응답을 기다리던 장치 명령·안내 중과 녹음 중 표시·대기(queued)와 멈춤(held_by) 표시와 무응답·대기 마감은
    버리고(다시 읽으면 sync_timers가 다시 건다), 켜져 있던 메트로놈·구조신호는 다시 켠다(재연결 때 자동으로 울리지
    않는 규칙의 예외). T_RESUME 뒤에, 다 읽지 못한 단계 문장이
    있었으면 말하기 버튼처럼 그 첫 단계로 되돌려 거기부터 읽고, 없으면 현재 단계를 다시 읽는다.
    안내 시작 전(현재 단계 없음, idle)이면 T_RESUME 대신 T_WELCOME을 읽는다.
    """
    run = Run(state, ctx, None)
    s, shift = run.s, ctx.now - downtime_s - saved_now
    unspoken_step = _back_to_unspoken(run)
    for alert in s.alerts:
        if alert.deadline is not None:
            alert.deadline += shift
    if s.timer.running_since is not None:
        s.timer.running_since += shift
    if s.timer.starts_after is not None:
        timers.start_after_confirm(run)  # 확인 문장은 다시 읽지 않으므로 이어 가는 지금 시작한다
    if s.gps_retry is not None:
        s.gps_retry.give_up_at += shift
    s.pending_says, s.speech_active, s.pending_devices, s.timer_reset_record = {}, False, {}, None
    s.announcing = s.capturing = False
    for device in s.devices.values():
        device.queued = device.held_by = None
    s.no_speech.deadline = None
    s.wait_deadline = None
    clear_prompt(run)
    for alert in s.alerts:
        if alert.waits_for_action is not None:
            start_alert(run, alert)  # 그 단계 문장을 읽다 끊겼으면 지금부터 잰다
    for name in ("metronome", "rescue_signal"):
        if s.devices[name].want == "on":
            set_sound_device(run, name, on=True, source="engine", force=True)
    if s.step_id is None and s.mode == "idle":
        run.say_template("T_WELCOME")
    else:
        run.say_template("T_RESUME")
    if unspoken_step is not None:
        goto(run, unspoken_step)
    else:
        functions.repeat(run, 2, 0)  # ask면 질문, wait면 마지막으로 읽은 단계
        continue_from_say(run)  # say 단계(말하기 버튼으로 되돌아온 곳)면 그 단계부터
    timers.sync_timers(run)
    timers.sync_holds(run)
    return run.result()
