"""④ 모델 추론 — PC 개발용 실제 모델 실행기(transformers).

받은 모델 폴더(config.json, model.safetensors, tokenizer.json, tokenizer_config.json, generation_config.json)를
그대로 읽는다. 키트(NPU, W8A16)에서는 같은 ModelRunner 자리에 키트용 실행기를 끼운다.
설치: pip install -e '.[model]'
"""
import gc
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from app.services.model_runner import MAX_NEW_TOKENS, WARMUP_TEXT, OneAtATime, build_prompt, clean_output


class HfModelRunner:
    def __init__(self, model_dir: Path):
        self.model_dir = model_dir
        self.calls = 0
        self._worker = OneAtATime()
        self._device = "mps" if torch.backends.mps.is_available() else "cpu"
        self._tokenizer = None
        self._model = None

    async def load(self) -> None:
        """모델 적재(수 초). 서버를 켠 뒤 백그라운드로 부른다."""
        await self._worker.call(self._load)

    async def warm_up(self) -> None:
        """예열 추론 1회(명세 ④ 예열). 결과는 쓰지 않는다."""
        await self._worker.call(self._generate, WARMUP_TEXT)

    async def run(self, text: str) -> str:
        self.calls += 1
        return await self._worker.call(self._generate, text)

    async def close(self) -> None:
        """서버를 끌 때 모델을 내려놓는다(같은 프로세스에서 앱을 여러 번 만드는 시험에서 맥 GPU 메모리가 쌓이지 않게)."""
        await self._worker.call(self._unload)

    def _unload(self) -> None:
        self._model = self._tokenizer = None
        gc.collect()
        if self._device == "mps":
            torch.mps.empty_cache()

    def _load(self) -> None:
        # CPU는 float16 계산이 느리거나 안 되는 연산이 있어 float32로 읽는다
        dtype = torch.float32 if self._device == "cpu" else torch.float16
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_dir)
        self._model = AutoModelForCausalLM.from_pretrained(self.model_dir, dtype=dtype).to(self._device).eval()

    def _generate(self, text: str) -> str:
        # 토크나이저가 BOS를 붙인다(학습 때 chat_template과 같은 입력). 탐욕 디코딩, <maum_end>·<|eot_id|>에서 멈춤
        inputs = self._tokenizer(build_prompt(text), return_tensors="pt").to(self._device)
        with torch.no_grad():
            out = self._model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=False)
        generated = out[0, inputs["input_ids"].shape[1]:]
        # 공백 정리(clean_up_tokenization_spaces)는 BPE 출력의 문장부호 앞 공백을 지울 수 있어 끈다
        return clean_output(self._tokenizer.decode(generated, skip_special_tokens=False,
                                                   clean_up_tokenization_spaces=False))
