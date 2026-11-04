"""위치 API (명세서 "위치"): GET /api/location, POST /api/location/announce."""
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


async def test_location_updated_event(kit):
    """위치·수신 상태가 바뀌면 location.updated로 GET /api/location과 같은 Location을 보낸다."""
    kit.parts.gps.lose_fix()
    await kit.advance(60)
    kit.parts.gps.set_fix(37.5, 127.0)
    await kit.settle()
    updates = [e for e in kit.events if e.type == "location.updated"]
    assert [e.turn_id for e in updates] == [None, None]  # 연결·상태 이벤트는 턴이 없다
    lost, found = (e.data for e in updates)
    assert list(lost) == LOCATION_KEYS and lost["fix"] is False and lost["last_known"]["age_s"] == 0
    assert (found["fix"], found["lat"], found["lon"]) == (True, 37.5, 127.0)
    assert found == await get_location(kit)


def test_location_responds_before_ready(client):
    """조회 API라 준비 전에도 200. 503 NOT_READY 없음."""
    assert client.get("/api/health").json()["data"]["ready"] is False
    res = client.get("/api/location")
    assert res.status_code == 200 and res.json()["data"]["fix"] is True


async def announce(kit: Kit) -> dict:
    res = await kit.http.post("/api/location/announce")
    assert res.status_code == 202, res.text
    assert res.json()["ok"] is True
    return res.json()["data"]


def said_since(kit: Kit, mark: int) -> list[str | None]:
    return [u.template_id or u.step_id for u in kit.spoken_since(mark)]


async def test_announce_with_fix(kit):
    """휴대폰 신고 안내(T_OOS_2) 뒤 좌표 낭독. 202 + announce: fix."""
    mark = len(kit.spoken)
    data = await announce(kit)
    assert list(data) == ["turn_id", "announce"]
    assert data["turn_id"].startswith("t_") and data["announce"] == "fix"
    await kit.settle()
    assert said_since(kit, mark) == ["T_OOS_2", "T_GPS_OK"]


async def test_announce_last_known(kit):
    """못 잡았지만 30분 안의 마지막 위치가 있으면 last."""
    kit.parts.gps.lose_fix()
    await kit.advance(60)
    mark = len(kit.spoken)
    assert (await announce(kit))["announce"] == "last"
    await kit.settle()
    assert said_since(kit, mark) == ["T_OOS_2", "T_GPS_LAST"]


async def test_announce_wait_then_found(kit):
    """마지막 위치도 쓸 수 없으면 wait. 조용히 다시 시도하다가 잡히면 T_GPS_OK."""
    kit.parts.gps.lose_fix()
    await kit.advance(31 * 60)  # 마지막 위치는 30분이 지나면 읽지 않는다
    mark = len(kit.spoken)
    assert (await announce(kit))["announce"] == "wait"
    await kit.settle()
    assert said_since(kit, mark) == ["T_OOS_2", "T_GPS_WAIT"]
    assert kit.state.gps_retry is not None
    kit.parts.gps.set_fix()
    await kit.settle()
    assert said_since(kit, mark)[-1] == "T_GPS_OK" and kit.state.gps_retry is None


async def test_announce_without_gps_module(kit):
    """GPS 모듈이 없어도 오류가 아니다. 휴대폰 신고 안내만 읽고 202 + unavailable, 재시도도 걸지 않는다."""
    kit.parts.gps.present = False
    mark = len(kit.spoken)
    assert (await announce(kit))["announce"] == "unavailable"
    await kit.settle()
    assert said_since(kit, mark) == ["T_OOS_2"]
    assert kit.state.gps_retry is None


async def test_announce_during_question_asks_again(kit):
    """질문 중이면 좌표를 읽은 뒤 그 질문을 다시 묻는다."""
    await kit.say("등산하다 넘어졌는데 다리를 움직이기 어려워요")
    question = kit.state.step_id
    mark = len(kit.spoken)
    assert (await announce(kit))["announce"] == "fix"
    await kit.settle()
    assert said_since(kit, mark) == ["T_OOS_2", "T_GPS_OK", question]
    assert kit.state.step_id == question


def test_announce_before_ready_is_503(client):
    res = client.post("/api/location/announce")
    assert res.status_code == 503 and res.json()["error"]["code"] == "NOT_READY"
