"""④ 모델 추론 (명세서 내부 모듈 계약 ④).

지금은 가짜 모델(app/fakes/fake_model.py)만 있다. 실제 모델(Llama-3.2-1B + LoRA)은 키트 단계에서 같은 Protocol로 끼운다.
"""
from typing import Protocol


class ModelRunner(Protocol):
    async def run(self, text: str) -> str:
        """정규화한 문장을 받아 모델 출력 원문(<maum_N>(...)<maum_end>)을 돌려준다. 대화 이력은 없다."""
        ...
