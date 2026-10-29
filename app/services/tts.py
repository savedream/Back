"""⑦ TTS 자리. 어떤 TTS를 쓸지는 AI 쪽에서 정하고(미정), 백엔드는 이 Protocol과 가짜만 둔다."""
from collections.abc import Callable
from typing import Literal, Protocol

from app.schemas.utterance import Utterance


class Speaker(Protocol):
    async def speak(self, utterance: Utterance, volume: int,
                    on_sound: Callable[[], None]) -> Literal["done", "text_only"]:
        """문장을 끝까지 읽고 돌려준다. 스피커가 없어 글로만 냈으면 text_only. 낭독을 끊을 때는 이 코루틴을 취소한다.

        첫 소리가 실제로 나가는 순간 on_sound()를 부른다(turn.latency 기준). 다른 스레드에서는 이벤트 루프로 넘겨 부른다.
        """
        ...
