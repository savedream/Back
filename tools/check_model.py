#!/usr/bin/env python3
"""실제 모델 확인: 가짜 모델 표(시나리오 시험 문장)를 실제 모델에 넣어 출력이 표와 같은지, 파서를 통과하는지, 얼마나 걸리는지 본다.

사용: python tools/check_model.py [모델 폴더]
기본값: 서버와 같은 설정의 model_dir(SALYEO_MODEL_DIR·.env), 없으면 저장소 옆 models/maum. 필요: pip install -e '.[model]'
표와 다른 출력은 보고만 한다(표는 시험용으로 사람이 적은 기대값). 파서를 통과하지 못한 출력이 하나라도 있으면 종료 코드 1.
"""
import asyncio, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import Settings  # noqa: E402
from app.fakes.fake_model import load_default_table  # noqa: E402
from app.services.hf_model import HfModelRunner  # noqa: E402
from app.services.output_parser import ModelOutputError, parse  # noqa: E402

model_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Settings().model_dir or ROOT.parent / 'models' / 'maum'
if not model_dir.is_dir():
    sys.exit(f'모델 폴더가 없음: {model_dir}')


async def main() -> int:
    runner = HfModelRunner(model_dir)
    start = time.perf_counter()
    await runner.load()
    print(f'적재 {time.perf_counter() - start:.1f}초 ({model_dir})')
    start = time.perf_counter()
    await runner.warm_up()
    print(f'예열 {time.perf_counter() - start:.1f}초')
    table = load_default_table()
    different, broken, times = [], [], []
    for text, expected in table.items():
        start = time.perf_counter()
        raw = await runner.run(text)
        times.append(time.perf_counter() - start)
        try:
            parse(raw)
        except ModelOutputError as e:
            broken.append((text, raw, e.code.value))
        if raw != expected:
            different.append((text, raw, expected))
    print(f'\n표와 같음 {len(table) - len(different)}/{len(table)}, 파서 통과 {len(table) - len(broken)}/{len(table)}')
    print(f'추론 시간 평균 {sum(times) / len(times):.2f}초, 최대 {max(times):.2f}초 (이 PC 기준, 키트와 다름)')
    for text, raw, expected in different:
        print(f'\n[표와 다름] {text}\n  모델: {raw}\n  표:   {expected}')
    for text, raw, code in broken:
        print(f'\n[파서 실패 {code}] {text}\n  모델: {raw}')
    return 1 if broken else 0


sys.exit(asyncio.run(main()))
