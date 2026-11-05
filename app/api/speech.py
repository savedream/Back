"""말하기 버튼 API (명세서 "말하기"): POST /api/speech/start·stop·restart.

시작은 말하기 버튼, 끝은 "말하기 완료", "다시 말하기"는 녹음을 버리고 처음부터(① audio_input). 녹음 상태와 턴 잠금은
SpeechCapture(app/core/speech.py)가 들고, 녹음한 말은 STT 뒤 /api/utterances와 같은 턴 경로(③부터)를 탄다.
"""
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.common.response import OkResponse, ok
from app.core.readiness import require_ready

router = APIRouter()


class SpeechOut(BaseModel):
    turn_id: str
    state: Literal["capturing", "processing"]


@router.post("/speech/start", response_model=OkResponse[SpeechOut], dependencies=[Depends(require_ready)])
async def speech_start(request: Request):
    """녹음 시작. 읽던 안내는 끊고(barge-in, 6-2) 켜 둔 구조신호는 녹음 동안 멈춘다(held_by recording).
    마이크 일시정지·구조신호 중에도 된다. 녹음·턴 처리 중이면 409 AUDIO_BUSY, 마이크가 없으면 503 MIC_UNAVAILABLE."""
    turn_id = await request.app.state.speech.start()
    return ok(SpeechOut(turn_id=turn_id, state="capturing"))


@router.post("/speech/stop", status_code=202, response_model=OkResponse[SpeechOut],
             dependencies=[Depends(require_ready)])
async def speech_stop(request: Request):
    """말하기 완료. 바로 202로 응답하고 인식·해석·엔진 처리는 백그라운드에서 한다(결과는 speech.final·안내 이벤트).
    지연 t0는 이 호출 시각. 녹음 중이 아니면 409 NOT_LISTENING."""
    turn_id = request.app.state.speech.stop()
    return ok(SpeechOut(turn_id=turn_id, state="processing"))


@router.post("/speech/restart", response_model=OkResponse[SpeechOut], dependencies=[Depends(require_ready)])
async def speech_restart(request: Request):
    """다시 말하기. 지금까지 녹음을 인식 없이 버리고 같은 turn_id로 처음부터 다시 녹음한다(최대 녹음 시간도 다시 잰다).
    버린 녹음은 기록하지 않는다. 녹음 중이 아니면 409 NOT_LISTENING."""
    turn_id = request.app.state.speech.restart()
    return ok(SpeechOut(turn_id=turn_id, state="capturing"))
