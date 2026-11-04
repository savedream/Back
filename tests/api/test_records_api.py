"""처치 기록 API (명세서 "처치 기록"): POST /api/records/retry."""
import pytest

from tests.scenario.kit import Kit


@pytest.fixture
async def kit():
    async with Kit() as k:
        yield k


async def retry(kit: Kit):
    res = await kit.http.post("/api/records/retry")
    kit.drain()
    return res


def storage_events(kit: Kit) -> list[dict]:
    return [e.data for e in kit.events if e.type == "status.changed"]


async def test_retry_saves_unsaved_records(kit):
    kit.parts.records.fail_next_save("user_utterance")
    await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    assert len(kit.parts.records.unsaved()) == 1
    mark = len(storage_events(kit))
    res = await retry(kit)
    assert res.status_code == 200
    assert res.json() == {"ok": True, "data": {"saved": 1, "unsaved": 0}}
    assert kit.parts.records.unsaved() == [] and all(r.saved for r in kit.records)
    assert storage_events(kit)[mark:][-1] == {"storage": {"unsaved_records": 0}}


async def test_retry_reports_status_even_when_nothing_to_save(kit):
    mark = len(storage_events(kit))
    res = await retry(kit)
    assert res.json()["data"] == {"saved": 0, "unsaved": 0}
    assert storage_events(kit)[mark:] == [{"storage": {"unsaved_records": 0}}]


async def test_retry_fails_when_records_still_unsaved(kit):
    kit.parts.records.fail_next_save("user_utterance")
    await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    kit.parts.records.fail_next_save()
    mark = len(storage_events(kit))
    res = await retry(kit)
    assert res.status_code == 500
    assert res.json() == {"ok": False, "error": {"code": "SAVE_FAILED", "message": "기록 1건을 저장하지 못했습니다"}}
    assert storage_events(kit)[mark:] == [{"storage": {"unsaved_records": 1}}]


def test_retry_works_before_ready(client):
    res = client.post("/api/records/retry")
    assert res.status_code == 200 and res.json()["data"] == {"saved": 0, "unsaved": 0}
