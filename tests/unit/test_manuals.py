"""GET /api/manuals, GET /api/manuals/{manual_id} (명세서 "[매뉴얼] 매뉴얼 목록", "[매뉴얼] 매뉴얼 보기용 카드")."""
import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


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
    assert client.get("/api/manuals/EM-01").status_code == 200


def test_card_shape_matches_spec(client, db):
    """매뉴얼 필드와 view_steps만. 값은 DB 그대로, 엔진 단계(steps)는 내보내지 않는다."""
    res = client.get("/api/manuals/EM-01")
    assert res.status_code == 200
    data = res.json()["data"]
    assert set(data) == {"manual_id", "situation_code", "name", "summary", "call_119", "view_steps"}
    manual = next(m for m in db["manuals"] if m["manual_id"] == "EM-01")
    assert data["call_119"] == manual["call_119"] == "즉시"
    assert data["view_steps"] == manual["view_steps"]
    assert set(data["view_steps"][0]) == {"no", "kind", "title", "text", "image"}


def test_cards_for_every_situation_manual(client, db):
    """v1.7은 상황 매뉴얼 16개 모두 카드가 있고 모두 98장. no 순서대로."""
    total = 0
    for manual in _situations(db):
        steps = client.get(f"/api/manuals/{manual['manual_id']}").json()["data"]["view_steps"]
        assert [s["no"] for s in steps] == sorted(s["no"] for s in steps)
        total += len(steps)
    assert total == sum(len(m["view_steps"]) for m in _situations(db)) == 98


def test_card_empty_view_steps(tmp_path, db):
    """view_steps가 빈 매뉴얼은 빈 목록을 그대로 준다."""
    em02 = next(m for m in db["manuals"] if m["manual_id"] == "EM-02")
    em02["view_steps"] = []
    path = tmp_path / "manual_db.json"
    path.write_text(json.dumps(db, ensure_ascii=False), encoding="utf-8")
    with TestClient(create_app(Settings(_env_file=None, manual_db_path=path))) as c:
        assert c.get("/api/manuals/EM-02").json()["data"]["view_steps"] == []


@pytest.mark.parametrize("manual_id", ["EM-99", "COM-SAFE"])
def test_card_not_found(client, manual_id):
    """없는 manual_id나 공통 절차(COM-*)는 404 MANUAL_NOT_FOUND."""
    res = client.get(f"/api/manuals/{manual_id}")
    assert res.status_code == 404
    assert res.json() == {"ok": False, "error": {"code": "MANUAL_NOT_FOUND",
                                                 "message": f"{manual_id} 매뉴얼이 없습니다"}}


def test_static_manual_icon(tmp_path):
    """그림 파일은 /static/manuals/에서 정적 파일로. 폴더나 파일이 없으면 404."""
    with TestClient(create_app(Settings(_env_file=None, static_dir=tmp_path / "none"))) as c:
        assert c.get("/static/manuals/EM-01.svg").status_code == 404
    (tmp_path / "manuals").mkdir()
    (tmp_path / "manuals" / "EM-01.svg").write_text("<svg/>", encoding="utf-8")
    with TestClient(create_app(Settings(_env_file=None, static_dir=tmp_path))) as c:
        res = c.get("/static/manuals/EM-01.svg")
        assert res.status_code == 200
        assert res.text == "<svg/>"
