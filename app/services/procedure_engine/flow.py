"""단계 진행·라우팅·절차 알림·GPS 낭독 (명세서 6-2, 6-3, 6-11, 6-12).

goto()는 입력 하나에서 다음 ask 또는 wait 단계까지 실행 목록을 만든다. 분기·문장은 모두 매뉴얼 DB에서 읽는다.
"""
import re

from app.schemas.function_call import Observation
from app.schemas.record import DeviceResultRecord
from app.schemas.session import Alert, AnswerLogEntry, GpsRetry, ObservedValue, RoutingFrame, StepFrame
from app.services.procedure_engine import engine_constants as C
from app.services.procedure_engine.context import Run
from app.services.procedure_engine.templates import read_coordinate, read_count

_MAX_STEPS = 200  # DB가 끝없이 이어지면 엔진 버그로 본다
_ALERT_RE = re.compile(r"alert:(\d+)")
_ROUTING_TERM_RE = re.compile(r"(\w+) (==|in) (-?\d+)(?:\.\.(-?\d+))?")


def manual_of(run: Run, step_id: str) -> str:
    return run.db.steps_by_id[step_id]["manual_id"]


def is_situation_manual(run: Run, manual_id: str) -> bool:
    return run.db.manuals_by_id[manual_id]["type"] == "situation"


# 관찰값
def store_observation(run: Run, observation: Observation, source: str) -> None:
    """-1(말하지 않음)은 덮어쓰지 않는다. 값마다 출처·시각·기록 ID를 남긴다."""
    for key, value in observation.model_dump().items():
        if value == -1:
            continue
        run.s.observation[key] = ObservedValue(allowed=[value], source=source, step_id=run.s.step_id,
                                               record_id=run.user_record_id, routing_no=run.s.routing_no,
                                               at=run.ctx.iso())
        run.input_obs_keys.append(key)


def known_values(run: Run, key: str) -> list[int] | None:
    """이번 라우팅에서 들은 값 묶음만 쓴다(질문 건너뛰기, 6-3 규칙 2)."""
    ov = run.s.observation.get(key)
    return ov.allowed if ov is not None and ov.routing_no == run.s.routing_no else None


def slot_branch(step: dict, allowed: list[int]) -> str | None:
    """값 묶음이 slot_yes나 slot_no 한쪽에 다 들어가면 그쪽 분기."""
    if not step["slot"] or not allowed:
        return None
    if set(allowed) <= set(step["slot_yes"]):
        return "on_yes"
    if set(allowed) <= set(step["slot_no"]):
        return "on_no"
    return None


def log_answer(run: Run, step: dict, allowed: list[int], how: str, branch: str) -> None:
    if step["slot"]:
        run.s.answer_log.append(AnswerLogEntry(step_id=step["step_id"], slot=step["slot"], slot_key=step["slot_key"],
                                               allowed=allowed, how=how, branch=branch,
                                               record_id=run.user_record_id, at=run.ctx.iso()))


# 단계 진행
def goto(run: Run, step_id: str) -> None:
    db = run.db
    for _ in range(_MAX_STEPS):
        step = db.steps_by_id[step_id]
        _arrive(run, step)
        kind = step["kind"]
        if kind == "say":
            if step["skip_flag"] and step["skip_flag"] in run.s.flags:
                step_id = step["next"]
                continue
            say_action = run.say_step(step, "instruction") if step["tts"] else None
            _run_step_devices(run, step, say_action)
            if step["call"]:
                run.s.call_stack.append(StepFrame(return_to=step["next"]))
                step_id = db.manuals_by_id[step["call"]]["entry_step"]
            else:
                step_id = step["next"]
        elif kind == "ask":
            if step["skip_flag"] and step["skip_flag"] in run.s.flags:
                step_id = step["on_no"]
                continue
            if step["skip_if_known"]:
                allowed = known_values(run, step["slot_key"])
                branch = slot_branch(step, allowed) if allowed else None
                if branch:
                    log_answer(run, step, allowed, "skip", branch)
                    step_id = step[branch]
                    continue
            ask(run, step)
            return
        elif kind == "call":
            run.s.call_stack.append(StepFrame(return_to=step["next"]))
            step_id = db.manuals_by_id[step["call"]]["entry_step"]
        elif kind == "return":
            if step["tts"]:
                run.say_step(step, "instruction")
            if not run.s.call_stack:
                step_id = step["next"]
                continue
            frame = run.s.call_stack.pop()
            if isinstance(frame, RoutingFrame):
                route(run, frame.situation, from_rule=frame.resume_rule)
                return
            step_id = frame.return_to
        elif kind == "wait":
            run.set_step(step_id)
            run.s.wait_deadline = None  # 대기 시간은 이 단계 문장을 다 읽은 뒤부터(sync_timers)
            return
        else:
            raise ValueError(f"알 수 없는 단계 종류 {kind}")
    raise RuntimeError(f"단계가 {_MAX_STEPS}번 넘게 이어짐: {step_id}")


def ask(run: Run, step: dict, speed: int = 0) -> None:
    """질문을 읽고 답을 기다린다. 무응답 횟수는 단계가 바뀔 때 0으로."""
    if run.s.step_id != step["step_id"]:
        run.s.no_speech.count = 0
    run.set_step(step["step_id"])
    run.s.no_speech.deadline = None
    if not run.s.mic.connected and not run.s.mic.error_announced:
        run.say_template("T_MIC_ERROR")  # 마이크가 없어진 뒤 첫 질문 전에 한 번(6-9)
        run.s.mic.error_announced = True
    run.say_step(step, "question", speed)


def ask_again(run: Run) -> None:
    """질문 중이면 현재 질문을 다시 묻는다. 위치가 그대로라 읽던 안내를 끊지 않고 그 뒤에 붙인다(append)."""
    step = current_step(run)
    if step is not None and step["kind"] == "ask":
        ask(run, step)


def continue_from_say(run: Run) -> None:
    """say처럼 멈춰 설 수 없는 단계에 서 있으면(말하기 버튼으로 되돌아온 곳) 그 단계부터 다시 이어 간다(6-2)."""
    step = current_step(run)
    if step is not None and step["kind"] not in ("ask", "wait") and run.s.pending_prompt is None:
        goto(run, step["step_id"])


def current_step(run: Run) -> dict | None:
    return run.db.steps_by_id[run.s.step_id] if run.s.step_id else None


def _arrive(run: Run, step: dict) -> None:
    # 알림의 on_timeout 목적지에 다른 경로로 먼저 도착하면 그 알림을 취소한다
    cancel_alerts(run, lambda a: a.on_timeout == step["step_id"])
    if is_situation_manual(run, step["manual_id"]):
        run.set_manual(step["manual_id"])


def _run_step_devices(run: Run, step: dict, say_action: str | None) -> None:
    """단계 device를 순서대로. 장치 명령은 그 단계 문장을 읽은 뒤에 실행된다(actions 순서)."""
    for d in step["device"]:
        if d == "metronome:on:110":
            set_sound_device(run, "metronome", on=True, source="engine", step_id=step["step_id"])
        elif d == "metronome:off":
            set_sound_device(run, "metronome", on=False, source="engine", step_id=step["step_id"])
        elif d == "timer:start":
            _timer_start_by_step(run, step)
        elif d == "gps:announce":
            announce_gps(run)
        elif d == "alert:cancel":
            cancel_alerts(run)
        elif m := _ALERT_RE.fullmatch(d):
            add_alert(run, step, int(m.group(1)), say_action)
        elif d.startswith("flag:"):
            if d[5:] not in run.s.flags:
                run.s.flags.append(d[5:])
        elif d.startswith("unflag:"):
            if d[7:] in run.s.flags:
                run.s.flags.remove(d[7:])
        else:
            raise ValueError(f"모르는 device {d} ({step['step_id']})")


def _timer_start_by_step(run: Run, step: dict) -> None:
    """멈춰 있으면 시작하고, 돌고 있거나 일시정지 중이면 그대로 두고 그 단계의 시각만 기록한다(6-3 규칙 7)."""
    timer = run.s.timer
    if timer.status == "idle":
        timer.status, timer.running_since, timer.started_at = "running", run.ctx.now, run.ctx.iso()
        detail = None
    else:
        detail = "already_running"
    run.record(DeviceResultRecord(**run.record_base(), object=3, type=1, source="engine", result="success",
                                  step_id=step["step_id"], detail=detail))


def set_sound_device(run: Run, name: str, on: bool, source: str, step_id: str | None = None,
                     force: bool = False) -> bool:
    """메트로놈(2)·구조신호(4) 켜기·끄기. 이미 같은 상태를 요청했으면(force가 아니면) 명령을 내지 않고 False."""
    device = run.s.devices[name]
    want = "on" if on else "off"
    if device.want == want and not force:
        return False
    device.want = want
    device.status = "pending"
    if not on:
        run.s.last_off_device = device.object
    action_id = run.device(device.object, 1 if on else 2, source, step_id)
    run.s.pending_devices[action_id] = {"object": device.object, "type": 1 if on else 2, "source": source,
                                        "step_id": step_id}
    run.event("device.state", device.model_dump())
    return True


def leave_compression(run: Run) -> None:
    """압박 구간을 벗어나면 metronome:off와 unflag:cpr (6-4 정정, 6-6 정리)."""
    if "cpr" in run.s.flags:
        run.s.flags.remove("cpr")
        set_sound_device(run, "metronome", on=False, source="engine")


# 절차 알림 (6-12)
def add_alert(run: Run, step: dict, seconds: int, say_action: str | None) -> None:
    cancel_alerts(run, lambda a: a.step_id == step["step_id"], reason="replaced")
    alert = Alert(alert_id=run.next_id("alert", "a", 2), step_id=step["step_id"], seconds=seconds,
                  on_timeout=step["on_timeout"], log_index=len(run.s.answer_log))
    run.s.alerts.append(alert)
    if say_action:
        alert.waits_for_action = say_action  # 그 단계 문장의 낭독이 끝난 시각부터 잰다
    else:
        start_alert(run, alert)  # 문장이 없는 단계(MON-07, EM15-10)는 도착한 시각부터


def start_alert(run: Run, alert: Alert) -> None:
    alert.waits_for_action = None
    alert.deadline = run.ctx.now + alert.seconds
    run.event("alert.started", {"alert_id": alert.alert_id, "step_id": alert.step_id, "seconds": alert.seconds,
                                "ends_at": run.ctx.iso(alert.seconds)})


def cancel_alerts(run: Run, match=None, reason: str = "cancel") -> None:
    for alert in [a for a in run.s.alerts if match is None or match(a)]:
        run.s.alerts.remove(alert)
        run.event("alert.ended", {"alert_id": alert.alert_id, "reason": reason})


# GPS 낭독 (6-11)
def announce_gps(run: Run) -> None:
    loc = run.ctx.location
    if loc is not None and loc.fix:
        run.say_template("T_GPS_OK", lat=read_coordinate(loc.lat), lon=read_coordinate(loc.lon))
        run.s.gps_retry = None
    elif loc is not None and loc.last_known is not None and loc.last_known.age_s <= C.GPS_LAST_KNOWN_MAX_S:
        last = loc.last_known
        run.say_template("T_GPS_LAST", ago=read_count(last.age_s // 60), lat=read_coordinate(last.lat),
                         lon=read_coordinate(last.lon))
    else:
        run.say_template("T_GPS_WAIT")
        run.s.gps_retry = GpsRetry(give_up_at=run.ctx.now + C.GPS_RETRY_MAX_S)


# 라우팅 (R1~R7)
def _matches(run: Run, condition: str, situation: int) -> bool:
    """routing.condition 고정 해석기: "a == 0 and b == 0", "situation == 1", "situation in 3..15"."""
    for term in condition.split(" and "):
        m = _ROUTING_TERM_RE.fullmatch(term.strip())
        if not m:
            raise ValueError(f"해석할 수 없는 라우팅 조건 {condition!r}")
        name, op, low, high = m.group(1), m.group(2), int(m.group(3)), m.group(4)
        if name == "situation":
            ok = low <= situation <= int(high) if op == "in" else situation == low
        else:
            ov = run.s.observation.get(name)
            ok = ov is not None and ov.allowed == [low]
        if not ok:
            return False
    return True


def _rule_target(run: Run, rule: dict, situation: int) -> str:
    if rule["target"] == "situation 표의 entry":
        return run.db.manual_by_situation[situation]["entry_step"]
    return rule["target"]


def routing_target_manual(run: Run, situation: int) -> str:
    """call 규칙(R4)을 빼고 라우팅이 결국 시작할 상황 매뉴얼."""
    for rule in run.db.data["routing"]:
        if rule["mode"] == "goto" and _matches(run, rule["condition"], situation):
            return manual_of(run, _rule_target(run, rule, situation))
    raise ValueError(f"situation {situation}에 맞는 라우팅 규칙이 없음")


def route(run: Run, situation: int, from_rule: str = "R1") -> None:
    rules = run.db.data["routing"]
    start = next(i for i, r in enumerate(rules) if r["rule_id"] == from_rule)
    for i in range(start, len(rules)):
        rule = rules[i]
        if not _matches(run, rule["condition"], situation):
            continue
        target = _rule_target(run, rule, situation)
        if rule["mode"] == "call":
            # R4 → COM-SAFE: 돌아오면 다음 규칙부터 이어서 평가한다
            run.s.call_stack.append(RoutingFrame(resume_rule=rules[i + 1]["rule_id"], situation=situation))
            run.s.mode = "guiding"
            goto(run, target)
            return
        start_manual(run, manual_of(run, target), target)
        return
    raise ValueError(f"situation {situation}에 맞는 라우팅 규칙이 없음")


def start_manual(run: Run, manual_id: str, step_id: str) -> None:
    """라우팅으로 매뉴얼을 시작할 때만 시작 안내(intro_tts)를 읽는다(6-3 규칙 6)."""
    run.s.mode = "guiding"
    run.set_manual(manual_id)
    manual = run.db.manuals_by_id[manual_id]
    run.say(manual["intro_tts"], "instruction", "intro", manual_id=manual_id)
    goto(run, step_id)


def new_routing(run: Run, target_manual: str) -> None:
    """라우팅(상황 설명으로 시작, 전환, I1) 직전 정리. 이번 입력의 관찰값은 올린 번호로 다시 매긴다(6-6 정리)."""
    if run.s.step_id is not None:
        run.s.call_stack = []
        cancel_alerts(run)
        if target_manual != C.CARDIAC_MANUAL:
            leave_compression(run)
        if run.s.pending_prompt is not None:
            run.s.pending_prompt = None
            run.position_changed()
    run.s.routing_no += 1
    for key in run.input_obs_keys:
        ov = run.s.observation[key]
        if ov is not None:
            ov.routing_no = run.s.routing_no
