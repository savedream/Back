"""시스템 API: GET /health, POST /api/system/shutdown."""
import asyncio
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel

from app.common.error_code import ErrorCode
from app.common.exceptions import AppError
from app.common.response import OkResponse, ok
from app.services.procedure_engine.inputs import ShutdownInput

router = APIRouter()


class HealthOut(BaseModel):
    ready: bool
    version: str
    manual_db: str
    model: str


@router.get("/health", response_model=OkResponse[HealthOut])
def health(request: Request):
    """준비 상태. 적재 중에도 응답하고, 장치는 열지 않는다."""
    settings = request.app.state.settings
    return ok(
        HealthOut(
            ready=request.app.state.readiness.ready,
            version=settings.app_version,
            manual_db=request.app.state.manual_db.version,
            model=settings.model_version,
        )
    )


class ShutdownIn(BaseModel):
    confirm: Any = None  # true일 때만 끈다


class ShutdownOut(BaseModel):
    saved_records: int  # 지금 세션의 저장된 기록 수
    shutting_down: bool


@router.post("/system/shutdown", response_model=OkResponse[ShutdownOut])
async def shutdown(request: Request, background: BackgroundTasks, body: ShutdownIn | None = None):
    """키트 끄기(화면 전원 버튼). 준비 전에도 응답한다(엔진이 시작 전이면 소리 기능 끄기는 건너뜀).

    새 입력을 동결하고(준비 필요 API는 503, 절차 알림·무응답·GPS 재시도는 미룸) 기록을 모두 저장한다. 그래도 남으면 동결을
    풀고 끄지 않으며 500 SAVE_FAILED. 저장했으면 녹음 중인 말하기 버튼 턴을 버리고(마이크 일시정지 설정은 그대로) 남은 안내를
    끊고 메트로놈·구조신호를 끈 뒤, 그 결과 기록까지 저장해 보고 응답한다. 종료 훅은 응답을 보낸 뒤 부른다(가짜는 꺼짐 요청만
    표시하고 동결을 풀어 서버를 계속 쓸 수 있다).
    """
    if body is None or body.confirm is not True:
        raise AppError(ErrorCode.CONFIRM_REQUIRED)
    state = request.app.state
    parts = state.parts
    engine = parts.engine
    engine.freeze()
    try:
        await _settle_turn(state, state.settings.shutdown_turn_wait_s)
        await parts.records.flush()
        _, left = await parts.records.retry_now()
        if left:
            raise AppError(ErrorCode.SAVE_FAILED, f"기록 {left}건을 저장하지 못했습니다")
        # 녹음을 먼저 버린다. 그 뒤에 이어 읽을 안내가 생겨도 종료 입력이 함께 끊는다
        state.speech.cancel()
        if engine.state is not None:
            await engine.submit(ShutdownInput(turn_id=state.turn_lock.next_id()))
            await engine.sequencer.drain()  # 끊긴 문장·꺼진 장치 결과 기록까지
        # 이번 저장이 실패해도 응답은 그대로 한다(미저장은 record.save_failed로 나간다)
        await parts.records.flush()
    except BaseException:
        engine.unfreeze()
        raise
    records = await parts.records.session_records(engine.state.session_id) if engine.state is not None else []
    background.add_task(parts.shutdown.shutdown)
    return ok(ShutdownOut(saved_records=sum(r.saved for r in records), shutting_down=True))


async def _settle_turn(state, limit_s: float) -> None:
    """처리 중인 턴(말하기 완료 뒤 STT·모델, 문장 턴)이 끝나기를 기다린다. 동결은 새 입력만 막으므로, 기다리지 않으면 그 턴이
    끈 뒤에 안내를 읽거나 소리 기능을 다시 켜고 그 기록이 저장에서 빠진다. 녹음 중인 턴은 기다리지 않는다(뒤에서 버림)."""
    loop = asyncio.get_running_loop()
    end = loop.time() + limit_s
    while state.turn_lock.busy and state.speech.turn_id is None and loop.time() < end:
        await asyncio.sleep(0.01)
