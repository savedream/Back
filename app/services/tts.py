"""⑦ TTS 자리. 어떤 TTS를 쓸지는 AI 쪽에서 정하고(미정), 백엔드는 이 Protocol과 가짜만 둔다."""
from typing import Literal, Protocol

from app.schemas.utterance import Utterance


class Speaker(Protocol):
    async def speak(self, utterance: Utterance, volume: int) -> Literal["done", "text_only"]:
        """문장을 끝까지 읽고 돌려준다. 스피커가 없어 글로만 냈으면 text_only. 낭독을 끊을 때는 이 코루틴을 취소한다."""
        ...
