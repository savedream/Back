"""위치 API (명세서 "위치"): GET /api/location."""
import pytest

from app.fakes.fake_gps import DEFAULT_FIX
from tests.scenario.kit import Kit

LOCATION_KEYS = ["fix", "lat", "lon", "accuracy_m", "satellites", "read_at", "age_s", "last_known"]


@pytest.fixture
async def kit():
    async with Kit() as k:
        yield k


async def get_location(kit: Kit) -> dict:
    res = await kit.http.get("/api/location")
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True
    return res.json()["data"]


async def test_location_with_fix(kit):
    data = await get_location(kit)
    assert list(data) == LOCATION_KEYS
    assert (data["fix"], data["lat"], data["lon"]) == (True, *DEFAULT_FIX)
    assert (data["accuracy_m"], data["satellites"], data["age_s"], data["last_known"]) == (8, 7, 0, None)
    assert data["read_at"] == "2026-10-13T14:00:00+09:00"


async def test_location_lost_fix_gives_last_known(kit):
    """위치를 못 잡았으면 오류가 아니라 fix: false와 마지막 위치(몇 초 전인지 포함)."""
    kit.parts.gps.lose_fix()
    await kit.advance(312)
    data = await get_location(kit)
    assert (data["fix"], data["lat"], data["lon"], data["read_at"], data["age_s"]) == (False, None, None, None, None)
    assert data["last_known"] == {"lat": DEFAULT_FIX[0], "lon": DEFAULT_FIX[1],
                                  "read_at": "2026-10-13T14:00:00+09:00", "age_s": 312}


async def test_location_without_gps_module_is_503(kit):
    kit.parts.gps.present = False
    res = await kit.http.get("/api/location")
    assert res.status_code == 503
    assert res.json() == {"ok": False, "error": {"code": "DEVICE_UNAVAILABLE", "message": "GPS가 연결되지 않았습니다"}}


def test_location_responds_before_ready(client):
    """조회 API라 준비 전에도 200. 503 NOT_READY 없음."""
    assert client.get("/api/health").json()["data"]["ready"] is False
    res = client.get("/api/location")
    assert res.status_code == 200 and res.json()["data"]["fix"] is True
