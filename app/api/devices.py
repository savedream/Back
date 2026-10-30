"""기기 버튼 API (명세서 "기기"): 구조신호, 음량, 타이머.

화면 버튼을 DeviceCommand(source: button)로 바꿔 엔진 큐(device_button)에 넣는다. 음성 명령과 같은 규칙(6-10)을 탄다.
응답은 엔진이 받아들인 결과만 바로 주고, 실제 상태는 이벤트(device.state, status.changed, session.updated)로 나간다.
"""
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.common.error_code import ErrorCode
from app.common.exceptions import AppError
from app.common.response import OkResponse, ok
from app.core.readiness import require_ready
from app.schemas.device import DeviceCommand, DeviceState
from app.schemas.record import DeviceResultRecord
from app.schemas.session import SessionState
from app.services.procedure_engine.actions import DeviceAction, EngineResult, RecordAction
from app.services.procedure_engine.inputs import DeviceButtonInput

router = APIRouter()

# pending: 확인 문장을 읽고 곧 실행 · queued: 읽던 안내 문장이 끝난 뒤 실행 · already: 이미 같은 상태
ButtonResult = Literal["pending", "queued", "already"]


@dataclass
class Pressed:
    turn_id: str
    result: EngineResult
    reading: bool  # 누를 때 읽던(읽을) 안내 문장이 있었는지


async def _press(request: Request, obj: int, typ: int) -> Pressed:
    """버튼 하나 = 턴 하나. 턴 잠금은 쓰지 않고(명세 오류 목록에 AUDIO_BUSY가 없음) 턴 번호만 받는다."""
    state = request.app.state
    engine = state.parts.engine
    reading = engine.sequencer.reading  # 읽던 문장이 있으면 확인 문장은 그 뒤에 읽고, 실행도 그 뒤가 된다
    turn_id = state.turn_lock.next_id()
    command = DeviceCommand(object=obj, type=typ, source="button")
    result = await engine.submit(DeviceButtonInput(turn_id=turn_id, command=command))
    return Pressed(turn_id=turn_id, result=result, reading=reading)


def _button_result(pressed: Pressed, already: bool) -> ButtonResult:
    if already:
        return "already"
    return "queued" if pressed.reading else "pending"


def _commanded(result: EngineResult, obj: int) -> bool:
    """엔진이 그 장치에 명령을 냈는지. 이미 같은 상태면 확인 문장만 읽고 명령은 내지 않는다."""
    return any(isinstance(a, DeviceAction) and a.command.object == obj for a in result.actions)


def _require_speaker(state: SessionState) -> None:
    """스피커는 안내 음성과 구조신호가 함께 쓴다. 구조신호 장치의 연결 상태로 스피커 연결을 본다."""
    if not state.devices["rescue_signal"].connected:
        raise AppError(ErrorCode.DEVICE_UNAVAILABLE, "스피커가 연결되지 않았습니다")


class RescueSignalOut(BaseModel):
    turn_id: str
    result: ButtonResult
    device: dict  # DeviceState에서 엔진 내부 칸(want)을 뺀 것


def _device_view(device: DeviceState) -> dict:
    return device.model_dump(exclude={"want"})


async def _rescue_signal(request: Request, on: bool):
    _require_speaker(request.app.state.parts.engine.state)
    pressed = await _press(request, 4, 1 if on else 2)
    return ok(RescueSignalOut(
        turn_id=pressed.turn_id,
        result=_button_result(pressed, already=not _commanded(pressed.result, 4)),
        device=_device_view(pressed.result.state.devices["rescue_signal"]),
    ))


@router.post("/rescue-signal/on", response_model=OkResponse[RescueSignalOut], dependencies=[Depends(require_ready)])
async def rescue_signal_on(request: Request):
    """구조신호 켜기. T_DEV_SOS_ON을 읽고 그 문장이 끝난 직후 켠다. 울리는 상태는 device.state로 보낸다."""
    return await _rescue_signal(request, on=True)


@router.post("/rescue-signal/off", response_model=OkResponse[RescueSignalOut], dependencies=[Depends(require_ready)])
async def rescue_signal_off(request: Request):
    """구조신호 끄기. T_DEV_SOS_OFF를 읽고 그 문장이 끝난 직후 끈다."""
    return await _rescue_signal(request, on=False)


class SpeakerView(BaseModel):
    connected: bool
    volume: int


class VolumeOut(BaseModel):
    turn_id: str
    result: ButtonResult
    speaker: SpeakerView


async def _volume(request: Request, up: bool):
    parts = request.app.state.parts
    _require_speaker(parts.engine.state)
    pressed = await _press(request, 5, 1 if up else 2)
    state = pressed.result.state
    speaker = SpeakerView(connected=state.devices["rescue_signal"].connected, volume=state.volume)
    already = not _commanded(pressed.result, 5)  # 끝값(10, 100)이면 바꾸지 않는다
    if not already:
        parts.events.publish("status.changed", pressed.turn_id, {"speaker": {"volume": state.volume}})
    return ok(VolumeOut(turn_id=pressed.turn_id, result=_button_result(pressed, already), speaker=speaker))


@router.post("/volume/up", response_model=OkResponse[VolumeOut], dependencies=[Depends(require_ready)])
async def volume_up(request: Request):
    """안내 음성 음량을 10 올린다(최대 100). T_DEV_VOL_UP을 읽고 그 문장이 끝난 직후 바꾼다. 구조신호에는 적용하지 않는다."""
    return await _volume(request, up=True)


@router.post("/volume/down", response_model=OkResponse[VolumeOut], dependencies=[Depends(require_ready)])
async def volume_down(request: Request):
    """안내 음성 음량을 10 내린다(최소 10). T_DEV_VOL_DOWN을 읽고 그 문장이 끝난 직후 바꾼다."""
    return await _volume(request, up=False)


class TimerView(BaseModel):
    object: int = 3
    status: Literal["idle", "running", "paused"]
    elapsed_s: int
    started_at: str | None


class TimerOut(BaseModel):
    turn_id: str
    result: ButtonResult
    timer: TimerView


def _timer_view(request: Request, state: SessionState) -> TimerView:
    timer = state.timer
    return TimerView(status=timer.status, elapsed_s=timer.elapsed_s(request.app.state.parts.clock.now()),
                     started_at=timer.started_at)


def _recorded_result(result: EngineResult, obj: int, typ: int) -> str | None:
    """엔진이 남긴 그 버튼의 기기 결과 기록(device_result)의 result. 타이머는 장치 명령이 없어 기록으로 본다."""
    for action in result.actions:
        record = action.record if isinstance(action, RecordAction) else None
        if isinstance(record, DeviceResultRecord) and (record.object, record.type) == (obj, typ):
            return record.result
    return None


async def _timer(request: Request, typ: int):
    pressed = await _press(request, 3, typ)
    already = _recorded_result(pressed.result, 3, typ) == "already"  # 도는 중에 start, 도는 중이 아닌데 pause
    return ok(TimerOut(turn_id=pressed.turn_id, result=_button_result(pressed, already),
                       timer=_timer_view(request, pressed.result.state)))


@router.post("/timer/start", response_model=OkResponse[TimerOut], dependencies=[Depends(require_ready)])
async def timer_start(request: Request):
    """타이머 시작·재개. 멈춰 있으면 T_DEV_TIMER_START, 일시정지 중이면 T_DEV_TIMER_RESUME을 읽는다.
    시간 표시는 timer.tick·session.updated로 바꾼다."""
    return await _timer(request, 1)


@router.post("/timer/pause", response_model=OkResponse[TimerOut], dependencies=[Depends(require_ready)])
async def timer_pause(request: Request):
    """타이머 일시정지. T_DEV_TIMER_PAUSE(지금까지 잰 시간)를 읽는다. 경과 확인(type 2)은 음성으로만."""
    return await _timer(request, 3)
