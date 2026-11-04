"""POST /api/utterances (명세서 "[시험] 문장으로 한 턴 처리(내부 API)"): 마이크 없이 문장으로 한 턴을 처리한다."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.common.response import OkResponse, ok
from app.core.readiness import require_ready
from app.core.turn import UtteranceReply, run_utterance

router = APIRouter()


class UtteranceIn(BaseModel):
    text: str = Field(min_length=1)  # 사용자가 한 말 대신 넣는 문장
    speak: bool = True  # false면 낭독하지 않고 끝난 것으로 처리


@router.post("/utterances", response_model=OkResponse[UtteranceReply], dependencies=[Depends(require_ready)])
async def post_utterance(body: UtteranceIn, request: Request):
    """처리 중인 턴이 있으면 409 AUDIO_BUSY. 엔진 처리까지 기다리는 동기 호출이다(낭독은 기다리지 않음)."""
    state = request.app.state
    with state.turn_lock.turn() as turn_id:
        reply = await run_utterance(state.parts, state.settings.model_timeout_s, turn_id, body.text, body.speak)
    return ok(reply)
