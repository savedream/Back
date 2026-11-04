"""② STT 자리 (명세서 내부 모듈 계약 ② stt). 어떤 STT를 쓸지는 AI 쪽에서 정하고, 백엔드는 이 Protocol과 가짜만 둔다."""
from typing import Protocol

from pydantic import BaseModel

from app.services.audio_input import AudioSegment


class SttResult(BaseModel):
    turn_id: str
    text: str  # 알아들은 문장. 소리가 없으면 빈 문자열
    latency_ms: int


class Stt(Protocol):
    async def transcribe(self, segment: AudioSegment) -> SttResult:
        """문장 하나를 한 번에 인식한다(스트리밍 없음). text가 빈 문자열이면 호출한 쪽이 STT_EMPTY, 예외면 STT_FAILED로
        처리한다. 실제 STT는 이벤트 루프를 막지 않게 전용 단일 스레드 executor에서 돌린다."""
        ...
