"""템플릿 채우기와 숫자 읽기 (명세서 ⑦ 숫자 읽기).

템플릿 문장은 DB 원문 그대로 두고 중괄호 자리만 채운다. 읽는 문장에는 아라비아 숫자를 쓰지 않으므로 값은 한글로 바꿔 넣는다.
"""
import re

from app.services.normalizer import read_sino_korean

# T_WHICH_OFF의 {devices} (명세서 ⑦: "메트로놈, 구조 신호"를 넣은 T_WHICH_OFF)
SOUND_DEVICES_SPOKEN = "메트로놈, 구조 신호"

_DIGIT_BY_DIGIT = "공일이삼사오육칠팔구"
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


def read_coordinate(value: float) -> str:
    """정수부는 한자어 수, 소수점 아래 여섯 자리는 한 자리씩(0은 '공'). 37.402180 → 삼십칠 점 사 공 이 일 팔 공"""
    whole, frac = f"{value:.6f}".split(".")
    return f"{read_sino_korean(int(whole))} 점 {' '.join(_DIGIT_BY_DIGIT[int(c)] for c in frac)}"


def read_count(n: int) -> str:
    """{min}, {sec}, {ago}: 한자어 수(삼 분 이십 초)."""
    return read_sino_korean(n)


def fill(tts: str, values: dict[str, str]) -> str:
    """중괄호 자리를 값으로 채운다. 템플릿에 있는 자리의 값이 없으면 KeyError(엔진 버그)."""
    return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], tts)
