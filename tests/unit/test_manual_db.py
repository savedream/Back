"""매뉴얼 DB 검사·적재 (명세서 ⑥ "매뉴얼 DB v1.7을 해석", 서버 시작 때 검사)."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import BACKEND_DIR
from app.services.manual_db import check, failures

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
