"""텍스트 한 턴: 정규화 → 모델 → 파서 (명세서 내부 모듈 계약 ③④⑤)."""
import asyncio

from app.common.error_code import ErrorCode
import pytest

from app.common.exceptions import AppError
from app.core.turn import TurnLock, run_text_turn
from app.fakes.fake_model import FakeModel


class SlowModel:
    async def run(self, text: str) -> str:
        await asyncio.sleep(1)
        return "<maum_5>(type=1)<maum_end>"


class BrokenModel:
    async def run(self, text: str) -> str:
        raise RuntimeError("런타임 오류")


async def test_success_goes_through_normalize_model_parse():
    out = await run_text_turn("t_0001", "등산하다, 넘어졌는데 다리를 움직이기 어려워요.", FakeModel(), 5)
    assert out.normalized.text == "등산하다 넘어졌는데 다리를 움직이기 어려워요"
    assert out.raw.startswith("<maum_0>(situation=3")
    assert out.function_call.args == {"situation": 3}
    assert out.function_call.observation.movement == 0
    assert out.error_code is None
    assert set(out.latency_ms) == {"normalize", "model", "parse"}


async def test_unknown_sentence_is_parse_error_with_raw():
    out = await run_text_turn("t_0002", "어 그게 아니고", FakeModel(), 5)
    assert out.error_code == ErrorCode.MODEL_PARSE_ERROR
    assert out.function_call is None
    assert out.raw == "<maum_9>"


async def test_out_of_code_table():
    out = await run_text_turn("t_0003", "이상한 출력", FakeModel({"이상한 출력": "<maum_5>(type=9)<maum_end>"}), 5)
    assert out.error_code == ErrorCode.MODEL_OUT_OF_RANGE


async def test_model_timeout():
    out = await run_text_turn("t_0004", "네", SlowModel(), 0.01)
    assert out.error_code == ErrorCode.MODEL_TIMEOUT
    assert out.raw is None
    assert set(out.latency_ms) == {"normalize", "model"}


async def test_model_runtime_error():
    out = await run_text_turn("t_0005", "네", BrokenModel(), 5)
    assert out.error_code == ErrorCode.MODEL_FAILED
    assert out.raw is None


async def test_outcome_serializes_error_code_as_string():
    out = await run_text_turn("t_0006", "어 그게 아니고", FakeModel(), 5)
    assert out.model_dump(mode="json")["error_code"] == "MODEL_PARSE_ERROR"


def test_turn_lock_numbers_turns_and_refuses_second():
    lock = TurnLock()
    with lock.turn() as first:
        assert first == "t_0001" and lock.busy
        with pytest.raises(AppError) as e:
            with lock.turn():
                pass
        assert e.value.code is ErrorCode.AUDIO_BUSY and e.value.status == 409
    with lock.turn() as second:
        assert second == "t_0002"
    assert not lock.busy


def test_turn_lock_released_on_error():
    lock = TurnLock()
    with pytest.raises(RuntimeError):
        with lock.turn():
            raise RuntimeError("턴 처리 중 오류")
    assert not lock.busy
