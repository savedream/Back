"""가짜 스피커·TTS: 문장을 받자마자 다 읽은 것으로 끝낸다. 스피커가 없으면 글로만 낸 것(text_only)으로 끝낸다."""
from app.schemas.utterance import Utterance


class FakeSpeaker:
    def __init__(self) -> None:
        self.spoken: list[Utterance] = []
        self.present = True

    async def speak(self, utterance: Utterance, volume: int) -> str:
        self.spoken.append(utterance)
        return "done" if self.present else "text_only"
