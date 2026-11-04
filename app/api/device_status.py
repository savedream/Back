"""기기 상태 API (명세서 "기기" GET /api/devices): 기기 상태 화면이 한 번에 받는 장치·모델·저장 상태.

gps·models·battery·storage(data_mb·free_mb)·clock은 요청마다 검사하지 않고 주기적으로 갱신한 값(app/core/device_status.py,
바뀌면 status.changed)이고, 마이크·스피커·부저·소리 기능·타이머·미저장 기록 수는 지금 상태로 만든다.
"""
from fastapi import APIRouter, Request

from app.common.response import ok
from app.core.device_status import engine_state
from app.core.readiness import Component
from app.services.procedure_engine.timers import listen_state
from app.services.procedure_engine.view import timer_view

router = APIRouter()


@router.get("/devices")
def get_devices(request: Request):
    """준비 전에도 200(엔진 시작 전이면 기본 상태, 마이크는 blocked). 배터리를 못 읽으면 battery: null이고 남은 사용 시간은
    주지 않는다. metronome·rescue_signal은 DeviceState에서 엔진 내부 칸(want)을 뺀 것."""
    app = request.app.state
    parts = app.parts
    engine = parts.engine
    state = engine_state(parts)
    latest = app.device_status.latest
    if engine.state is None:
        mic = "blocked"  # 준비 전(GET /api/session과 같음)
    else:
        mic = engine.sequencer.speech_state or listen_state(state)["state"]
    rescue, metronome = state.devices["rescue_signal"], state.devices["metronome"]
    return ok({
        "gps": latest["gps"],
        "microphone": {"connected": state.mic.connected, "state": mic},
        "speaker": {"connected": rescue.connected, "volume": state.volume},
        "buzzer": {"connected": metronome.connected},
        "metronome": metronome.model_dump(exclude={"want"}),
        "rescue_signal": rescue.model_dump(exclude={"want"}),
        "timer": timer_view(state.timer, parts.clock.now()),
        "models": latest["models"],
        "manual_db": {"loaded": app.readiness.is_ready(Component.MANUAL_DB), "version": app.manual_db.version},
        "battery": latest["battery"],
        "storage": {"unsaved_records": len(parts.records.unsaved()), **latest["storage"]},
        "clock": latest["clock"],
    })
