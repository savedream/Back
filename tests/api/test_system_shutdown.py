"""키트 끄기 (명세서 POST /api/system/shutdown, 계획 PR③ AC-D1~D3): 동결, 저장, 소리 기능 끄기, 종료 훅.

가짜 종료 훅은 꺼짐 요청(requested)만 표시하고 동결을 풀어 서버를 계속 쓸 수 있게 한다(사용자 결정).
"""
import asyncio

import httpx
import pytest

from tests.api.test_utterances import until
from tests.scenario.kit import Kit
from tests.unit.shell_kit import ScriptSpeaker, until_speaking

SHUTTING_DOWN = {"code": "NOT_READY", "message": "종료 중입니다"}
CONFIRM = {"confirm": True}
FALLEN = "등산하다 넘어졌는데 다리를 움직이기 어려워요"


@pytest.fixture
async def kit():
    async with Kit() as k:
        yield k


async def at_s5b(kit: Kit) -> None:
    """가슴압박 대기(EM01-15): 메트로놈 켜짐, 타이머 돎, 2분 알림."""
    await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    await kit.say("아니요 대답 없어요")
    await kit.say("아니요")


def track_hook(kit: Kit, order: list) -> None:
    """종료 훅이 불린 때를 order에 남긴다(가짜 훅은 그대로 부른다)."""
    hook = kit.parts.shutdown.shutdown

    async def tracked() -> None:
        order.append("hook")
        await hook()

    kit.parts.shutdown.shutdown = tracked


async def post_shutdown(kit: Kit, order: list) -> httpx.Response:
    """응답 본문을 다 보낸 때도 order에 남기며 종료를 요청한다."""
    async def app(scope, receive, send):
        async def tracked(message):
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body"):
                order.append("response")

        await kit.app(scope, receive, tracked)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://kit") as http:
        res = await http.post("/api/system/shutdown", json=CONFIRM)
    await kit.settle()
    return res


def saved_in_session(kit: Kit) -> list:
    return [r for r in kit.records if r.session_id == kit.state.session_id and r.saved]


@pytest.mark.parametrize("body", [None, {}, {"confirm": False}, {"confirm": "true"}])
async def test_confirm_required(kit, body):
    res = await kit.http.post("/api/system/shutdown", json=body)
    assert res.status_code == 400
    assert res.json()["error"] == {"code": "CONFIRM_REQUIRED", "message": "confirm: true가 필요합니다"}
    assert not kit.parts.engine.frozen and not kit.parts.shutdown.requested


async def test_shutdown_turns_sound_off_then_hook_after_response(kit):
    """성공: 기록을 저장하고 메트로놈·구조신호를 끈 뒤 200 {saved_records, shutting_down}. 종료 훅은 응답을 보낸 뒤 부르고,
    가짜 훅이 동결을 풀어 그 뒤에도 문장 턴이 된다(AC-D1·D3)."""
    await at_s5b(kit)
    assert (await kit.http.post("/api/rescue-signal/on")).status_code == 200
    await kit.settle()
    devices = kit.parts.devices
    assert devices.on == {2, 4}
    order = []
    track_hook(kit, order)
    res = await post_shutdown(kit, order)
    assert res.status_code == 200 and order == ["response", "hook"]
    data = res.json()["data"]
    assert list(data) == ["saved_records", "shutting_down"] and data["shutting_down"] is True
    assert devices.on == set() and {d.status for d in kit.state.devices.values()} == {"off"}
    # 꺼진 장치 결과 기록까지 저장한 뒤 센 수다
    offs = [r for r in kit.records if r.kind == "device_result" and r.type == 2 and r.source == "engine"]
    assert [r.object for r in offs] == [2, 4] and all(r.saved for r in offs)
    assert data["saved_records"] == len(saved_in_session(kit)) and kit.parts.records.unsaved() == []
    assert kit.parts.shutdown.requested and not kit.parts.engine.frozen
    assert (await kit.post("네")).status_code == 200


async def test_shutdown_while_reading_cuts_guidance_and_saves_it(kit):
    """읽던 안내는 끊고 끊긴 문장 기록(played: cut)까지 저장한 뒤 응답한다. 끈 뒤에 이어 읽지 않는다."""
    speaker = ScriptSpeaker()
    kit.parts.engine.sequencer._speaker = speaker
    speaker.hold()
    await kit.say(FALLEN, speak=True, settle=False)
    await until_speaking(speaker)  # EM-03 들어가는 말을 읽는 중
    res = await kit.http.post("/api/system/shutdown", json=CONFIRM)
    assert res.status_code == 200
    played = [(r.utterance.step_id or r.utterance.manual_id, r.played, r.saved) for r in kit.records
              if r.kind == "kit_guidance"]
    assert played[-2:] == [("EM-03", "cut", True), ("EM03-01", "cut", True)]
    assert res.json()["data"]["saved_records"] == len(saved_in_session(kit))
    speaker.release()
    await kit.settle()
    assert len(speaker.spoken) == 1 and not kit.parts.engine.sequencer.reading


async def test_frozen_until_sound_is_off(kit):
    """장치 응답을 기다리는 동안에도 동결이라 준비 필요 API는 503 "종료 중입니다"이고 종료 훅은 아직 부르지 않는다."""
    await at_s5b(kit)
    devices = kit.parts.devices
    devices.fail_next("timeout")  # 메트로놈 끄기에 응답하지 않는다(장치 응답 제한 시간 2초)
    mark = len(devices.commands)
    shutting = asyncio.create_task(kit.http.post("/api/system/shutdown", json=CONFIRM))
    await until(lambda: [(c.object, c.type) for c in devices.commands[mark:]] == [(2, 2)])
    res = await kit.http.post("/api/utterances", json={"text": "네", "speak": False})
    assert res.status_code == 503 and res.json()["error"] == SHUTTING_DOWN
    assert not shutting.done() and not kit.parts.shutdown.requested
    kit.clock.advance(2)
    res = await shutting
    assert res.status_code == 200 and kit.parts.shutdown.requested and not kit.parts.engine.frozen
    assert kit.state.devices["metronome"].last_result == "timeout"


async def test_save_failure_unfreezes_and_keeps_running(kit):
    """저장하지 못한 기록이 남으면 동결을 풀고 끄지 않으며 500 SAVE_FAILED(AC-D2)."""
    kit.parts.records.fail_next_save("user_utterance")
    await at_s5b(kit)
    assert len(kit.parts.records.unsaved()) == 1
    kit.parts.records.fail_next_save()  # 종료 전 다시 저장도 실패
    res = await kit.http.post("/api/system/shutdown", json=CONFIRM)
    assert res.status_code == 500
    assert res.json()["error"] == {"code": "SAVE_FAILED", "message": "기록 1건을 저장하지 못했습니다"}
    assert not kit.parts.engine.frozen and not kit.parts.shutdown.requested
    assert kit.parts.devices.on == {2} and kit.state.devices["metronome"].status == "on"
    assert (await kit.post("네")).status_code == 200


async def test_shutdown_discards_button_recording_and_keeps_mic_paused(kit):
    """녹음 중인 말하기 버튼 턴은 인식 없이 버리고 턴 잠금을 놓는다. 마이크 일시정지 설정은 바꾸지 않는다."""
    assert (await kit.http.post("/api/mic/pause")).status_code == 200
    assert (await kit.http.post("/api/speech/start")).status_code == 200
    await kit.settle()
    assert kit.state.capturing
    res = await kit.http.post("/api/system/shutdown", json=CONFIRM)
    assert res.status_code == 200
    await kit.settle()
    recorder = kit.parts.recorder
    assert recorder.discarded == 1 and recorder.recording is None and kit.parts.stt.segments == []
    assert not kit.app.state.turn_lock.busy and kit.app.state.speech.turn_id is None
    assert not kit.state.capturing and not kit.state.speech_active and kit.state.mic.paused
    assert not any(r.kind == "user_utterance" for r in kit.records)
    assert [e.data for e in kit.events if e.type == "speech.state"][-1] == {"state": "paused"}
    assert (await kit.http.post("/api/speech/start")).status_code == 200


def test_shutdown_before_ready(app, client):
    """준비 전(엔진 시작 전)에도 응답한다(503 없음). 끌 소리 기능·세션이 없어 saved_records는 0."""
    res = client.post("/api/system/shutdown", json=CONFIRM)
    assert res.status_code == 200 and res.json()["data"] == {"saved_records": 0, "shutting_down": True}
    parts = app.state.parts
    assert parts.shutdown.requested and not parts.engine.frozen and parts.engine.state is None


async def test_frozen_engine_refuses_ready_apis(kit):
    """동결 중에는 준비가 필요한 API가 503 NOT_READY "종료 중입니다"(계획 기본값 6). 조회 API는 그대로."""
    kit.parts.engine.freeze()
    for path, body in (("/api/utterances", {"text": "네", "speak": False}), ("/api/rescue-signal/on", None),
                       ("/api/speech/start", None)):
        res = await kit.http.post(path, json=body)
        assert res.status_code == 503 and res.json()["error"] == SHUTTING_DOWN, path
    assert (await kit.http.get("/api/session")).status_code == 200
    kit.parts.engine.unfreeze()
    assert (await kit.post("사람이 쓰러졌는데 숨을 안 쉬어")).status_code == 200
