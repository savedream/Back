"""절차 엔진 리듀서: reduce(state, input, ctx) -> EngineResult (명세서 ⑥, 계획 3장).

순수 함수다. 상태를 바꾸지 않고 사본으로 새 상태를 만들며, 시계·장치·네트워크를 직접 쓰지 않는다.
"""
from app.schemas.session import SessionState
from app.services.procedure_engine import functions, timers
from app.services.procedure_engine.actions import EngineResult
from app.services.procedure_engine.context import Ctx, Run
from app.services.procedure_engine.devices_rules import device_result, request_device
from app.services.procedure_engine.flow import cancel_alerts, continue_from_say, set_sound_device
from app.services.procedure_engine.inputs import EngineInput

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
    elif kind == "mic":
        timers.handle_mic(run, inp.connected, inp.paused)
    elif kind == "tick":
        timers.handle_tick(run)
    elif kind == "barge_in":
        _barge_in(run)
    elif kind == "session_reset":
        return new_session(ctx, previous=state, turn_id=inp.turn_id)
    else:
        raise ValueError(f"모르는 엔진 입력 {kind}")
    if kind in _CONTINUE_AFTER or (kind == "speech_activity" and inp.state == "cancelled"):
        continue_from_say(run)
    timers.sync_timers(run)
    return run.result()


def _barge_in(run: Run) -> None:
    """낭독 중 말하기 버튼: 남은 낭독을 끊고 엔진 위치를 아직 다 읽지 않은 첫 단계로 되돌린다(6-2).

    끊긴 문장은 시퀀서의 cut 되먹임으로 kit_guidance(played: cut)가 남는다. 이어 가기는 사용자 입력을 처리한 뒤에 한다.
    """
    unspoken = [p for p in run.s.pending_says.values()
                if not p.dropped and p.utterance.source == "step" and p.utterance.step_id]
    run.preempt()
    if unspoken:
        run.s.call_stack = list(unspoken[0].call_stack)
        run.set_step(unspoken[0].utterance.step_id)


def new_session(ctx: Ctx, previous: SessionState | None = None, turn_id: str | None = None) -> EngineResult:
    """새 세션: 켜진 소리를 끄고 알림을 취소하고, 준비가 끝났으면 T_WELCOME(6-14)."""
    session_id = "s_" + ctx.wall.strftime("%Y%m%d_%H%M")
    state = SessionState(session_id=session_id, previous_session_id=previous.session_id if previous else None,
                         started_at=ctx.iso())
    if previous is not None:
        state.counters = previous.counters.model_copy()
        state.devices = {k: d.model_copy() for k, d in previous.devices.items()}
        state.volume = previous.volume
        state.mic = previous.mic.model_copy()
        state.alerts = [a.model_copy() for a in previous.alerts]
    run = Run(state, ctx, turn_id)
    run.preempt()
    for name in ("metronome", "rescue_signal"):
        set_sound_device(run, name, on=False, source="engine")
    cancel_alerts(run)
    run.say_template("T_WELCOME")
    return run.result()
