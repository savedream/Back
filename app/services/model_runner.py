"""④ 모델 추론 (명세서 내부 모듈 계약 ④).

가짜 모델(app/fakes/fake_model.py)과 PC 개발용 실제 모델(app/services/hf_model.py, Llama-3.2-1B + LoRA를 합친 모델)이
같은 Protocol을 따른다. 키트(NPU)용 실행기도 키트 단계에서 같은 자리에 끼우고, 프롬프트·출력 정리·한 번에 하나씩
돌리기는 실행기마다 여기 것을 같이 쓴다.
"""
import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol, TypeVar

T = TypeVar("T")

# 학습 때와 바이트 단위로 같아야 한다(명세 ④ 프롬프트). 앞의 BOS는 토크나이저가 붙인다
PROMPT = ("Below is the query from the users, please choose the correct function and generate the parameters to call "
          "the function. Query: {text} Response:")
MAX_NEW_TOKENS = 64  # 명세 ④ 생성 설정
EOT_TEXT = "<|eot_id|>"  # <maum_end> 말고 생성을 멈추는 다른 토큰(generation_config의 eos). 출력에서는 지운다
WARMUP_TEXT = "다시 말해 줘"  # 기동 때 예열 추론에 넣는 문장(결과는 쓰지 않음)


class ModelRunner(Protocol):
    async def run(self, text: str) -> str:
        """정규화한 문장을 받아 모델 출력 원문(<maum_N>(...)<maum_end>)을 돌려준다. 대화 이력은 없다."""
        ...


class LoadedModel(ModelRunner, Protocol):
    """실제 모델 실행기(PC용 hf_model, 키트용). 서버를 켠 뒤 적재·예열하고 끌 때 내려놓는다(app/main.py)."""

    async def load(self) -> None:
        """모델 적재. 끝나면 준비 상태의 LLM을 켠다."""
        ...

    async def warm_up(self) -> None:
        """예열 추론 1회(명세 ④ 예열). 끝나면 준비 상태의 LLM_WARMUP을 켠다."""
        ...

    async def close(self) -> None:
        """모델을 내려놓는다. 진행 중인 추론이 끝난 뒤에 돈다."""
        ...


def build_prompt(text: str) -> str:
    """정규화한 문장을 학습 때의 프롬프트에 넣는다(채팅 템플릿을 씌우지 않음)."""
    return PROMPT.format(text=text)


def clean_output(decoded: str) -> str:
    """특수 토큰을 살려 디코딩한 생성 부분에서 멈춤 토큰 <|eot_id|>와 앞뒤 공백을 지운다. <maum_end>는 남긴다(파서가 본다)."""
    return decoded.replace(EOT_TEXT, "").strip()


class OneAtATime:
    """네이티브 추론을 전용 스레드 하나에서 한 번에 하나씩 돌린다(명세 ④ 동시 실행).

    시간 초과로 취소돼도 네이티브 호출이 실제로 끝날 때까지 돌아오지 않는다. 그래서 턴 잠금은 그때까지 유지되고,
    결과는 버린다(asyncio.wait_for는 취소한 작업이 끝나기를 기다린 뒤 시간 초과를 낸다).
    """

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="model")

    async def call(self, fn: Callable[..., T], *args) -> T:
        future = asyncio.get_running_loop().run_in_executor(self._pool, fn, *args)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            await asyncio.wait([future])
            raise
