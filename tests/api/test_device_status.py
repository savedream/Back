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
