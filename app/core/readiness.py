"""준비 상태 (명세서 GET /health: 매뉴얼 DB·VAD·STT·TTS·LLM 적재와 LLM 예열이 끝나야 ready)."""
from enum import Enum

from fastapi import Request

from app.common.error_code import ErrorCode
from app.common.exceptions import AppError


class Component(str, Enum):
    MANUAL_DB = "manual_db"
    VAD = "vad"
    STT = "stt"
    TTS = "tts"
    LLM = "llm"
    LLM_WARMUP = "llm_warmup"


class Readiness:
    def __init__(self) -> None:
        self._done: set[Component] = set()

    def mark_ready(self, component: Component) -> None:
        self._done.add(component)

    @property
    def ready(self) -> bool:
        return self._done.issuperset(Component)


def require_ready(request: Request) -> None:
    """음성·엔진이 필요한 API에 붙이는 의존성. 준비 전이면 503 NOT_READY, 키트를 끄는 중(엔진 동결)이어도 503 NOT_READY."""
    state = request.app.state
    if not state.readiness.ready:
        raise AppError(ErrorCode.NOT_READY)
    if state.parts.engine.frozen:
        raise AppError(ErrorCode.NOT_READY, "종료 중입니다")
