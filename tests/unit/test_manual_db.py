"""매뉴얼 DB 검사·적재 (명세서 ⑥ "매뉴얼 DB v1.7을 해석", 서버 시작 때 검사)."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import BACKEND_DIR, Settings
from app.core.readiness import Component
from app.main import create_app
from app.services.manual_db import ManualDBError, check, failures, load_manual_db

DB_PATH = BACKEND_DIR / "data" / "manual_db.json"
TOOL = BACKEND_DIR / "tools" / "check_manual_db.py"


@pytest.fixture(scope="module")
def real_db() -> dict:
    return json.loads(DB_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def db(real_db) -> dict:
    return copy.deepcopy(real_db)


def _write(tmp_path: Path, db: dict) -> Path:
    path = tmp_path / "manual_db.json"
    path.write_text(json.dumps(db, ensure_ascii=False), encoding="utf-8")
    return path


def test_real_db_passes_check(db):
    assert failures(check(db)) == []


def test_check_finds_dangling_step_reference(db):
    db["steps"][0]["next"] = "XX-99"
    results = check(db)
    assert f"{db['steps'][0]['step_id']}.next=XX-99" in results["끊긴 단계 참조"]
    assert "끊긴 단계 참조" in failures(results)


def test_check_finds_spoken_text_rule_violation(db):
    """읽는 문장에는 아라비아 숫자·'/'·영문을 쓰지 않는다."""
    db["templates"][0]["tts"] = "A 1/2"
    assert "읽는 문장 규칙 위반 (숫자·/·영문)" in failures(check(db))


def test_check_finds_situation_mapping_gap(db):
    situation = next(m for m in db["manuals"] if m["type"] == "situation")
    situation["situation_code"] = 99
    assert "situation_code 매핑" in failures(check(db))


def _run_tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, encoding="utf-8")


def test_tool_passes_on_real_db():
    res = _run_tool()
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout.rstrip().endswith("결과: 통과")


def test_tool_fails_on_broken_db(tmp_path, db):
    db["steps"][0]["next"] = "XX-99"
    res = _run_tool(str(_write(tmp_path, db)))
    assert res.returncode == 1
    assert "[FAIL] 끊긴 단계 참조" in res.stdout


def test_load_builds_lookups(real_db):
    mdb = load_manual_db(DB_PATH)
    assert mdb.version == real_db["meta"]["version"].split()[0]
    assert len(mdb.steps_by_id) == len(real_db["steps"])
    assert len(mdb.manuals_by_id) == len(real_db["manuals"])
    assert len(mdb.templates_by_id) == len(real_db["templates"])
    assert mdb.steps_by_id["EM01-13"]["kind"] == "ask"
    assert mdb.manual_by_situation[1]["manual_id"] == "EM-01"
    assert mdb.manual_by_situation[-1]["manual_id"] == "EM-00"
    assert len(mdb.manual_by_situation) == 16


def test_load_raises_when_check_fails(tmp_path, db):
    db["steps"][0]["next"] = "XX-99"
    with pytest.raises(ManualDBError) as e:
        load_manual_db(_write(tmp_path, db))
    assert e.value.failures[0] == "끊긴 단계 참조"
    assert "XX-99" in str(e.value)


def test_load_raises_on_missing_file(tmp_path):
    with pytest.raises(ManualDBError, match="파일이 없습니다"):
        load_manual_db(tmp_path / "none.json")


def test_load_raises_on_broken_json(tmp_path):
    path = tmp_path / "manual_db.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ManualDBError, match="JSON 형식"):
        load_manual_db(path)


def test_load_raises_on_missing_field(tmp_path, db):
    del db["routing"]
    with pytest.raises(ManualDBError, match="구조"):
        load_manual_db(_write(tmp_path, db))


def test_server_does_not_start_with_broken_db(tmp_path, db):
    db["steps"][0]["next"] = "XX-99"
    app = create_app(Settings(_env_file=None, manual_db_path=_write(tmp_path, db)))
    with pytest.raises(ManualDBError):
        with TestClient(app):
            pass


def test_startup_loads_db_and_marks_manual_db_ready(real_db):
    """시작 때 매뉴얼 DB를 적재하고 준비 상태에 표시한다. 나머지 부품이 끝나야 ready."""
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as c:
        assert app.state.manual_db.version == real_db["meta"]["version"].split()[0]
        assert c.get("/api/health").json()["data"]["ready"] is False
        for comp in Component:
            if comp is not Component.MANUAL_DB:
                app.state.readiness.mark_ready(comp)
        assert c.get("/api/health").json()["data"]["ready"] is True
