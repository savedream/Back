"""한 턴의 중간 결과 (명세서 내부 모듈 계약 ③~⑤)."""
from pydantic import BaseModel


class NormalizedText(BaseModel):
    """③ 정규화 결과. original은 STT 원문(텍스트 턴이면 넣은 문장)."""

    original: str
    text: str
