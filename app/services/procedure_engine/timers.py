"""낭독 되먹임과 시간 규칙 (명세서 6-8 대기, 6-9 무응답·마이크, 6-11 GPS 재시도, 6-12 절차 알림).

시간은 상태의 마감 시각으로 두고, 셸이 가장 이른 마감에 TickInput을 넣는다. epoch로 버리지 않고 규칙으로만 취소한다.
"""
from app.schemas.record import KitGuidanceRecord
from app.schemas.session import SessionState
from app.services.procedure_engine import engine_constants as C
from app.services.procedure_engine.context import Run
from app.services.procedure_engine.flow import ask_again, current_step, goto, start_alert
from app.services.procedure_engine.interrupts import clear_prompt, finish_timer_reset, prompt_timed_out
from app.services.procedure_engine.templates import read_coordinate


def deadlines(state: SessionState) -> list[float]:
    """지금 걸린 마감 시각들. 말소리를 받는 중에는 절차 알림을 그 턴이 끝난 뒤로 미루므로 빼 둔다(6-12)."""
    found = [] if state.speech_active else [a.deadline for a in state.alerts if a.deadline is not None]
    if state.pending_prompt is not None and state.pending_prompt.deadline is not None:
        found.append(state.pending_prompt.deadline)
    for d in (state.no_speech.deadline, state.wait_deadline, state.gps_retry and state.gps_retry.give_up_at):
        if d is not None:
            found.append(d)
    return found


def next_deadline(state: SessionState) -> float | None:
    """셸이 다음에 깨어날 시각."""
    found = deadlines(state)
    return min(found) if found else None


def _can_listen(run: Run) -> bool:
    """무응답 타이머를 멈추는 동안: 마이크 일시정지, 구조신호, 확인 질문, 말소리 받는 중."""
    s = run.s
    return not (s.mic.paused or s.devices["rescue_signal"].want == "on" or s.pending_prompt is not None
                or s.speech_active)


def listen_state(state: SessionState) -> dict:
    """낭독이 끝난 뒤의 듣기 상태(speech.state 이벤트 data)."""
    if not state.mic.connected:
        return {"state": "unavailable"}
    if state.devices["rescue_signal"].want == "on":
        return {"state": "blocked", "reason": "rescue_signal"}
    if state.mic.paused:
        return {"state": "paused"}
    return {"state": "armed"}


def sync_timers(run: Run) -> None:
    """입력 하나를 처리한 끝에 확인 질문·무응답·대기 마감을 상태에 맞춘다(6-7·6-8·6-9).

    마감을 지우는 곳은 여럿이어도 다시 거는 곳은 여기 하나다. 읽을 문장이 남았거나 말소리를 받는 중이면 멈추고,
    끝나면 처음부터 다시 잰다.
    """
    s, now = run.s, run.ctx.now
    step = current_step(run)
    quiet = not s.pending_says and not s.speech_active
    prompt = s.pending_prompt
    if prompt is not None:
        if quiet and prompt.waits_for_action is None:  # 확인 질문을 다 읽은 뒤부터 15초
            prompt.deadline = prompt.deadline if prompt.deadline is not None else now + C.PROMPT_TIMEOUT_S
        else:
            prompt.deadline = None
    if step is not None and step["kind"] == "ask" and quiet and _can_listen(run):
        if s.no_speech.deadline is None:
            # 마이크가 없으면 재질문 없이 낭독 5초 뒤 on_unknown
            s.no_speech.deadline = now + (C.NO_SPEECH_S if s.mic.connected else C.MIC_MISSING_ADVANCE_S)
    else:
        s.no_speech.deadline = None
    if (step is not None and step["kind"] == "wait" and step["wait_timeout_s"] and quiet
            and s.wait_timeouts.get(step["step_id"], 0) < C.WAIT_TIMEOUT_MAX):
        if s.wait_deadline is None:
            s.wait_deadline = now + step["wait_timeout_s"]
    else:
        s.wait_deadline = None


def handle_playback(run: Run, action_id: str, event: str, result: str | None) -> None:
    if event != "finished":
        return
    pending = run.s.pending_says.pop(action_id, None)
    if pending is not None:
        utterance = pending.utterance
        base = {**run.record_base(pending.record_id), "turn_id": pending.turn_id}
        run.record(KitGuidanceRecord(**base, utterance=utterance, played=result or "done"))
        if utterance.source == "step" and utterance.step_id and result != "cut":
            run.s.spoken_step_id = utterance.step_id
    for alert in run.s.alerts:
        if alert.waits_for_action == action_id:
            start_alert(run, alert)  # 그 단계 문장의 낭독이 끝난 시각부터 잰다
    prompt = run.s.pending_prompt
    if prompt is not None and prompt.waits_for_action == action_id:
        prompt.waits_for_action = None  # 확인 질문을 다 읽음. 15초는 sync_timers가 건다


def handle_tick(run: Run) -> None:
    now, s = run.ctx.now, run.s
    # 절차 알림. 말소리를 받는 중이면 그 턴이 끝난 뒤 처리한다
    if not s.speech_active:
        for alert in sorted((a for a in s.alerts if a.deadline is not None and a.deadline <= now),
                            key=lambda a: a.deadline):
            s.alerts.remove(alert)
            run.event("alert.ended", {"alert_id": alert.alert_id, "reason": "timeout"})
            if alert.on_timeout:
                clear_prompt(run)  # 위치가 바뀌면 걸려 있던 확인 질문은 취소한다(타이머 초기화라면 타이머는 그대로)
                run.preempt()
                goto(run, alert.on_timeout)
                break  # 입력 하나에 단계 이동은 한 번. 같이 끝난 알림은 다음 Tick에서 처리한다
            else:
                step = run.db.steps_by_id[alert.step_id]
                if step["alert_tts"]:
                    run.say(step["alert_tts"], "notice", "alert", manual_id=step["manual_id"], step_id=alert.step_id)
                else:
                    run.say_template("T_ALERT_DONE")
    if s.pending_prompt is not None and s.pending_prompt.deadline is not None and s.pending_prompt.deadline <= now:
        prompt_timed_out(run)
    if s.no_speech.deadline is not None and s.no_speech.deadline <= now and not s.speech_active:
        s.no_speech.deadline = None
        step = current_step(run)
        if step is not None and step["kind"] == "ask":
            if not s.mic.connected:
                goto(run, step["on_unknown"])
            else:
                s.no_speech.count += 1
                run.preempt()
                if s.no_speech.count >= C.NO_SPEECH_MAX:
                    s.no_speech.count = 0
                    run.say_template("T_UNKNOWN")  # 세 번 연속이면 더 안전한 쪽으로
                    goto(run, step["on_unknown"])
                else:
                    run.say_template("T_NO_SPEECH")
                    ask_again(run)
    if s.wait_deadline is not None and s.wait_deadline <= now and not s.speech_active:
        s.wait_deadline = None
        step = current_step(run)
        if step is not None and step["kind"] == "wait" and step["next"]:
            s.wait_timeouts[step["step_id"]] = s.wait_timeouts.get(step["step_id"], 0) + 1
            clear_prompt(run)  # 위치가 바뀌면 걸려 있던 확인 질문은 취소한다
            goto(run, step["next"])
    if s.gps_retry is not None and s.gps_retry.give_up_at <= now:
        s.gps_retry = None  # 3분 동안 못 잡으면 조용히 그만둔다


def handle_speech(run: Run, state: str) -> None:
    """말소리가 잡히면 턴 처리가 끝날 때까지 타이머를 멈춘다. ended 뒤에는 턴 입력(함수 호출·해석 실패·진행 표현)이
    반드시 온다(턴 파이프라인 계약). 턴 없이 끝나면 cancelled가 온다."""
    if state == "started":
        run.s.speech_active = True
    elif state == "cancelled":
        run.s.speech_active = False


def handle_mic(run: Run, connected: bool | None, paused: bool | None) -> None:
    mic = run.s.mic
    if connected is not None and connected != mic.connected:
        mic.connected = connected
        mic.error_announced = False
    if paused is not None:
        mic.paused = paused
    run.s.no_speech.deadline = None  # 마이크 상태에 맞는 길이로 다시 잰다


def handle_location(run: Run) -> None:
    """GPS 재시도 중 위치가 잡히면 다음 끊기 좋은 지점에 T_GPS_OK(지금 묶음 뒤에 붙는다)."""
    loc = run.ctx.location
    if run.s.gps_retry is not None and loc is not None and loc.fix:
        run.s.gps_retry = None
        run.say_template("T_GPS_OK", lat=read_coordinate(loc.lat), lon=read_coordinate(loc.lon))


def handle_record_saved(run: Run, action_id: str, ok: bool) -> None:
    if action_id == run.s.timer_reset_record:
        finish_timer_reset(run, ok)
