"""⑤ 출력 파서: 모델 출력 문자열을 FunctionCall로 바꾼다 (명세서 내부 모듈 계약 ⑤).

검사: 1. <maum_N>(...)<maum_end> 형식 2. func별 필수 키가 모두 있고 남는 키 없음 3. 모든 값이 코드표 범위 안
4. object·type 조합이 코드표에 있음 5. func 1·2의 관찰값 개수.
형식 위반은 MODEL_PARSE_ERROR, 범위 밖은 MODEL_OUT_OF_RANGE. 둘 다 엔진이 T_RETRY로 받는다.
"""
import logging
import re

from app.common.error_code import ErrorCode
from app.schemas.device import DEVICE_TYPES
from app.schemas.function_call import FUNCTIONS, OBSERVATION_VALUES, FunctionCall, Observation

log = logging.getLogger(__name__)

_CALL_RE = re.compile(r"<maum_(\d+)>\((.*)\)<maum_end>", re.DOTALL)
_PAIR_RE = re.compile(r"\s*([a-z_]+)\s*=\s*(-?\d+)\s*")


class ModelOutputError(Exception):
    """해석 실패. code는 MODEL_PARSE_ERROR 또는 MODEL_OUT_OF_RANGE."""

    def __init__(self, code: ErrorCode, message: str):
        super().__init__(message)
        self.code = code


def _format_error(message: str) -> ModelOutputError:
    return ModelOutputError(ErrorCode.MODEL_PARSE_ERROR, message)


def _range_error(message: str) -> ModelOutputError:
    return ModelOutputError(ErrorCode.MODEL_OUT_OF_RANGE, message)


def parse(raw: str) -> FunctionCall:
    # 1 형식. <maum_end>가 없으면 잘린 출력이다
    m = _CALL_RE.fullmatch(raw.strip())
    if not m:
        raise _format_error("<maum_N>(...)<maum_end> 형식이 아닙니다")
    func = int(m.group(1))
    spec = FUNCTIONS.get(func)
    if spec is None:
        raise _format_error(f"없는 함수 maum_{func}")
    values: dict[str, int] = {}
    if m.group(2).strip():
        for part in m.group(2).split(","):
            pair = _PAIR_RE.fullmatch(part)
            if not pair:
                raise _format_error(f"인자 형식 오류 {part.strip()!r}")
            key = pair.group(1)
            if key in values:
                raise _format_error(f"중복 키 {key}")
            values[key] = int(pair.group(2))

    # 2 필수 키
    expected = set(spec.args) | (set(OBSERVATION_VALUES) if spec.observation else set())
    if set(values) != expected:
        raise _format_error(f"키 불일치 (없음 {sorted(expected - set(values))}, 남음 {sorted(set(values) - expected)})")

    # 3 코드표 범위, 4 object·type 조합
    for key, allowed in spec.args.items():
        if values[key] not in allowed:
            raise _range_error(f"{key}={values[key]}는 코드표 밖입니다")
    if func == 4 and values["type"] not in DEVICE_TYPES[values["object"]]:
        raise _range_error(f"object={values['object']}, type={values['type']} 조합이 코드표에 없습니다")

    observation = None
    if spec.observation:
        for key, allowed in OBSERVATION_VALUES.items():
            if values[key] not in allowed:
                raise _range_error(f"{key}={values[key]}는 코드표 밖입니다")
        # 5 관찰값 개수: 코드표상 func 1은 0~1개, func 2는 정확히 1개.
        # 2개 이상이어도 버리지 않고 모두 반영한다(재질문만 늘지 않게). func 2에 하나도 없으면 범위 밖
        filled = [k for k in OBSERVATION_VALUES if values[k] != -1]
        if func == 2 and not filled:
            raise _range_error("정정(maum_2)에 관찰값이 없습니다")
        if func in (1, 2) and len(filled) >= 2:
            log.warning("maum_%d에 관찰값이 %d개: %s", func, len(filled), raw)
        observation = Observation(**{k: values[k] for k in OBSERVATION_VALUES})

    return FunctionCall(
        func=func,
        name=spec.name,
        args={k: values[k] for k in spec.args},
        observation=observation,
        raw=raw,
    )
