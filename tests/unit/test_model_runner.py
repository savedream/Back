"""④ 모델 추론 공용 부분: 학습 때 프롬프트, 출력 정리, 한 번에 하나씩 돌리기(명세 ④)."""
import asyncio
import threading
import time

import pytest

from app.services.model_runner import OneAtATime, build_prompt, clean_output


def test_prompt_is_same_as_training():
    """학습 때와 바이트 단위로 같아야 한다(받은 모델의 tokenizer_config.json chat_template과 같은 문장, BOS는 토크나이저 몫)."""
    assert build_prompt("다시 말해 줘") == (
        "Below is the query from the users, please choose the correct function and generate the parameters to call "
        "the function. Query: 다시 말해 줘 Response:")


def test_clean_output_keeps_maum_end_and_drops_eot():
    assert clean_output("<maum_3>(scope=1, speed=0)<maum_end>") == "<maum_3>(scope=1, speed=0)<maum_end>"
    assert clean_output(" <maum_5>(type=1)<|eot_id|>") == "<maum_5>(type=1)"  # <maum_end> 없이 멈추면 파서가 잘린 출력으로 본다


async def test_calls_run_one_at_a_time():
    worker, running, most = OneAtATime(), [], []

    def infer(n):
        running.append(n)
        most.append(len(running))
        time.sleep(0.02)
        running.remove(n)
        return n

    assert await asyncio.gather(*(worker.call(infer, n) for n in range(3))) == [0, 1, 2]
    assert max(most) == 1


async def test_timeout_returns_only_after_native_call_ends():
    """시간 초과로 취소돼도 네이티브 호출이 끝나야 돌아온다(그때까지 턴 잠금 유지), 결과는 버린다."""
    worker, release, finished = OneAtATime(), threading.Event(), threading.Event()

    def infer():
        release.wait(2)
        finished.set()
        return "<maum_5>(type=1)<maum_end>"

    asyncio.get_running_loop().call_later(0.1, release.set)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(worker.call(infer), timeout=0.01)
    assert finished.is_set()
