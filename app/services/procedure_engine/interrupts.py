"""I1·I2·매뉴얼 전환·확인 질문 (명세서 6-5, 6-6, 6-7). I1·I2는 언제나 확인 질문보다 먼저다."""
from app.schemas.device import TimerState
from app.schemas.function_call import FunctionCall, Observation
from app.schemas.record import DeviceResultRecord
from app.schemas.session import PendingPrompt
from app.services.procedure_engine import engine_constants as C
from app.services.procedure_engine.context import Run
from app.services.procedure_engine.flow import (
    ask_again, cancel_alerts, current_step, goto, log_answer, manual_of, new_routing, route, routing_target_manual,
    slot_branch, start_manual,
)
from app.services.procedure_engine.templates import SOUND_DEVICES_SPOKEN

_I1_EXCLUDED_MANUALS = {C.CARDIAC_MANUAL, C.AED_MANUAL}


def try_i1(run: Run, branch_target: str | None, said: Observation | None) -> bool:
    """이번 입력이 의식이나 호흡 값을 말했고, 의식·호흡 값 묶음이 둘 다 정확히 [0]이고, 현재 단계와 이번 입력의 분기
    목적지가 모두 EM-01·COM-AED 밖이면 심정지로. 예전에 저장한 값만으로는 걸지 않는다(2026-10-29 팀 결정)."""
    if said is None or (said.conscious == -1 and said.breathing == -1):
        return False
    obs = run.s.observation
    if not all(obs[k] is not None and obs[k].allowed == [0] for k in ("conscious", "breathing")):
        return False
    if run.s.step_id and manual_of(run, run.s.step_id) in _I1_EXCLUDED_MANUALS:
        return False
    if branch_target and manual_of(run, branch_target) in _I1_EXCLUDED_MANUALS:
        return False
    target = C.I1_STEP_WITH_CALL119 if "call119" in run.s.flags else C.I1_STEP_WITHOUT_CALL119
    new_routing(run, C.CARDIAC_MANUAL)
    run.preempt()
    start_manual(run, C.CARDIAC_MANUAL, target)
    return True


def try_i2(run: Run, observation: Observation | None) -> bool:
    """압박 중(cpr) 회복 신호(호흡 1·2, 움직임 1, 의식 1·2)가 오면 EM01-13 회복 확인 질문으로. 메트로놈과 cpr은 그대로."""
    if observation is None or "cpr" not in run.s.flags or run.s.step_id in C.I2_EXCLUDED_STEPS:
        return False
    o = observation
    if not (o.breathing in (1, 2) or o.movement == 1 or o.conscious in (1, 2)):
        return False
    cancel_alerts(run)
    run.s.call_stack = []
    clear_prompt(run)
    run.preempt()
    goto(run, C.I2_STEP)
    return True


def clear_prompt(run: Run) -> None:
    if run.s.pending_prompt is not None:
        run.s.pending_prompt = None
        run.position_changed()


def open_prompt(run: Run, type: str, template_id: str, **values: str) -> PendingPrompt:
    """확인 질문을 읽고 답을 기다린다. 걸려 있는 동안 원래 질문의 무응답 타이머는 멈춘다."""
    action_id = run.say_template(template_id, **values)
    prompt = PendingPrompt(type=type, template_id=template_id, origin_step_id=run.s.step_id,
                           waits_for_action=action_id)
    run.s.pending_prompt = prompt
    run.s.no_speech.deadline = None
    run.position_changed()
    return prompt


def reask_prompt(run: Run, speed: int = 0) -> None:
    """확인 질문을 다시 묻고 15초를 다시 잰다."""
    prompt = run.s.pending_prompt
    values = {}
    if prompt.template_id == "T_SWITCH_ASK":
        values = {"name": _spoken_name(run, prompt.target_manual_id)}
    elif prompt.template_id == "T_WHICH_OFF":
        values = {"devices": SOUND_DEVICES_SPOKEN}
    prompt.deadline = None
    prompt.waits_for_action = run.say_template(prompt.template_id, speed=speed, **values)


def reask(run: Run) -> None:
    """걸려 있는 확인 질문이 있으면 그것을, 없으면 현재 질문을 다시 묻는다."""
    if run.s.pending_prompt is not None:
        reask_prompt(run)
    else:
        ask_again(run)


def resume_after_prompt(run: Run, prompt: PendingPrompt) -> None:
    """확인이 끝난 뒤 원래 단계에 그대로 있을 때만 원래 질문을 다시 묻는다."""
    if run.s.step_id == prompt.origin_step_id:
        ask_again(run)


def _spoken_name(run: Run, manual_id: str) -> str:
    return run.db.manuals_by_id[manual_id]["spoken_name"]


# 매뉴얼 전환 (6-6)
def switch(run: Run, situation: int, fc: FunctionCall) -> None:
    new_manual = run.db.manual_by_situation[situation]["manual_id"]
    if (run.s.active_manual_id == C.UNKNOWN_MANUAL or run.s.step_id == C.DISTRESS_WAIT_STEP
            or situation == C.CARDIAC_SITUATION):
        do_switch(run, situation)
    elif "cpr" in run.s.flags:
        run.say_template("T_NOTED")  # 압박 구간이면 전환하지 않는다. 발화는 기록함
    else:
        prompt = open_prompt(run, "switch_confirm", "T_SWITCH_ASK", name=_spoken_name(run, new_manual))
        prompt.target_manual_id = new_manual
        prompt.deferred_branch = fc


def do_switch(run: Run, situation: int) -> None:
    run.say_template("T_SWITCH")
    new_routing(run, routing_target_manual(run, situation))
    run.preempt()
    route(run, situation)


def _switch_by_priority(run: Run, prompt: PendingPrompt) -> None:
    """모름이나 15초 무응답: 새 매뉴얼의 priority 숫자가 지금보다 작으면 전환, 아니면 유지."""
    new = run.db.manuals_by_id[prompt.target_manual_id]
    current = run.db.manuals_by_id[run.s.active_manual_id]
    if new["priority"] < current["priority"]:
        _switch_confirmed(run, prompt)
    else:
        resume_after_prompt(run, prompt)


def _switch_confirmed(run: Run, prompt: PendingPrompt) -> None:
    """확인 질문을 거쳐 전환한다. 확인을 건 입력의 관찰값도 새 routing_no로 다시 매긴다(공통 데이터 형식 ObservedValue)."""
    said = prompt.deferred_branch.observation if prompt.deferred_branch else None
    if said is not None:
        run.input_obs_keys.extend(k for k, v in said.model_dump().items() if v != -1)
    do_switch(run, run.db.manuals_by_id[prompt.target_manual_id]["situation_code"])


def answer_prompt(run: Run, answer: int) -> None:
    """timer_reset·switch_confirm에 대한 확인 응답(6-7)."""
    prompt = run.s.pending_prompt
    clear_prompt(run)
    if prompt.type == "switch_confirm":
        if answer == 1:
            _switch_confirmed(run, prompt)
        elif answer == 2:
            # 하던 안내 계속. 이번 관찰값은 이미 반영됨, 상황 설명 처리 5번(현재 질문 분기)부터 이어서
            if not _continue_deferred(run, prompt.deferred_branch):
                resume_after_prompt(run, prompt)
        else:
            _switch_by_priority(run, prompt)
    elif prompt.type == "timer_reset":
        if answer == 1:
            save_timer_record(run, "voice", prompt.origin_step_id)
        else:
            run.say_template("T_DEV_TIMER_KEEP")
            resume_after_prompt(run, prompt)


def prompt_timed_out(run: Run) -> None:
    prompt = run.s.pending_prompt
    clear_prompt(run)
    if prompt.type == "switch_confirm":
        _switch_by_priority(run, prompt)
    elif prompt.type == "timer_reset":
        run.say_template("T_DEV_TIMER_KEEP")
        resume_after_prompt(run, prompt)
    else:
        resume_after_prompt(run, prompt)  # which_on·which_off는 말없이 취소하고 원래 질문으로


def _continue_deferred(run: Run, fc: FunctionCall | None) -> bool:
    """전환 확인에 아니요: 확인을 건 상황 설명을 5번(현재 질문 분기)과 6번(관찰 대기 중 의식·호흡 → MON-02)부터 잇는다."""
    step = current_step(run)
    if fc is None or fc.observation is None or step is None:
        return False
    o = fc.observation
    if step["kind"] == "ask" and step["slot"]:
        value = getattr(o, step["slot_key"])
        branch = slot_branch(step, [value]) if value != -1 else None
        if branch is not None:
            log_answer(run, step, [value], "answer", branch)
            goto(run, step[branch])
            return True
    if step["step_id"] == C.OBSERVE_WAIT_STEP and (o.conscious != -1 or o.breathing != -1):
        goto(run, C.OBSERVE_RECHECK_STEP)
        return True
    return False


def cancel_timer_start(run: Run) -> None:
    """확인 문장 뒤로 미룬 타이머 시작·재개를 취소한다(그 전에 일시정지·초기화가 옴). 그 요청은 cancelled로 남긴다."""
    pending, run.s.timer.starts_after = run.s.timer.starts_after, None
    run.record(DeviceResultRecord(**{**run.record_base(), "turn_id": pending.turn_id}, object=3, type=1,
                                  source=pending.source, result="cancelled"))


# 타이머 초기화: 현재 측정 구간을 기록에 저장한 뒤 초기화(저장 결과는 RecordSaved로 돌아온다)
def save_timer_record(run: Run, source: str, origin_step_id: str | None) -> None:
    if run.s.timer.starts_after is not None:
        cancel_timer_start(run)  # 미뤄 둔 재개는 하지 않고 지금 구간으로 초기화한다
    elapsed = run.s.timer.elapsed_s(run.ctx.now)
    run.s.timer_reset_record = run.record(DeviceResultRecord(
        **run.record_base(), object=3, type=4, source=source, result="success", detail={"elapsed_s": elapsed}),
        wants_result=True)


def finish_timer_reset(run: Run, ok: bool) -> None:
    run.s.timer_reset_record = None
    if ok:
        run.s.timer = TimerState()
        run.say_template("T_DEV_TIMER_RESET")
    else:
        run.say_template("T_DEV_TIMER_KEEP")
        run.notice("SAVE_FAILED")
    reask(run)
