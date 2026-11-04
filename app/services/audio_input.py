"""① 음성 입력 자리 (명세서 내부 모듈 계약 ① audio_input). 상시 듣기(VAD)와 실제 마이크는 AI 쪽·키트 단계에서 같은 자리에
끼우고, 백엔드는 말하기 버튼 녹음 Protocol과 가짜(app/fakes/fake_audio.py)만 둔다."""
from typing import Literal, Protocol

from pydantic import BaseModel


class AudioSegment(BaseModel):
    """발화 하나(①의 출력). 시각은 턴 지연과 같은 time.perf_counter() 초이고, ended_at은 마지막 음성 프레임 시각(지연 t0)."""

    turn_id: str
    pcm: bytes
    sample_rate: int = 16000
    started_at: float
    ended_at: float
    trigger: Literal["vad", "button"]
    merged_from: list[str] = []  # 짧게 끊긴 발화를 이어 붙였으면 그 발화들


class ButtonRecorder(Protocol):
    """말하기 버튼 녹음(시작은 버튼, 끝은 "말하기 완료"). 마이크가 없으면 connected가 False다."""

    connected: bool

    def start(self, turn_id: str) -> None:
        """녹음을 시작한다. 바로 끝나야 한다(프레임은 녹음기가 따로 모은다)."""
        ...

    def stop(self) -> AudioSegment:
        """녹음을 끝내고 모은 발화를 돌려준다(trigger: button)."""
        ...

    def discard(self) -> None:
        """지금까지 녹음을 인식·처리 없이 버린다(다시 말하기)."""
        ...
