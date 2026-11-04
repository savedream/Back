"""가짜 말하기 버튼 녹음·STT (명세서 내부 모듈 계약 ① audio_input, ② stt, AC-S1)."""
import pytest

from app.config import Settings
from app.fakes.fake_audio import FakeRecorder, FakeStt


def test_recorder_makes_button_segment():
    recorder = FakeRecorder()
    recorder.start("t_0001")
    assert recorder.recording == "t_0001"
    segment = recorder.stop()
    assert (segment.turn_id, segment.trigger, segment.sample_rate) == ("t_0001", "button", 16000)
    assert segment.started_at <= segment.ended_at and recorder.recording is None
    with pytest.raises(RuntimeError):
        recorder.stop()


def test_recorder_discard_drops_recording():
    recorder = FakeRecorder()
    recorder.start("t_0001")
    recorder.discard()
    assert recorder.recording is None and recorder.discarded == 1 and recorder.started == ["t_0001"]


async def test_stt_says_configured_sentence_unless_queued():
    stt = FakeStt(Settings(_env_file=None).fake_stt_text)
    recorder = FakeRecorder()
    recorder.start("t_0001")
    segment = recorder.stop()
    stt.queue.extend(["네", ""])
    assert [(await stt.transcribe(segment)).text for _ in range(3)] == ["네", "", "등산하다 넘어졌는데 다리를 움직이기 어려워요"]
    result = await stt.transcribe(segment)
    assert result.turn_id == "t_0001" and stt.segments == [segment] * 4
