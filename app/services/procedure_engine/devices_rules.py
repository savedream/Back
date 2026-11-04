"""기기 규칙 (명세서 6-10): 음성(func 4)·버튼 요청, 장치 응답.

음성·버튼 요청은 T_DEV_* 확인 문장을 읽고, 장치는 그 문장이 끝난 직후 실행한다(actions 순서). 동작 후 원래 질문을 다시 묻는다.
"""
from app.schemas.device import PendingStart
from app.schemas.record import DeviceResultRecord
from app.services.procedure_engine import engine_constants as C
from app.services.procedure_engine.context import Run
from app.services.procedure_engine.flow import announce_gps, set_sound_device
from app.services.procedure_engine.interrupts import (
    cancel_timer_start, clear_prompt, open_prompt, reask, save_timer_record,
)
from app.services.procedure_engine.templates import SOUND_DEVICES_SPOKEN, read_count

_SOUND = {2: ("metronome", "T_DEV_METRO_ON", "T_DEV_METRO_OFF"), 4: ("rescue_signal", "T_DEV_SOS_ON", "T_DEV_SOS_OFF")}


def request_device(run: Run, obj: int, typ: int, source: str) -> None:
    """걸려 있는 확인 질문: which_on·which_off는 음성 기능 요청이 그 답이고, switch_confirm은 다른 입력으로 취소하지
    않고 처리 뒤 다시 묻는다(v1.7). 그 밖의 확인 질문은 이 입력으로 취소한다. 버튼은 누른 그대로 실행한다."""
    prompt = run.s.pending_prompt
    if prompt is not None and prompt.type in ("which_on", "which_off") and obj in _SOUND and source == "voice":
        typ = 1 if prompt.type == "which_on" else 2  # 대상 없는 켜기·끄기 확인 질문의 답: type과 상관없이 켜기·끄기
        clear_prompt(run)
    elif prompt is not None and prompt.type != "switch_confirm":
        clear_prompt(run)

    waiting = False  # 새 확인 질문을 걸었거나 저장 결과를 기다리면 지금 다시 묻지 않는다
    if obj == 1:
        announce_gps(run)
    elif obj in _SOUND:
        _sound(run, obj, on=typ == 1, source=source)
    elif obj == 3:
        waiting = _timer(run, typ, source)
    elif obj == 5:
        _volume(run, typ, source)
    elif obj == -1:
        waiting = _no_target(run, on=typ == 1, source=source)
    if not waiting:
        reask(run)


def _sound(run: Run, obj: int, on: bool, source: str) -> None:
    name, on_template, off_template = _SOUND[obj]
    device = run.s.devices[name]
    if device.want == ("on" if on else "off") and device.status != "unknown":
        run.say_template("T_DEV_ALREADY_ON" if on else "T_DEV_ALREADY_OFF")  # 켜진 기능에 켜 줘라고 해도 끄지 않음
        return
    if not on and device.queued is not None and _cancel_queued(run, device):
        run.say_template(off_template)
        return
    say_action = run.say_template(on_template if on else off_template)
    if not on:
        # 응답 실패로 확인이 안 되는(unknown) 기능은 같은 요청이어도 다시 명령한다
        set_sound_device(run, name, on=False, source=source, force=True)
        return
    prev = {"want": device.want, "status": device.status, "last_off_device": run.s.last_off_device}
    set_sound_device(run, name, on=True, source=source, force=True, after_say=say_action, prev=prev)
    run.on_requests.append((name, source))


def _cancel_queued(run: Run, device) -> bool:
    """읽던 안내 뒤로 미룬 켜기에 끄기가 오면 대기를 취소한다(6-10): 응답 대기에서 빼고(시퀀서가 그 켜기 명령을 건너뜀)
    요청 전 값으로 되돌린다. 끄기 명령은 내지 않는다. 이미 큐에 있는 켜기 확인 문장은 그대로 읽힌다. 취소했으면 True."""
    queued = [a for a, c in run.s.pending_devices.items() if c["object"] == device.object and "after_say" in c]
    if not queued:
        return False
    command = run.s.pending_devices.pop(queued[-1])
    prev = command["prev"]
    device.want, device.status, device.queued = prev["want"], prev["status"], None
    run.s.last_off_device = prev["last_off_device"]
    run.record(DeviceResultRecord(**run.record_base(), object=device.object, type=command["type"],
                                  source=command["source"], result="cancelled", step_id=command["step_id"]))
    run.event("device.state", device.model_dump())
    return True


def _no_target(run: Run, on: bool, source: str) -> bool:
    """대상 없는 켜기·끄기는 소리 기능(메트로놈, 구조신호)만. 확인 질문을 걸었으면 True."""
    if on:
        if run.s.last_off_device in _SOUND:
            _sound(run, run.s.last_off_device, on=True, source=source)
            return False
        open_prompt(run, "which_on", "T_WHICH_ON")
        return True
    playing = [obj for obj, (name, _, _) in _SOUND.items() if run.s.devices[name].want == "on"]
    if len(playing) == 1:
        _sound(run, playing[0], on=False, source=source)
    elif len(playing) == 2:
        open_prompt(run, "which_off", "T_WHICH_OFF", devices=SOUND_DEVICES_SPOKEN)
        return True
    else:
        run.say_template("T_NOTHING_ON")
    return False


def _timer(run: Run, typ: int, source: str) -> bool:
    """경과 시간 타이머(엔진 상태). 초기화 확인 질문을 걸었으면 True.

    시작·재개는 확인 문장이 끝난 뒤에 하고(그때 기록, timers.start_after_confirm), 일시정지는 누른 순간 멈춘다(AC-E1·E2).
    """
    timer, now = run.s.timer, run.ctx.now
    result = "success"
    if typ == 1:
        if timer.status == "running" or timer.starts_after is not None:
            run.say_template("T_DEV_ALREADY_ON")
            result = "already"
        else:
            template = "T_DEV_TIMER_START" if timer.status == "idle" else "T_DEV_TIMER_RESUME"
            timer.starts_after = PendingStart(action_id=run.say_template(template), source=source, turn_id=run.turn_id)
            return False
    elif typ == 2:
        elapsed = timer.elapsed_s(now)
        run.say_template("T_DEV_TIMER_CHECK", min=read_count(elapsed // 60), sec=read_count(elapsed % 60))
    elif typ == 3:
        if timer.starts_after is not None:
            cancel_timer_start(run)  # 시작 전에 일시정지가 오면 시작을 취소한다
        if timer.status != "running":
            run.say_template("T_DEV_ALREADY_OFF")
            result = "already"
        else:
            timer.accumulated_s += now - timer.running_since
            timer.status, timer.running_since = "paused", None
            elapsed = timer.elapsed_s(now)
            run.say_template("T_DEV_TIMER_PAUSE", min=read_count(elapsed // 60), sec=read_count(elapsed % 60))
    elif typ == 4:
        if timer.status == "idle":
            if timer.starts_after is not None:
                cancel_timer_start(run)
            run.say_template("T_DEV_ALREADY_OFF")  # 저장할 구간이 없음
            result = "already"
        elif source == "button":
            save_timer_record(run, source, run.s.step_id)  # 화면 확인 창을 거친 버튼 = 확인 질문에 네
            return True
        else:
            open_prompt(run, "timer_reset", "T_TIMER_RESET_ASK")
            return True
    run.record(DeviceResultRecord(**run.record_base(), object=3, type=typ, source=source, result=result))
    return False


def _volume(run: Run, typ: int, source: str) -> None:
    """안내 음성만, 한 번에 10씩, 10~100 사이. 끝값에서는 그대로."""
    before = run.s.volume
    step = C.VOLUME_STEP if typ == 1 else -C.VOLUME_STEP
    run.s.volume = max(C.VOLUME_MIN, min(C.VOLUME_MAX, before + step))
    run.say_template("T_DEV_VOL_UP" if typ == 1 else "T_DEV_VOL_DOWN")
    if run.s.volume != before:
        run.device(5, typ, source)
    run.record(DeviceResultRecord(**run.record_base(), object=5, type=typ, source=source,
                                  result="success" if run.s.volume != before else "already",
                                  detail={"volume": run.s.volume}))


def device_result(run: Run, action_id: str | None, obj: int, status: str, detail: dict | None) -> None:
    """장치 응답·연결 변화. 성공 응답 전에는 켜짐으로 표시하지 않는다."""
    command = run.s.pending_devices.pop(action_id, None) if action_id else None
    if command is not None:
        run.record(DeviceResultRecord(**run.record_base(), object=command["object"], type=command["type"],
                                      source=command["source"], result=status, step_id=command["step_id"],
                                      detail=detail))
    name = {2: "metronome", 4: "rescue_signal"}.get(obj)
    if name is None:
        return
    device = run.s.devices[name]
    if status == "success":
        done = ("on" if command["type"] == 1 else "off") if command is not None else device.want
        # 그 사이 반대 명령을 냈으면 그 응답이 올 때까지 처리 중으로 둔다
        device.status = done if done == device.want else "pending"
        device.last_result, device.last_error = "success", None
    elif status in ("failed", "timeout", "unavailable"):
        device.status, device.last_result = "unknown", status
        device.last_error = (detail or {}).get("message")
    elif status == "disconnected":
        device.connected, device.status = False, "unknown"
    elif status == "connected":
        # 재연결돼도 자동으로 다시 울리지 않는다
        device.connected, device.status, device.want = True, "off", "off"
    run.event("device.state", device.model_dump())
