"""마이크 API (명세서 "말하기" POST /api/mic/pause·resume): 상시 듣기 일시정지 설정.

사용자 설정이라 화면 이동·장치 재연결·안내 음성·재시작 이어가기로 풀지 않는다(엔진 상태 mic.paused). 일시정지 중에도
말하기 버튼 녹음은 되고, 질문 단계 무응답 타이머는 멈춘다(6-9).
"""
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.common.response import OkResponse, ok
from app.core.readiness import require_ready
from app.services.procedure_engine.inputs import MicInput
from app.services.procedure_engine.timers import listen_state

router = APIRouter()


class MicOut(BaseModel):
    # 바뀐 뒤 듣기 상태(speech.state의 state). 구조신호 중이면 blocked, 마이크가 없으면 unavailable
    state: Literal["armed", "paused", "blocked", "unavailable"]


async def _set_paused(request: Request, paused: bool):
    state = request.app.state
    engine = state.parts.engine
    result = await engine.submit(MicInput(paused=paused))
    listening = listen_state(result.state)
    # 안내를 읽거나 턴을 받는(녹음·처리) 중이면 그것이 끝날 때 듣기 상태를 내므로 지금은 바꾸지 않는다
    if not engine.sequencer.reading and not state.turn_lock.busy:
        engine.sequencer.set_speech_state(**listening)
    return ok(MicOut(state=listening["state"]))


@router.post("/mic/pause", response_model=OkResponse[MicOut], dependencies=[Depends(require_ready)])
async def mic_pause(request: Request):
    """상시 듣기 일시정지. 이미 일시정지여도 200이고, 마이크가 없어도 설정은 저장한다."""
    return await _set_paused(request, True)


@router.post("/mic/resume", response_model=OkResponse[MicOut], dependencies=[Depends(require_ready)])
async def mic_resume(request: Request):
    """상시 듣기 다시 시작. 이미 듣는 중이어도 200."""
    return await _set_paused(request, False)
