"""사용자 턴 처리 (명세서 6-4): 모델 출력별, 해석·인식 실패, 대기 단계 진행 표현. 입력 하나에 단계 이동은 한 번만."""
from app.schemas.function_call import FunctionCall
from app.schemas.record import UserReportRecord, UserUtteranceRecord
from app.schemas.session import ObservedValue
from app.services.procedure_engine import engine_constants as C
from app.services.procedure_engine.context import Run
from app.services.procedure_engine.devices_rules import request_device
from app.services.procedure_engine.flow import (
    announce_gps, ask, ask_again, cancel_alerts, current_step, goto, leave_compression, log_answer, manual_of,
    new_routing, route, routing_target_manual, slot_branch, store_observation,
)
from app.services.procedure_engine.inputs import FunctionCallInput, ModelErrorInput, ProgressPhraseInput
from app.services.procedure_engine.interrupts import (
    answer_prompt, clear_prompt, reask_prompt, switch, try_i1, try_i2,
)


def _record_user(run: Run, inp, fc: FunctionCall | None, corrects: str | None = None, note: str | None = None) -> None:
    rec = UserUtteranceRecord(**run.record_base(), text=inp.text, normalized=inp.normalized, function_call=fc,
                              corrects=corrects, note=note)
    run.user_record_id = rec.record_id
    run.record(rec)


def handle_function_call(run: Run, inp: FunctionCallInput) -> None:
    fc = inp.function_call
    func = fc.func
    corrects = None
    if func == 2:
        key = next(k for k, v in fc.observation.model_dump().items() if v != -1)
        old = run.s.observation[key]
        corrects = old.record_id if old else None
    note = "기타 type 4, 처리 미정" if func == 5 and fc.args["type"] == 4 else None
    _record_user(run, inp, fc, corrects, note)
    run.event("turn.result", {"function_call": fc.model_dump(), "error_code": None})
    run.s.speech_active = False

    prompt = run.s.pending_prompt
    if prompt is not None and prompt.type == "switch_confirm" and func != 1:
        _during_switch_confirm(run, fc)
        return
    if prompt is not None and prompt.type == "timer_reset" and func != 1:
        clear_prompt(run)  # 그 확인 질문의 답이 아닌 입력이면 취소하고 그 입력을 처리한다
    if prompt is not None and prompt.type in ("which_on", "which_off") and func != 4:
        clear_prompt(run)

    if func == 0:
        _report_situation(run, fc)
    elif func == 1:
        _answer_check(run, inp, fc)
    elif func == 2:
        _correct(run, fc)
    elif func == 3:
        repeat(run, fc.args["scope"], fc.args["speed"])
    elif func == 4:
        request_device(run, fc.args["object"], fc.args["type"], "voice")
    elif func == 5:
        _out_of_scope(run, fc.args["type"])


def _during_switch_confirm(run: Run, fc: FunctionCall) -> None:
    """확인 질문(switch_confirm) 중 확인 응답이 아닌 입력(v1.7): I1·I2는 그대로, 상황 설명·정정은 관찰값만 저장,
    그 밖의 함수는 각 규칙대로 처리한 뒤 확인 질문을 다시 묻고 15초를 다시 잰다."""
    if fc.observation is not None:
        store_observation(run, fc.observation, "situation" if fc.func == 0 else "correction")
        if try_i1(run, None) or try_i2(run, fc.observation):
            return
    if fc.func == 3:
        repeat(run, fc.args["scope"], fc.args["speed"])
    elif fc.func == 4:
        prompt = run.s.pending_prompt
        request_device(run, fc.args["object"], fc.args["type"], "voice")
        run.s.pending_prompt = prompt  # 기기 요청이 확인 질문을 지우지 않게 되돌린다
    elif fc.func == 5:
        _out_of_scope(run, fc.args["type"], reask=False)
    if run.s.pending_prompt is not None:
        reask_prompt(run)


def _report_situation(run: Run, fc: FunctionCall) -> None:
    store_observation(run, fc.observation, "situation")
    if try_i1(run, None) or try_i2(run, fc.observation):
        return
    situation = fc.args["situation"]
    active = run.s.active_manual_id
    if active and situation != -1 and run.db.manual_by_situation[situation]["manual_id"] != active:
        switch(run, situation, fc)
        return
    step = current_step(run)
    if step is not None and step["kind"] == "ask" and step["slot"]:
        value = getattr(fc.observation, step["slot_key"])
        branch = slot_branch(step, [value]) if value != -1 else None
        if branch:
            log_answer(run, step, [value], "answer", branch)
            goto(run, step[branch])
            return
    if run.s.mode == "idle":
        new_routing(run, routing_target_manual(run, situation))
        route(run, situation)
        return
    o = fc.observation
    if step is not None and step["step_id"] == C.OBSERVE_WAIT_STEP and (o.conscious != -1 or o.breathing != -1):
        goto(run, C.OBSERVE_RECHECK_STEP)
    elif step is not None and step["kind"] == "ask":
        ask_again(run)
    else:
        run.say_template("T_NOTED")


def _answer_branch(step: dict, fc: FunctionCall) -> tuple[str, list[int] | None, bool]:
    """ask 단계에서 이 확인 응답이 갈 분기, 저장할 값 묶음, 관찰값으로 분기했는지."""
    if step["slot"]:
        value = getattr(fc.observation, step["slot_key"])
        branch = slot_branch(step, [value]) if value != -1 else None
        if branch:
            return branch, [value], True
    answer = fc.args["answer"]
    if answer == 3:
        return "on_unknown", None, False
    branch = "on_yes" if answer == 1 else "on_no"
    allowed = (step["slot_yes"] if answer == 1 else step["slot_no"]) if step["slot"] else None
    return branch, allowed, False


def _answer_check(run: Run, inp: FunctionCallInput, fc: FunctionCall) -> None:
    store_observation(run, fc.observation, "answer")
    step = current_step(run)
    target = None
    if step is not None and step["kind"] == "ask" and run.s.pending_prompt is None:
        target = step[_answer_branch(step, fc)[0]]
    if try_i1(run, target) or try_i2(run, fc.observation):
        return
    if run.s.pending_prompt is not None:
        answer_prompt(run, fc.args["answer"])
        return
    if step is None:
        return  # 안내 전 확인 응답은 기록만
    if inp.epoch != run.s.epoch or inp.step_id != run.s.step_id:
        ask_again(run)  # 질문이 바뀌어 있으면 그 답을 쓰지 않고 현재 질문을 다시 묻는다
        return
    if step["kind"] == "ask":
        branch, allowed, by_value = _answer_branch(step, fc)
        if branch == "on_unknown":
            run.say_template("T_UNKNOWN")
        elif allowed is not None:
            if not by_value:
                run.s.observation[step["slot_key"]] = ObservedValue(
                    allowed=list(allowed), source="answer", step_id=step["step_id"], record_id=run.user_record_id,
                    routing_no=run.s.routing_no, at=run.ctx.iso())
            log_answer(run, step, list(allowed), "answer", branch)
        if step["report_on_yes"] and branch == "on_yes":
            _user_report(run, inp, step["report_on_yes"], step["step_id"], "report_on_yes")
        run.s.no_speech.count = 0
        goto(run, step[branch])
    elif step["kind"] == "wait":
        nxt = step["next"]
        if nxt and run.db.steps_by_id[nxt]["kind"] == "ask":
            goto(run, nxt)
        else:
            _user_report(run, inp, inp.normalized, step["step_id"], "wait_answer")  # 말없이 기록
    else:
        run.say_template("T_NO_ASK")
        goto(run, step["step_id"])


def _user_report(run: Run, inp, report: str, step_id: str, basis: str) -> None:
    run.record(UserReportRecord(**run.record_base(), text=inp.text, report=report, step_id=step_id, basis=basis,
                                from_record=run.user_record_id))


def _correct(run: Run, fc: FunctionCall) -> None:
    store_observation(run, fc.observation, "correction")
    if try_i1(run, None):
        return
    key = next(k for k, v in fc.observation.model_dump().items() if v != -1)
    value = getattr(fc.observation, key)
    for index in range(len(run.s.answer_log) - 1, -1, -1):
        entry = run.s.answer_log[index]
        if entry.slot_key != key:
            continue
        step = run.db.steps_by_id[entry.step_id]
        branch = slot_branch(step, [value])
        if branch is None or branch == entry.branch:
            run.say_template("T_CORRECT_KEEP")
            ask_again(run)
            return
        run.say_template("T_CORRECT")
        cancel_alerts(run, lambda a: a.log_index > index)  # 되돌린 지점 이후에 건 알림
        target = step[branch]
        if manual_of(run, target) not in (C.CARDIAC_MANUAL, C.AED_MANUAL):
            leave_compression(run)
        run.s.answer_log = run.s.answer_log[: index + 1]
        log_answer(run, step, [value], "answer", branch)
        run.preempt()
        goto(run, target)
        return
    run.say_template("T_CORRECT_KEEP")  # 그런 질문이 아직 없으면 값만 바꾼다
    ask_again(run)


def repeat(run: Run, scope: int, speed: int) -> None:
    """반복 요청(다시 듣기 버튼은 scope 2). 단계·상태·타이머·알림은 바꾸지 않는다."""
    if scope == 3:
        run.say_template("T_REPEAT_ASK", speed=speed)
        return
    if scope == 1:
        last = run.s.last_utterance
        if last is None:
            return
        run.say_utterance(last.model_copy(update={"utterance_id": run.next_id("utterance", "u"), "speed": speed}))
        return
    step = current_step(run)
    if step is not None and step["kind"] == "ask":
        ask(run, step, speed)
    elif step is not None and step["kind"] == "say" and step["tts"]:
        run.say_step(step, "instruction", speed)
    elif run.s.spoken_step_id:
        spoken = run.db.steps_by_id[run.s.spoken_step_id]  # wait처럼 문장이 없으면 마지막으로 읽은 단계
        run.say_step(spoken, "instruction", speed)


def _out_of_scope(run: Run, typ: int, reask: bool = True) -> None:
    if typ == 1:
        if run.s.mode == "idle":
            run.notice("OUT_OF_SCOPE", "T_OOS_1", run.template_text("T_OOS_1"))  # 안내 전에는 말하지 않음
            return
        run.say_template("T_OOS_1")
    elif typ == 2:
        run.say_template("T_OOS_2")
        announce_gps(run)
    elif typ == 3:
        run.say_template("T_OOS_3")  # 매뉴얼 밖 내용은 답하지 않는다
        if reask:
            repeat(run, 2, 0)
        return
    if reask:
        ask_again(run)


def handle_model_error(run: Run, inp: ModelErrorInput) -> None:
    run.s.speech_active = False
    run.event("turn.result", {"function_call": None, "error_code": inp.error_code.value})
    if inp.error_code.value.startswith("STT_"):
        run.notice(inp.error_code.value)  # 잡음마다 말하지 않게 화면에만. 무응답 횟수에 넣지 않음
        return
    _record_user(run, inp, None)
    run.say_template("T_RETRY")
    if run.s.pending_prompt is not None:
        reask_prompt(run)
    else:
        ask_again(run)


def handle_progress_phrase(run: Run, inp: ProgressPhraseInput) -> None:
    run.s.speech_active = False
    _record_user(run, inp, None)
    step = current_step(run)
    if step is None or step["kind"] != "wait":
        ask_again(run)
    elif step["next"]:
        run.s.wait_deadline = None
        goto(run, step["next"])
    else:
        run.say_template("T_WAITING")  # 압박 2분·관찰 3분·조난 30분은 건너뛰지 않음


def handle_location_button(run: Run) -> None:
    """메인 화면의 119 신고 버튼: 기타 type 2와 같은 처리."""
    _out_of_scope(run, 2)
