"""가짜 모델 (명세서 ④ 자리, 문장 → 모델 출력 표)."""
import json

import pytest

from app.common.error_code import ErrorCode
from app.fakes.fake_model import DEFAULT_TABLE_PATH, FakeModel, load_default_table
from app.services.normalizer import normalize
from app.services.output_parser import ModelOutputError, parse


async def test_returns_table_output_and_counts_calls():
    model = FakeModel({"네": "<maum_3>(scope=1, speed=0)<maum_end>"})
    assert await model.run("네") == "<maum_3>(scope=1, speed=0)<maum_end>"
    assert model.calls == 1


async def test_unknown_sentence_is_parse_error():
    model = FakeModel({})
    with pytest.raises(ModelOutputError) as e:
        parse(await model.run("어 그게 아니고"))
    assert e.value.code == ErrorCode.MODEL_PARSE_ERROR


async def test_add_is_per_instance():
    a, b = FakeModel(), FakeModel()
    a.add("시험 문장", "<maum_5>(type=1)<maum_end>")
    assert await a.run("시험 문장") == "<maum_5>(type=1)<maum_end>"
    assert await b.run("시험 문장") != "<maum_5>(type=1)<maum_end>"


def test_default_table_rows_are_valid():
    """기본 표의 문장은 이미 정규화된 모양이고, 출력은 파서를 통과한다(관찰값 다섯 키, <maum_end>)."""
    rows = json.loads(DEFAULT_TABLE_PATH.read_text(encoding="utf-8"))
    texts = [row["text"] for row in rows]
    assert len(texts) == len(set(texts))
    for row in rows:
        assert normalize(row["text"]).text == row["text"]
        assert parse(row["raw"]).raw == row["raw"]
    assert len(load_default_table()) == len(rows)
