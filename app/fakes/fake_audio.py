"""가짜 말하기 버튼 녹음·STT. 실제 소리 없이 녹음 구간만 재고, STT는 설정 문장(fake_stt_text)을 알아들은 것으로 낸다.

시험에서는 마이크 없음(connected = False)과 다음에 알아들을 문장(queue)을 넣는다.
"""
import time
from collections import deque

from app.services.audio_input import AudioSegment
from app.services.stt import SttResult


class FakeRecorder:
    def __init__(self) -> None:
        self.connected = True
        self.recording: str | None = None  # 녹음 중인 턴
        self.started: list[str] = []  # start 호출 기록(턴 ID)
        self.discarded = 0
        self._started_at = 0.0

    def start(self, turn_id: str) -> None:
        self.recording, self._started_at = turn_id, time.perf_counter()
        self.started.append(turn_id)

    def stop(self) -> AudioSegment:
        if self.recording is None:
            raise RuntimeError("녹음 중이 아님")
        turn_id, self.recording = self.recording, None
        return AudioSegment(turn_id=turn_id, pcm=b"", started_at=self._started_at, ended_at=time.perf_counter(),
                            trigger="button")

    def discard(self) -> None:
        self.recording = None
        self.discarded += 1


class FakeStt:
    def __init__(self, text: str):
        self.text = text  # queue가 비었을 때 알아들을 문장
        self.queue: deque[str] = deque()  # 시험이 넣은 다음 문장들(빈 문자열이면 소리 없음)
        self.segments: list[AudioSegment] = []  # 받은 발화

    async def transcribe(self, segment: AudioSegment) -> SttResult:
        self.segments.append(segment)
        text = self.queue.popleft() if self.queue else self.text
        return SttResult(turn_id=segment.turn_id, text=text, latency_ms=0)
