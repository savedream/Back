"""키트가 하는 말 한 묶음 (명세서 공통 데이터 형식 Utterance)."""
from typing import Literal

from pydantic import BaseModel


class Utterance(BaseModel):
    utterance_id: str
    # question: 대답을 기다리는 질문 · instruction: 안내 · notice: 그 밖의 템플릿과 alert_tts
    role: Literal["question", "instruction", "notice"]
    text: str  # DB 원문 그대로(템플릿은 중괄호 자리만 채움)
    screen: str | None  # 단계의 screen. 템플릿·intro_tts·alert_tts는 None
    source: Literal["step", "intro", "alert", "template"]
    manual_id: str | None
    step_id: str | None
    template_id: str | None
    speed: int = 0  # 0 보통, 1 천천히(0.8배)
    audio: bool = True  # 스피커가 없거나 TTS 실패로 글로만 내면 False
