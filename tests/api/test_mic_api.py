"""마이크 API (명세서 "말하기" POST /api/mic/pause·resume, 계획 PR② AC-S6·S7)."""
import pytest

from app.services.procedure_engine.inputs import MicInput
from tests.scenario.kit import Kit, ids
from tests.unit.shell_kit import ScriptSpeaker, until_speaking
from tests.unit.test_snapshot_resume import relaunch

FALLEN = "등산하다 넘어졌는데 다리를 움직이기 어려워요"


@pytest.fixture
async def kit():
    async with Kit() as k:
        yield k


async def press(kit: Kit, path: str) -> dict:
    res = await kit.http.post(path)
    assert res.status_code == 200, res.text
    return res.json()["data"]


def speech_states(kit: Kit, mark: int = 0) -> list[dict]:
    return [e.data for e in kit.events[mark:] if e.type == "speech.state"]


async def test_pause_and_resume_even_if_already_so(kit):
    mark = len(kit.events)
    assert await press(kit, "/api/mic/pause") == {"state": "paused"}
    assert await press(kit, "/api/mic/pause") == {"state": "paused"}  # 같은 상태여도 200
    assert kit.state.mic.paused
    assert await press(kit, "/api/mic/resume") == {"state": "armed"}
    assert await press(kit, "/api/mic/resume") == {"state": "armed"}
    kit.drain()
    assert speech_states(kit, mark) == [{"state": "paused"}, {"state": "armed"}] and not kit.state.mic.paused


async def test_pause_while_reading_keeps_speaking_until_done(kit):
    """읽는 중이면 speech.state는 그대로 두고, 안내가 끝나면 일시정지 상태로 바뀐다(안내 음성으로 풀리지 않음)."""
    speaker = ScriptSpeaker()
    kit.parts.engine.sequencer._speaker = speaker
    speaker.hold()
    await kit.say(FALLEN, speak=True, settle=False)
    await until_speaking(speaker)
    kit.drain()
    mark = len(kit.events)
    assert await press(kit, "/api/mic/pause") == {"state": "paused"}
    kit.drain()
    assert speech_states(kit, mark) == []
    speaker.release()
    await kit.settle()
    assert speech_states(kit)[-1] == {"state": "paused"} and kit.state.mic.paused


async def test_pause_stops_no_speech_timer(kit):
    """일시정지 중에는 질문 단계 무응답 타이머가 멈추고, 다시 시작하면 처음부터 잰다(6-9)."""
    await kit.say(FALLEN)  # EM03-01 질문
    await press(kit, "/api/mic/pause")
    assert kit.state.no_speech.deadline is None
    assert await kit.advance(30) == []
    await press(kit, "/api/mic/resume")
    assert await kit.advance(14) == []
    assert ids(await kit.advance(1)) == [("template", "T_NO_SPEECH"), ("step", "EM03-01")]


async def test_pause_survives_reconnect_and_guidance(kit):
    """사용자 설정이라 마이크 재연결·안내 음성으로 풀리지 않는다. 마이크가 없어도 설정은 저장한다."""
    await kit.parts.engine.submit(MicInput(connected=False))
    assert await press(kit, "/api/mic/pause") == {"state": "unavailable"}
    await kit.parts.engine.submit(MicInput(connected=True))
    await kit.say(FALLEN, speak=True)
    assert kit.state.mic.paused and speech_states(kit)[-1] == {"state": "paused"}


async def test_pause_survives_snapshot_resume(tmp_path):
    """재시작 이어가기(6-14)에도 일시정지가 그대로다."""
    async with relaunch(tmp_path, 0) as kit:
        await kit.say(FALLEN)
        await press(kit, "/api/mic/pause")
    async with relaunch(tmp_path, 10) as kit:
        assert ids(kit.spoken)[0] == ("template", "T_RESUME")
        assert kit.state.mic.paused and speech_states(kit)[-1] == {"state": "paused"}
        assert (await kit.http.get("/api/session")).json()["data"]["mic"] == "paused"


@pytest.mark.parametrize("path", ["/api/mic/pause", "/api/mic/resume"])
def test_not_ready_is_503(client, path):
    res = client.post(path)
    assert res.status_code == 503 and res.json()["error"]["code"] == "NOT_READY"
