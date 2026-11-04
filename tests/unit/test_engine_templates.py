"""템플릿 채우기와 숫자 읽기 (명세서 ⑦ 숫자 읽기)."""
import pytest

from app.config import BACKEND_DIR
from app.services.manual_db import load_manual_db
from app.services.procedure_engine.templates import fill, read_coordinate, read_count


def test_read_coordinate():
    assert read_coordinate(37.402180) == "삼십칠 점 사 공 이 일 팔 공"  # 명세서 예시
    assert read_coordinate(127.679420) == "백이십칠 점 육 칠 구 사 이 공"


def test_read_count():
    assert read_count(3) == "삼"
    assert read_count(20) == "이십"


def test_fill_gps_ok_matches_db_template():
    db = load_manual_db(BACKEND_DIR / "data" / "manual_db.json")
    text = fill(db.templates_by_id["T_GPS_OK"]["tts"], {"lat": read_coordinate(37.40218), "lon": read_coordinate(127.67942)})
    assert text == "현재 위치는 위도 삼십칠 점 사 공 이 일 팔 공, 경도 백이십칠 점 육 칠 구 사 이 공입니다."


def test_fill_timer_check():
    assert fill("{min} 분 {sec} 초 지났어요.", {"min": read_count(3), "sec": read_count(20)}) == "삼 분 이십 초 지났어요."


def test_fill_missing_value_is_error():
    with pytest.raises(KeyError):
        fill("{name} 안내로 바꿀까요?", {})
