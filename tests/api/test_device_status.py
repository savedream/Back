"""기기 상태 (명세서 GET /api/devices, 계획 PR③ AC-D4·D5): 주기 갱신과 바뀐 부분 status.changed.

남은 디스크 공간은 시험 중에도 바뀌므로 shutil.disk_usage를 바꿔 끼운다. 주기(device_status_interval_s 5초)는 엔진 시계
(FakeClock)로 넘긴다.
"""
import asyncio
import shutil
from types import SimpleNamespace

import pytest

from app.core.readiness import Component
from app.services.procedure_engine.inputs import DeviceResultInput, MicInput
from tests.scenario.kit import Kit

MB = 1024 * 1024
# 명세서 GET /api/devices 응답 예시의 키 순서
SPEC_KEYS = ["gps", "microphone", "speaker", "buzzer", "metronome", "rescue_signal", "timer", "models", "manual_db",
             "battery", "storage", "clock"]
SOUND_KEYS = ["object", "name", "connected", "status", "held_by", "queued", "last_result", "last_error", "detail"]


@pytest.fixture
def disk(monkeypatch) -> dict:
    """남은 디스크 공간(바이트). 시험이 바꾸면 다음 갱신에서 읽는다."""
    space = {"free": 2048 * MB}
    monkeypatch.setattr(shutil, "disk_usage", lambda path: SimpleNamespace(total=0, used=0, free=space["free"]))
    return space


@pytest.fixture
async def kit(disk):
    async with Kit() as k:
        yield k


async def refreshed(kit: Kit, seconds: float = 5) -> list[dict]:
    """시계를 넘겨 주기 갱신이 돌게 한 뒤 그사이 나온 status.changed data."""
    mark = len(kit.events)
    kit.clock.advance(seconds)
    for _ in range(20):
        await asyncio.sleep(0)
    await kit.settle()
    return [e.data for e in kit.events[mark:] if e.type == "status.changed"]


async def test_refresh_sends_only_changed_parts(kit, disk):
    """5초마다 다시 읽고 바뀐 부분만 status.changed로 보낸다. 바뀐 것이 없으면 보내지 않는다."""
    assert await refreshed(kit, 4) == []  # 아직 주기 전
    assert await refreshed(kit, 1) == []  # 바뀐 것이 없음
    kit.parts.gps.lose_fix()
    disk["free"] = 1000 * MB + 5
    assert await refreshed(kit) == [{"gps": {"fix": False, "accuracy_m": None, "satellites": None},
                                     "storage": {"free_mb": 1000}}]
    assert await refreshed(kit) == []
    kit.parts.gps.present = False  # GPS 장치가 빠짐
    assert await refreshed(kit) == [{"gps": {"connected": False}}]


async def test_device_disconnect_sends_one_status_changed(kit):
    """장치 연결 변화(엔진 상태에서 계산)도 바뀐 부분으로 한 번 나간다."""
    await kit.parts.engine.submit(DeviceResultInput(action_id=None, object=4, status="disconnected"))
    assert await refreshed(kit) == [{"speaker": {"connected": False}}]
    assert await refreshed(kit) == []
    await kit.parts.engine.submit(MicInput(connected=False))
    await kit.parts.engine.submit(DeviceResultInput(action_id=None, object=2, status="disconnected"))
    assert await refreshed(kit) == [{"microphone": {"connected": False}, "buzzer": {"connected": False}}]


async def test_volume_and_unsaved_records_are_not_sent_again(kit):
    """음량(엔진)과 미저장 기록 수(기록 저장소)는 바뀔 때 이미 보내므로 주기 갱신이 다시 보내지 않는다."""
    mark = len(kit.events)
    assert (await kit.http.post("/api/volume/up")).status_code == 200
    kit.parts.records.fail_next_save("user_utterance")
    kit.parts.records.fail_next_save("user_utterance")  # 주기 동안 한 번 다시 저장해도 남는다(건수 그대로)
    await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    changed = [e.data for e in kit.events[mark:] if e.type == "status.changed"]
    assert changed == [{"speaker": {"volume": 80}}, {"storage": {"unsaved_records": 1}}]
    assert await refreshed(kit) == []


async def test_models_loaded_later_are_sent(disk):
    """준비 전에도 갱신한다. 모델 적재가 끝나면 그 부분만 나간다(준비 상태 Readiness)."""
    async with Kit(fake_auto_ready=False) as kit:
        assert kit.parts.engine.state is None
        kit.app.state.readiness.mark_ready(Component.VAD)
        kit.app.state.readiness.mark_ready(Component.LLM)
        assert await refreshed(kit) == [{"models": {"llm": {"loaded": True}, "vad": {"loaded": True}}}]


async def test_refresher_stops_with_app(disk):
    async with Kit() as kit:
        task = kit.app.state.device_status._task
        assert not task.done()
    assert task.cancelled()


async def get_devices(kit: Kit) -> dict:
    res = await kit.http.get("/api/devices")
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True
    return res.json()["data"]


async def test_devices_shape_matches_spec(kit):
    """명세 예시 모양 그대로. 가짜에서 모르는 값(배터리, TTS 문장 캐시, 모델·캐시 용량)은 null(계획 기본값 8)."""
    data = await get_devices(kit)
    assert list(data) == SPEC_KEYS
    assert data["gps"] == {"connected": True, "fix": True, "accuracy_m": 8, "satellites": 7}
    assert data["microphone"] == {"connected": True, "state": "armed"}
    assert data["speaker"] == {"connected": True, "volume": 70} and data["buzzer"] == {"connected": True}
    metronome, rescue = data["metronome"], data["rescue_signal"]
    assert list(metronome) == SOUND_KEYS and list(rescue) == SOUND_KEYS  # 엔진 내부 칸(want)은 뺀다
    assert (metronome["object"], metronome["status"], metronome["detail"]) == (2, "off", {"bpm": 110})
    assert (rescue["object"], rescue["status"], rescue["detail"]) == (4, "off", {"output": "speaker"})
    assert data["timer"] == {"object": 3, "status": "idle", "elapsed_s": 0, "started_at": None}
    assert data["models"] == {"llm": {"loaded": True, "warmed": True, "version": "v2.2"}, "vad": {"loaded": True},
                              "stt": {"loaded": True}, "tts": {"loaded": True, "cached_sentences": None}}
    assert data["manual_db"] == {"loaded": True, "version": kit.parts.engine.db.version}
    assert data["battery"] is None
    size = kit.app.state.settings.manual_db_path.stat().st_size
    assert data["storage"] == {"unsaved_records": 0,
                               "data_mb": {"manual_db": round(size / MB, 2), "models": None, "tts_cache": None},
                               "free_mb": 2048}
    assert data["clock"] == {"source": "unsynced"}


async def test_engine_parts_are_current_and_device_parts_are_refreshed(kit, disk):
    """엔진 상태 칸(소리 기능·타이머·음량·미저장 기록 수)은 요청 때 지금 값이고, 장치·시스템 칸은 다음 갱신까지 그대로다."""
    await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    await kit.say("아니요 대답 없어요")
    kit.parts.records.fail_next_save("user_utterance")
    await kit.say("아니요")  # 가슴압박 대기: 메트로놈 켜짐, 타이머 돎
    assert (await kit.http.post("/api/rescue-signal/on")).status_code == 200
    await kit.settle()
    kit.clock.advance(3)
    kit.parts.gps.lose_fix()
    disk["free"] = 100 * MB
    data = await get_devices(kit)
    assert data["metronome"]["status"] == "on" and data["rescue_signal"]["status"] == "on"
    assert data["timer"]["status"] == "running" and data["timer"]["elapsed_s"] == 3
    assert data["microphone"] == {"connected": True, "state": "blocked"}  # 구조신호가 울리는 동안
    assert data["storage"]["unsaved_records"] == 1
    assert data["gps"]["fix"] is True and data["storage"]["free_mb"] == 2048  # 아직 갱신 전
    await refreshed(kit, 2)
    data = await get_devices(kit)
    assert data["gps"]["fix"] is False and data["storage"]["free_mb"] == 100


async def test_devices_before_ready(app, client):
    """준비 전(엔진 시작 전)에도 기본 상태로 200. 모델은 적재 전, 매뉴얼 DB는 적재됨, 마이크는 blocked."""
    res = client.get("/api/devices")
    assert res.status_code == 200
    data = res.json()["data"]
    assert list(data) == SPEC_KEYS and app.state.parts.engine.state is None
    assert data["microphone"] == {"connected": True, "state": "blocked"}
    assert data["models"] == {"llm": {"loaded": False, "warmed": False, "version": "v2.2"},
                              "vad": {"loaded": False}, "stt": {"loaded": False},
                              "tts": {"loaded": False, "cached_sentences": None}}
    assert data["manual_db"]["loaded"] is True and data["battery"] is None
    assert data["metronome"]["status"] == "off" and "want" not in data["rescue_signal"]
    assert data["timer"] == {"object": 3, "status": "idle", "elapsed_s": 0, "started_at": None}
