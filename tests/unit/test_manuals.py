"""GET /api/manuals (명세서 "[매뉴얼] 매뉴얼 목록")."""
import json

import pytest


@pytest.fixture
def db(settings) -> dict:
    return json.loads(settings.manual_db_path.read_text(encoding="utf-8"))


def _situations(db: dict) -> list[dict]:
    return [m for m in db["manuals"] if m["type"] == "situation"]


def test_list_shape_matches_spec(client):
    """명세서 예시: [{"manual_id", "situation_code", "name", "summary", "featured", "icon"}]."""
    res = client.get("/api/manuals")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    first = body["data"][0]
    assert set(first) == {"manual_id", "situation_code", "name", "summary", "featured", "icon"}
    assert first == {"manual_id": "EM-01", "situation_code": 1, "name": "심정지 의심",
                     "summary": "반응, 호흡 확인과 가슴압박", "featured": 1, "icon": "/static/manuals/EM-01.svg"}


def test_list_only_situation_manuals(client, db):
    """상황 매뉴얼만 내보내고 공통 절차(COM-*)는 뺀다."""
    ids = [m["manual_id"] for m in client.get("/api/manuals").json()["data"]]
    assert sorted(ids) == sorted(m["manual_id"] for m in _situations(db))
    assert not any(i.startswith("COM-") for i in ids)


def test_list_order_featured_then_priority_and_unknown_last(client, db):
    """featured 순서대로 먼저, 나머지는 priority 순, EM-00은 맨 끝."""
    data = client.get("/api/manuals").json()["data"]
    situations = {m["manual_id"]: m for m in _situations(db)}
    featured = [m for m in data if m["featured"] is not None]
    rest = [m for m in data if m["featured"] is None]
    assert data[: len(featured)] == featured
    assert [m["featured"] for m in featured] == sorted(m["featured"] for m in featured)
    assert data[-1]["manual_id"] == "EM-00"
    priorities = [situations[m["manual_id"]]["priority"] for m in rest[:-1]]
    assert priorities == sorted(priorities)


def test_list_featured_only(client, db):
    """?featured=true면 featured가 있는 6개만, featured 순서대로."""
    data = client.get("/api/manuals", params={"featured": "true"}).json()["data"]
    assert [m["manual_id"] for m in data] == ["EM-01", "EM-02", "EM-03", "EM-04", "EM-05", "EM-06"]
    assert len(data) == len([m for m in _situations(db) if m.get("featured") is not None])


def test_list_featured_invalid_is_400(client):
    res = client.get("/api/manuals", params={"featured": "abc"})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_REQUEST"


def test_list_responds_before_ready(client):
    """준비 전(ready: false)에도 200. 503 NOT_READY 없음."""
    assert client.get("/api/health").json()["data"]["ready"] is False
    assert client.get("/api/manuals").status_code == 200

