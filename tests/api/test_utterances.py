"""POST /api/utterances: 응답 모양, 400, 503, 409, 이벤트 (명세서 "[시험] 문장으로 한 턴 처리(내부 API)")."""
import asyncio
import time

import pytest

from app.core import turn as turn_module
from tests.scenario.kit import Kit, ids


async def until(check, limit: int = 1000) -> None:
    for _ in range(limit):
        if check():
            return
        await asyncio.sleep(0)
    raise AssertionError("기다려도 되지 않음")


def hold_model(kit: Kit) -> asyncio.Event:
    gate = asyncio.Event()
    run = kit.model.run

    async def held(text: str) -> str:
        await gate.wait()
        return await run(text)

    kit.model.run = held
    return gate


@pytest.fixture
async def kit():
    async with Kit() as k:
        yield k


async def test_response_shape_matches_spec(kit):
    res = await kit.post("사람이 쓰러졌는데 숨을 안 쉬어", speak=True)
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    data = body["data"]
    assert set(data) == {"turn_id", "normalized", "raw", "function_call", "error_code", "say", "step_id",
                         "latency_ms"}
    assert data["turn_id"] == "t_0001" and data["normalized"] == "사람이 쓰러졌는데 숨을 안 쉬어"
    assert data["raw"].startswith("<maum_0>(situation=1") and data["function_call"]["func"] == 0
    assert data["error_code"] is None and data["step_id"] == "EM01-01"
    assert ids(data["say"]) == [("intro", "EM-01"), ("step", "EM01-01")]
    assert set(data["say"][1]) == {"utterance_id", "role", "text", "screen", "source", "manual_id", "step_id",
                                   "template_id", "speed", "audio"}
    assert set(data["latency_ms"]) == {"normalize", "model", "parse", "engine"}


async def test_parse_error_is_200_with_error_code(kit):
    await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    data = await kit.say("어 그게 아니고")
    assert data["error_code"] == "MODEL_PARSE_ERROR" and data["function_call"] is None and data["raw"] == "<maum_9>"
    assert ids(data["say"]) == [("template", "T_RETRY"), ("step", "EM01-01")]


async def test_missing_text_is_400(kit):
    res = await kit.http.post("/api/utterances", json={"speak": False})
    assert res.status_code == 400 and res.json()["error"]["code"] == "INVALID_REQUEST"


async def test_second_turn_while_first_is_processing_is_409(kit):
    gate = asyncio.Event()
    run = kit.model.run

    async def held(text: str) -> str:
        await gate.wait()
        return await run(text)

    kit.model.run = held
    first = asyncio.create_task(kit.post("등산하다 넘어졌는데 다리를 움직이기 어려워요"))
    await until(lambda: kit.app.state.turn_lock.busy)
    second = await kit.post("사람이 쓰러졌는데 숨을 안 쉬어")
    assert second.status_code == 409
    assert second.json()["error"] == {"code": "AUDIO_BUSY", "message": "다른 턴을 처리 중입니다"}
    gate.set()
    assert (await first).status_code == 200
    assert kit.state.step_id == "EM03-01"


async def test_events_for_a_text_turn(kit):
    mark = len(kit.events)
    await kit.say("등산하다 넘어졌는데 다리를 움직이기 어려워요", speak=True)
    def label(e):
        if e.type == "speech.state":
            return e.data["state"]
        if e.type in ("guide.step", "guide.say"):
            return e.data.get("template_id") or e.data["step_id"] or e.data["manual_id"]
        return None

    kinds = [(e.type, label(e)) for e in kit.events[mark:]
             if e.type in ("speech.state", "turn.result", "session.updated", "turn.latency", "guide.step", "guide.say")]
    assert kinds == [("speech.state", "processing"), ("turn.result", None), ("session.updated", None),
                     ("guide.say", "EM-03"), ("speech.state", "speaking"), ("turn.latency", None),
                     ("guide.step", "EM03-01"), ("guide.say", "EM03-01"), ("speech.state", "armed")]
    latency = next(e for e in kit.events[mark:] if e.type == "turn.latency")
    assert latency.turn_id == "t_0001"
    assert set(latency.data) == {"vad_tail", "stt", "normalize", "model", "parse", "engine", "tts_first", "e2e"}
    assert latency.data["vad_tail"] is None and latency.data["stt"] is None


async def test_turn_without_speech_goes_back_to_listening(kit):
    mark = len(kit.events)
    await kit.say("오늘 날씨 좋네")  # 안내 전 잡담: 말하지 않고 화면 알림만
    states = [e.data["state"] for e in kit.events[mark:] if e.type == "speech.state"]
    assert states == ["processing", "armed"]


def test_not_ready_is_503(client):
    res = client.post("/api/utterances", json={"text": "네"})
    assert res.status_code == 503 and res.json()["error"]["code"] == "NOT_READY"


async def test_timers_pause_while_text_turn_is_processed(kit):
    await kit.say("등산하다 넘어졌는데 다리를 움직이기 어려워요")
    await kit.say("아니요 피는 안 나요")
    gate = hold_model(kit)
    reply = asyncio.create_task(kit.post("네 많이 휘었어요"))
    await until(lambda: kit.state.speech_active)
    mark = len(kit.spoken)
    kit.clock.advance(20)  # 모델이 처리하는 동안 무응답 15초가 지나도
    for _ in range(50):
        await asyncio.sleep(0)
    assert kit.spoken_since(mark) == []
    gate.set()
    data = (await reply).json()["data"]
    assert ids(data["say"]) == [("step", "EM03-05"), ("step", "EM03-06"), ("step", "EM03-07")]  # 답이 쓰인다


async def test_failed_turn_restores_listening(kit, monkeypatch):
    async def broken(*args):
        raise RuntimeError("시험용 오류")

    monkeypatch.setattr(turn_module, "interpret", broken)
    res = await kit.post("등산하다 넘어졌는데 다리를 움직이기 어려워요")
    assert res.status_code == 500 and res.json()["error"]["code"] == "INTERNAL_ERROR"
    await kit.settle()
    states = [e.data["state"] for e in kit.events if e.type == "speech.state"]
    assert states[-1] == "armed" and not kit.app.state.turn_lock.busy and kit.state.speech_active is False


async def test_silent_turn_while_reading_stays_speaking(kit):
    await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    await kit.say("아니요 대답 없어요")
    await kit.say("아니요")  # EM01-15 압박 대기
    gate = asyncio.Event()
    speak = kit.parts.speaker.speak

    async def held(*args):
        await gate.wait()
        return await speak(*args)

    kit.parts.speaker.speak = held
    await kit.say("여기 어디야", speak=True, settle=False)
    # 좌표를 읽는 중. 읽기 전에 낭독 시작 되먹임(held_by, A3)을 한 번 거치므로 speaking이 나올 때까지 기다린다
    await until(lambda: kit.parts.engine.sequencer.speech_state == "speaking")
    kit.drain()
    mark = len(kit.events)
    # 대기 단계 확인 응답이라 읽을 문장이 없음. speak: false는 앞 낭독이 끝나야 응답하므로 true로
    await kit.say("네", speak=True, settle=False)
    kit.drain()
    during = [e.data["state"] for e in kit.events[mark:] if e.type == "speech.state"]
    assert during == ["processing", "speaking"]  # 아직 읽는 중이라 '듣는 중'으로 바꾸지 않는다
    gate.set()
    await kit.settle()
    assert [e.data["state"] for e in kit.events if e.type == "speech.state"][-1] == "armed"


async def test_latency_counts_until_first_sound(kit):
    speak = kit.parts.speaker.speak

    async def slow_first_sound(utterance, volume, on_sound):
        await asyncio.sleep(0.05)  # 합성에 걸리는 시간
        return await speak(utterance, volume, on_sound)

    kit.parts.speaker.speak = slow_first_sound
    await kit.say("등산하다 넘어졌는데 다리를 움직이기 어려워요", speak=True, settle=False)
    await asyncio.sleep(0.2)  # 실제 시간으로 두 문장을 읽게 둔다
    await kit.settle()
    latency = next(e.data for e in kit.events if e.type == "turn.latency")
    assert latency["tts_first"] >= 50 and latency["e2e"] >= latency["tts_first"]


async def test_turn_takes_t0_and_earlier_latency(kit):
    """음성 턴은 STT 뒤에 같은 경로를 탄다. t0(말하기 완료 시각)와 앞서 잰 stt 시간을 넘기면 turn.latency에 쓴다."""
    t0 = time.perf_counter() - 1  # 1초 전에 말하기 완료를 누름
    reply = await turn_module.run_utterance(kit.parts, 5, "t_voice", "등산하다 넘어졌는데 다리를 움직이기 어려워요", True,
                                            started=t0, latency={"stt": 7})
    await kit.settle()
    assert reply.latency_ms["stt"] == 7 and set(reply.latency_ms) == {"stt", "normalize", "model", "parse", "engine"}
    latency = next(e for e in kit.events if e.type == "turn.latency")
    assert latency.turn_id == "t_voice" and latency.data["stt"] == 7 and latency.data["vad_tail"] is None
    assert latency.data["e2e"] >= 1000


async def test_voice_turn_goes_through_stt_then_same_path(kit):
    """음성 턴: STT 뒤 문장 턴과 같은 경로(trigger button, speech.final). 빈 문장이면 모델 없이 인식 실패(STT_EMPTY)."""
    recorder, stt = kit.parts.recorder, kit.parts.stt
    stt.queue.extend(["", "등산하다 넘어졌는데 다리를 움직이기 어려워요"])
    for turn_id in ("t_v1", "t_v2"):
        recorder.start(turn_id)
        await turn_module.run_voice_turn(kit.parts, 5, recorder.stop(), time.perf_counter())
        await kit.settle()
    assert kit.model.calls == 1
    assert [(e.type, e.turn_id) for e in kit.events if e.type in ("ui.notice", "speech.final")] == [
        ("ui.notice", "t_v1"), ("speech.final", "t_v2")]
    assert kit.state.step_id == "EM03-01"
