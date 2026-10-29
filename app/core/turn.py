"""텍스트 한 턴: ③ 정규화 → ④ 모델 → ⑤ 파서 (엔진에 넘기기 직전까지).

명세서: /api/utterances는 ③부터 음성 턴과 같은 코드 경로를 탄다. 음성 턴도 STT 뒤에 이 함수를 쓴다.
"""
import asyncio
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import BaseModel

from app.common.error_code import ErrorCode
from app.common.exceptions import AppError
from app.schemas.function_call import FunctionCall
from app.schemas.turn import NormalizedText
from app.services.model_runner import ModelRunner
from app.services.normalizer import normalize
from app.services.output_parser import ModelOutputError, parse

log = logging.getLogger(__name__)


class TurnOutcome(BaseModel):
    turn_id: str
    normalized: NormalizedText
    raw: str | None  # 모델 출력 원문. 시간 초과·모델 오류면 None
    function_call: FunctionCall | None
    error_code: ErrorCode | None  # MODEL_TIMEOUT · MODEL_FAILED · MODEL_PARSE_ERROR · MODEL_OUT_OF_RANGE
    latency_ms: dict[str, int]  # normalize, model, parse


def _elapsed_ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


async def run_text_turn(turn_id: str, text: str, model: ModelRunner, model_timeout_s: float) -> TurnOutcome:
    latency: dict[str, int] = {}
    start = time.perf_counter()
    normalized = normalize(text)
    latency["normalize"] = _elapsed_ms(start)
    return await interpret(turn_id, normalized, model, model_timeout_s, latency)


async def interpret(turn_id: str, normalized: NormalizedText, model: ModelRunner, model_timeout_s: float,
                    latency: dict[str, int]) -> TurnOutcome:
    """④ 모델 → ⑤ 파서. latency에 model·parse 시간을 더한다."""

    def failed(code: ErrorCode, raw: str | None = None) -> TurnOutcome:
        return TurnOutcome(
            turn_id=turn_id, normalized=normalized, raw=raw, function_call=None, error_code=code, latency_ms=latency
        )

    start = time.perf_counter()
    try:
        raw = await asyncio.wait_for(model.run(normalized.text), timeout=model_timeout_s)
    except asyncio.TimeoutError:
        latency["model"] = _elapsed_ms(start)
        log.warning("모델 시간 초과 (%.1f초): %s", model_timeout_s, normalized.text)
        return failed(ErrorCode.MODEL_TIMEOUT)
    except Exception:
        latency["model"] = _elapsed_ms(start)
        log.exception("모델 실행 오류: %s", normalized.text)
        return failed(ErrorCode.MODEL_FAILED)
    latency["model"] = _elapsed_ms(start)

    start = time.perf_counter()
    try:
        function_call = parse(raw)
    except ModelOutputError as e:
        latency["parse"] = _elapsed_ms(start)
        log.warning("모델 출력 해석 실패 %s: %s (%s)", e.code.value, e, raw)
        return failed(e.code, raw)
    latency["parse"] = _elapsed_ms(start)
    return TurnOutcome(
        turn_id=turn_id, normalized=normalized, raw=raw, function_call=function_call, error_code=None, latency_ms=latency
    )


class TurnLock:
    """음성 경로·/api/speech/start·/api/utterances 공용 턴 잠금(계획 3-5). 턴을 시작할 때 잡으며 턴 번호를 매기고,
    엔진이 그 턴 입력을 처리한 뒤 놓는다. 잡혀 있으면 409 AUDIO_BUSY."""

    def __init__(self) -> None:
        self._busy = False
        self._count = 0

    @property
    def busy(self) -> bool:
        return self._busy

    @contextmanager
    def turn(self) -> Iterator[str]:
        if self._busy:
            raise AppError(ErrorCode.AUDIO_BUSY, "다른 턴을 처리 중입니다")
        self._busy = True
        self._count += 1
        try:
            yield f"t_{self._count:04d}"
        finally:
            self._busy = False
