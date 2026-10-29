"""가짜 스피커: 바로 다 읽음, 스피커 없음이면 글로만."""
from app.fakes.fake_speaker import FakeSpeaker
from app.schemas.utterance import Utterance

U = Utterance(utterance_id="u_1", role="notice", text="안녕", screen=None, source="template", manual_id=None,
              step_id=None, template_id="T_WELCOME")


async def test_speaks_at_once():
    speaker, sounds = FakeSpeaker(), []
    assert await speaker.speak(U, 70, lambda: sounds.append(1)) == "done"
    assert speaker.spoken == [U] and sounds == [1]


async def test_no_speaker_is_text_only():
    speaker, sounds = FakeSpeaker(), []
    speaker.present = False
    assert await speaker.speak(U, 70, lambda: sounds.append(1)) == "text_only"
    assert sounds == []  # 소리는 나가지 않음
