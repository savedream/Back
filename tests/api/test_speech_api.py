"""말하기 버튼 API (명세서 "말하기" POST /api/speech/start·stop·restart, 계획 PR② AC-S2~S5·S7·S8).

가짜 녹음기는 소리 없이 녹음 구간만 재고, 가짜 STT는 설정 문장(fake_stt_text) 또는 시험이 queue에 넣은 문장을 알아듣는다.
최대 녹음 시간(max_utterance_s 15초)은 엔진 시계(FakeClock)로 넘긴다.
"""
import asyncio

import pytest

from app.core import turn as turn_module
from app.services.procedure_engine.inputs import MicInput
from tests.scenario.kit import Kit, ids
from tests.unit.shell_kit import ScriptSpeaker, until_speaking

FALLEN = "등산하다 넘어졌는데 다리를 움직이기 어려워요"  # 가짜 STT 기본 문장
NOT_LISTENING = {"code": "NOT_LISTENING", "message": "녹음 중이 아닙니다"}


@pytest.fixture
async def kit():
    async with Kit() as k:
        yield k


async def start(kit: Kit) -> str:
    res = await kit.http.post("/api/speech/start")
    assert res.status_code == 200, res.text
    return res.json()["data"]["turn_id"]


async def stop(kit: Kit) -> None:
    res = await kit.http.post("/api/speech/stop")
    assert res.status_code == 202, res.text


async def button_turn(kit: Kit) -> str:
    turn_id = await start(kit)
    await stop(kit)
    await kit.settle()
    return turn_id


def speech_states(kit: Kit, mark: int = 0) -> list[dict]:
    return [e.data for e in kit.events[mark:] if e.type == "speech.state"]


def hold_stt(kit: Kit) -> asyncio.Event:
    """gate를 열 때까지 가짜 STT가 끝나지 않게 한다(처리 중 시험용)."""
    gate = asyncio.Event()
    transcribe = kit.parts.stt.transcribe

    async def held(segment):
        await gate.wait()
        return await transcribe(segment)

    kit.parts.stt.transcribe = held
    return gate


def assert_back_to_listening(kit: Kit) -> None:
    """처리가 끝나면(실패해도) 듣기 상태로 돌아오고 턴 잠금·녹음 중·말소리 받는 중 표시가 남지 않는다."""
    assert speech_states(kit)[-1] == {"state": "armed"}
    assert not kit.app.state.turn_lock.busy and not kit.state.capturing and not kit.state.speech_active


async def test_start_records_and_sends_capturing(kit):
    mark = len(kit.events)
    res = await kit.http.post("/api/speech/start")
    assert res.status_code == 200
    data = res.json()["data"]
    assert list(data) == ["turn_id", "state"] and data["state"] == "capturing" and data["turn_id"].startswith("t_")
    await kit.settle()
    assert speech_states(kit, mark) == [{"state": "capturing", "trigger": "button"}]
    assert kit.parts.recorder.recording == data["turn_id"] and kit.app.state.turn_lock.busy
    assert kit.state.capturing and kit.state.speech_active  # 녹음 동안 무응답·대기 타이머를 멈춘다


async def test_start_while_capturing_or_processing_is_409(kit):
    busy = {"code": "AUDIO_BUSY", "message": "이미 듣거나 처리 중입니다"}
    await start(kit)
    res = await kit.http.post("/api/speech/start")
    assert res.status_code == 409 and res.json()["error"] == busy
    assert (await kit.post("네")).status_code == 409  # 녹음 중에는 문장 턴도 받지 않는다(턴은 한 번에 하나)
    gate = hold_stt(kit)
    await stop(kit)
    res = await kit.http.post("/api/speech/start")  # 처리 중
    assert res.status_code == 409 and res.json()["error"] == busy
    gate.set()
    await kit.settle()
    assert (await kit.http.post("/api/speech/start")).status_code == 200


async def test_start_while_text_turn_is_processing_is_409(kit):
    gate = asyncio.Event()
    run = kit.model.run

    async def held(text: str) -> str:
        await gate.wait()
        return await run(text)

    kit.model.run = held
    first = asyncio.create_task(kit.post(FALLEN))
    for _ in range(1000):
        if kit.app.state.turn_lock.busy:
            break
        await asyncio.sleep(0)
    res = await kit.http.post("/api/speech/start")
    assert res.status_code == 409 and res.json()["error"]["code"] == "AUDIO_BUSY"
    gate.set()
    assert (await first).status_code == 200


@pytest.mark.parametrize("where", ["recorder", "engine"])
async def test_start_without_mic_is_503(kit, where):
    if where == "recorder":
        kit.parts.recorder.connected = False
    else:
        await kit.parts.engine.submit(MicInput(connected=False))
    res = await kit.http.post("/api/speech/start")
    assert res.status_code == 503
    assert res.json()["error"] == {"code": "MIC_UNAVAILABLE", "message": "마이크가 연결되지 않았습니다"}
    assert not kit.app.state.turn_lock.busy and kit.parts.recorder.started == []


async def test_start_while_reading_cuts_guidance(kit):
    """읽던 안내를 끊고(barge-in, 6-2) 아직 다 읽지 않은 첫 단계로 되돌린다. 끊긴 뒤에도 capturing이 덮이지 않는다."""
    speaker = ScriptSpeaker()
    kit.parts.engine.sequencer._speaker = speaker
    speaker.hold()
    await kit.say(FALLEN, speak=True, settle=False)
    await until_speaking(speaker)  # EM-03 들어가는 말을 읽는 중
    await start(kit)
    await kit.settle()
    played = [(r.utterance.manual_id, r.utterance.step_id, r.played) for r in kit.records if r.kind == "kit_guidance"]
    assert played[-2:] == [("EM-03", None, "cut"), ("EM-03", "EM03-01", "cut")]
    assert kit.state.step_id == "EM03-01" and not kit.parts.engine.sequencer.reading
    assert speech_states(kit)[-1] == {"state": "capturing", "trigger": "button"}
    speaker.release()
    await stop(kit)
    await kit.settle()
    assert [u.step_id for u in speaker.spoken][-1] == "EM03-01"  # 처리 뒤 끊긴 단계부터 다시 읽는다


async def test_start_while_mic_paused(kit):
    """마이크 일시정지는 상시 듣기만 멈춘다. 버튼 녹음은 되고, 끝나면 다시 일시정지 상태로 돌아온다."""
    await kit.parts.engine.submit(MicInput(paused=True))
    await kit.settle()
    await button_turn(kit)
    assert any(e.type == "speech.final" for e in kit.events)
    assert speech_states(kit)[-1] == {"state": "paused"} and kit.state.mic.paused


async def test_rescue_signal_held_while_recording(kit):
    """구조신호 중에도 버튼 녹음은 된다. 녹음 동안 구조신호를 멈추고(held_by recording, 음소거 관문) 끝나면 다시 울린다."""
    devices = kit.parts.devices
    assert (await kit.http.post("/api/rescue-signal/on")).status_code == 200
    await kit.settle()
    assert devices.on == {4} and devices.held == set()
    await start(kit)
    await kit.settle()
    assert kit.state.devices["rescue_signal"].held_by == "recording" and devices.held == {4}
    await stop(kit)
    await kit.settle()
    assert any(e.type == "speech.final" for e in kit.events)
    assert kit.state.devices["rescue_signal"].held_by is None and devices.held == set() and devices.on == {4}
    assert speech_states(kit)[-1] == {"state": "blocked", "reason": "rescue_signal"}


async def test_stop_processes_like_utterance_with_events_in_order(kit):
    """말하기 완료: 바로 202, 처리는 문장 턴과 같은 경로(trigger button). 이벤트 capturing → processing → speech.final →
    turn.result, 알아들은 문장의 record_id는 그 턴의 user_utterance 기록이다."""
    mark = len(kit.events)
    turn_id = await start(kit)
    res = await kit.http.post("/api/speech/stop")
    assert res.status_code == 202 and res.json()["data"] == {"turn_id": turn_id, "state": "processing"}
    await kit.settle()
    kinds = [(e.type, e.data.get("state")) for e in kit.events[mark:]
             if e.type in ("speech.state", "speech.final", "turn.result")]
    assert kinds[:4] == [("speech.state", "capturing"), ("speech.state", "processing"), ("speech.final", None),
                         ("turn.result", None)]
    final = next(e for e in kit.events[mark:] if e.type == "speech.final")
    user = next(r for r in kit.records if r.kind == "user_utterance")
    assert final.turn_id == turn_id and user.turn_id == turn_id
    assert final.data == {"text": FALLEN, "normalized": FALLEN, "record_id": user.record_id}
    assert ids(kit.spoken[-2:]) == [("intro", "EM-03"), ("step", "EM03-01")]
    latency = next(e.data for e in kit.events[mark:] if e.type == "turn.latency")
    assert isinstance(latency["stt"], int) and latency["vad_tail"] is None
    assert_back_to_listening(kit)


async def test_stop_or_restart_when_not_capturing_is_409(kit):
    for path in ("/api/speech/stop", "/api/speech/restart"):
        res = await kit.http.post(path)
        assert res.status_code == 409 and res.json()["error"] == NOT_LISTENING
    gate = hold_stt(kit)
    await start(kit)
    await stop(kit)
    for path in ("/api/speech/stop", "/api/speech/restart"):  # 처리 중
        res = await kit.http.post(path)
        assert res.status_code == 409 and res.json()["error"] == NOT_LISTENING
    gate.set()
    await kit.settle()
    assert len(kit.parts.stt.segments) == 1


@pytest.mark.parametrize("failure, code", [("empty", "STT_EMPTY"), ("error", "STT_FAILED")])
async def test_stt_failure_only_notices_and_restores(kit, failure, code):
    """인식 실패(6-4): 말하지 않고 ui.notice만, 기록·speech.final 없음. 듣기 상태·턴 잠금은 되돌리고 무응답을 다시 잰다."""
    await kit.say(FALLEN)  # EM03-01 질문
    if failure == "empty":
        kit.parts.stt.queue.append("")
    else:
        async def broken(segment):
            raise RuntimeError("시험용 STT 오류")

        kit.parts.stt.transcribe = broken
    mark, spoken, records = len(kit.events), len(kit.spoken), len(kit.records)
    await button_turn(kit)
    assert [e.data["code"] for e in kit.events[mark:] if e.type == "ui.notice"] == [code]
    assert not any(e.type == "speech.final" for e in kit.events[mark:])
    assert kit.spoken_since(spoken) == [] and [r.kind for r in kit.records[records:]] == []
    assert_back_to_listening(kit)
    assert kit.state.no_speech.deadline is not None and kit.state.no_speech.count == 0


async def test_model_error_reasks_and_restores(kit):
    """모델이 실패해도(MODEL_FAILED) 알아들은 문장은 내고 T_RETRY로 다시 묻는다."""
    await kit.say(FALLEN)

    async def broken(text: str) -> str:
        raise RuntimeError("시험용 모델 오류")

    kit.model.run = broken
    mark = len(kit.spoken)
    await button_turn(kit)
    assert ids(kit.spoken_since(mark)) == [("template", "T_RETRY"), ("step", "EM03-01")]
    assert [e.data["text"] for e in kit.events if e.type == "speech.final"] == [FALLEN]
    assert_back_to_listening(kit)


async def test_failed_processing_restores_listening_and_lock(kit, monkeypatch):
    async def broken(*args):
        raise RuntimeError("시험용 오류")

    monkeypatch.setattr(turn_module, "interpret", broken)
    await button_turn(kit)
    assert_back_to_listening(kit)
    monkeypatch.undo()
    await button_turn(kit)  # 다음 녹음은 그대로 된다
    assert kit.state.step_id == "EM03-01"


async def test_restart_discards_and_records_again(kit):
    """다시 말하기: 같은 turn_id로 처음부터 다시 녹음한다. 버린 녹음은 인식하지 않고 기록(GET /api/session/log)에도 없다."""
    recorder, stt = kit.parts.recorder, kit.parts.stt
    stt.queue.append("사람이 쓰러졌는데 숨을 안 쉬어")
    turn_id = await start(kit)
    await kit.settle()
    mark = len(kit.events)
    res = await kit.http.post("/api/speech/restart")
    assert res.status_code == 200 and res.json()["data"] == {"turn_id": turn_id, "state": "capturing"}
    assert recorder.discarded == 1 and recorder.started == [turn_id, turn_id] and stt.segments == []
    await kit.settle()
    assert speech_states(kit, mark) == [] and kit.state.capturing  # 녹음 중은 그대로
    await stop(kit)
    await kit.settle()
    assert len(stt.segments) == 1 and kit.state.step_id == "EM01-01"
    log = (await kit.http.get("/api/session/log")).json()["data"]["records"]
    users = [(r["turn_id"], r["text"]) for r in log if r["kind"] == "user_utterance"]
    assert users == [(turn_id, "사람이 쓰러졌는데 숨을 안 쉬어")]


async def test_max_recording_time_ends_like_stop(kit):
    """버튼 녹음도 최대 녹음 시간(15초)에 닿으면 말하기 완료와 같이 처리한다(① audio_input). 그 뒤 stop은 409."""
    mark = len(kit.events)
    turn_id = await start(kit)
    await kit.advance(14)
    assert kit.parts.stt.segments == [] and kit.state.capturing
    await kit.advance(1)
    assert [s["state"] for s in speech_states(kit, mark)][:2] == ["capturing", "processing"]
    assert [e.turn_id for e in kit.events[mark:] if e.type == "speech.final"] == [turn_id]
    res = await kit.http.post("/api/speech/stop")
    assert res.status_code == 409 and res.json()["error"] == NOT_LISTENING
    assert len(kit.parts.stt.segments) == 1
    assert_back_to_listening(kit)


async def test_stop_at_max_recording_time_processes_once(kit):
    """최대 녹음 시간이 지난 순간 말하기 완료가 같이 와도 처리는 한 번이다(generation)."""
    speech = kit.app.state.speech
    turn_id = await start(kit)
    kit.clock.advance(15)  # 만료 태스크가 깨어나기 전에
    assert speech.stop() == turn_id
    await kit.settle()
    assert len(kit.parts.stt.segments) == 1
    assert [e.turn_id for e in kit.events if e.type == "speech.final"] == [turn_id]
    assert [r.turn_id for r in kit.records if r.kind == "user_utterance"] == [turn_id]


async def test_restart_restarts_max_recording_time(kit):
    """다시 말하기는 최대 녹음 시간을 처음부터 다시 잰다. 만료와 같은 순간이어도 새 녹음은 이어진다."""
    speech = kit.app.state.speech
    await start(kit)
    await kit.advance(10)
    first = speech.generation
    assert (await kit.http.post("/api/speech/restart")).status_code == 200
    speech._finish(first, 0.0)  # 앞 녹음의 만료가 늦게 와도 지금 녹음은 끝내지 않는다(generation)
    assert speech.turn_id is not None and speech.generation == first + 1
    await kit.advance(14)  # 처음 녹음부터 24초
    assert kit.parts.stt.segments == [] and speech.turn_id is not None
    kit.clock.advance(1)  # 다시 녹음한 지 15초. 만료 태스크가 깨어나기 전에 다시 말하기
    speech.restart()
    await kit.settle()
    assert kit.parts.stt.segments == [] and speech.turn_id is not None
    await kit.advance(15)
    assert len(kit.parts.stt.segments) == 1 and speech.turn_id is None


@pytest.mark.parametrize("path", ["/api/speech/start", "/api/speech/stop", "/api/speech/restart"])
def test_not_ready_is_503(client, path):
    res = client.post(path)
    assert res.status_code == 503 and res.json()["error"]["code"] == "NOT_READY"


@pytest.mark.parametrize("when", ["recording", "processing"])
async def test_app_shutdown_cancels_recording_and_processing(when):
    kit = Kit()
    await kit.__aenter__()
    speech = kit.app.state.speech
    hold_stt(kit)
    await start(kit)
    if when == "processing":
        await stop(kit)
        for _ in range(10):
            await asyncio.sleep(0)
        task = speech._processing
    else:
        task = speech._expiry
    assert not task.done()
    await kit.__aexit__(None, None, None)
    assert task.cancelled() and not kit.app.state.turn_lock.busy
    assert kit.parts.recorder.recording is None
