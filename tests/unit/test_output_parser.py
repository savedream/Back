"""⑤ 출력 파서 (명세서 내부 모듈 계약 ⑤, 공통 데이터 형식 FunctionCall)."""
import logging

import pytest

from app.common.error_code import ErrorCode
from app.services.output_parser import ModelOutputError, parse

NO_OBS = "conscious=-1, breathing=-1, bleeding=-1, movement=-1, scene_danger=-1"


def obs(**filled) -> str:
    keys = ["conscious", "breathing", "bleeding", "movement", "scene_danger"]
    return ", ".join(f"{k}={filled.get(k, -1)}" for k in keys)


def test_report_situation():
    raw = f"<maum_0>(situation=1, {obs(conscious=0, breathing=0)})<maum_end>"  # 발표자료 8번 예시
    fc = parse(raw)
    assert (fc.func, fc.name, fc.args) == (0, "report_situation", {"situation": 1})
    assert fc.observation.model_dump() == {"conscious": 0, "breathing": 0, "bleeding": -1, "movement": -1, "scene_danger": -1}
    assert fc.raw == raw


@pytest.mark.parametrize(
    "raw, func, name, args",
    [
        (f"<maum_1>(answer=2, {NO_OBS})<maum_end>", 1, "answer_check", {"answer": 2}),
        (f"<maum_2>({obs(breathing=1)})<maum_end>", 2, "correct_observation", {}),
        ("<maum_3>(scope=1, speed=1)<maum_end>", 3, "repeat_guidance", {"scope": 1, "speed": 1}),
        ("<maum_4>(object=1, type=1)<maum_end>", 4, "request_device_function", {"object": 1, "type": 1}),
        ("<maum_4>(object=-1, type=2)<maum_end>", 4, "request_device_function", {"object": -1, "type": 2}),
        ("<maum_5>(type=4)<maum_end>", 5, "out_of_scope", {"type": 4}),
    ],
)
def test_each_function(raw, func, name, args):
    fc = parse(raw)
    assert (fc.func, fc.name, fc.args) == (func, name, args)
    assert (fc.observation is None) == (func >= 3)


def test_key_order_and_spaces_do_not_matter():
    fc = parse("  <maum_1>(scene_danger=-1,movement=-1, bleeding=-1,breathing=-1, conscious=-1 ,answer=3)<maum_end>\n")
    assert fc.args == {"answer": 3}


@pytest.mark.parametrize(
    "raw",
    [
        f"<maum_0>(situation=1, {NO_OBS})",  # <maum_end> 없음(잘린 출력)
        "<maum_9>",
        f"<maum_7>(situation=1, {NO_OBS})<maum_end>",  # 없는 함수
        "<maum_1>(answer=2)<maum_end>",  # 관찰값 키 없음
        f"<maum_1>(answer=2, extra=1, {NO_OBS})<maum_end>",  # 남는 키
        f"<maum_1>(answer=2, answer=1, {NO_OBS})<maum_end>",  # 중복 키
        f"<maum_1>(answer=yes, {NO_OBS})<maum_end>",  # 정수가 아님
        "<maum_3>(scope=1, speed=0))<maum_end>",
        "맞는 함수가 없습니다",
    ],
)
def test_format_violation_is_parse_error(raw):
    with pytest.raises(ModelOutputError) as e:
        parse(raw)
    assert e.value.code == ErrorCode.MODEL_PARSE_ERROR


@pytest.mark.parametrize(
    "raw",
    [
        f"<maum_0>(situation=16, {NO_OBS})<maum_end>",
        f"<maum_0>(situation=0, {NO_OBS})<maum_end>",
        f"<maum_1>(answer=0, {NO_OBS})<maum_end>",
        f"<maum_1>(answer=1, {obs(conscious=3)})<maum_end>",
        f"<maum_1>(answer=1, {obs(bleeding=2)})<maum_end>",
        "<maum_3>(scope=4, speed=0)<maum_end>",
        "<maum_4>(object=1, type=2)<maum_end>",  # GPS는 type 1만
        "<maum_4>(object=6, type=1)<maum_end>",
        "<maum_5>(type=5)<maum_end>",
        f"<maum_2>({NO_OBS})<maum_end>",  # 관찰값 없는 정정
    ],
)
def test_out_of_code_table_is_out_of_range(raw):
    with pytest.raises(ModelOutputError) as e:
        parse(raw)
    assert e.value.code == ErrorCode.MODEL_OUT_OF_RANGE


@pytest.mark.parametrize("func, head", [(1, "answer=1, "), (2, "")])
def test_two_or_more_observations_kept_with_warning(caplog, func, head):
    """func 1·2에 관찰값이 2개 이상이어도 버리지 않고 모두 반영하고 경고만 남긴다."""
    raw = f"<maum_{func}>({head}{obs(conscious=0, breathing=0)})<maum_end>"
    with caplog.at_level(logging.WARNING, logger="app.services.output_parser"):
        fc = parse(raw)
    assert (fc.observation.conscious, fc.observation.breathing) == (0, 0)
    assert "관찰값이 2개" in caplog.text
