"""가짜 모델: 정규화한 문장 → 모델 출력 표.

표에 없는 문장은 형식 위반 출력을 내서 해석 실패(MODEL_PARSE_ERROR → T_RETRY) 경로를 탄다.
표는 앱(인스턴스)마다 따로 가진다. 테스트는 add()로 문장을 더한다.
"""
import json
from pathlib import Path

DEFAULT_TABLE_PATH = Path(__file__).with_name("fake_model_table.json")
UNKNOWN_OUTPUT = "<maum_9>"


def load_default_table() -> dict[str, str]:
    rows = json.loads(DEFAULT_TABLE_PATH.read_text(encoding="utf-8"))
    return {row["text"]: row["raw"] for row in rows}


class FakeModel:
    def __init__(self, table: dict[str, str] | None = None):
        self.table = load_default_table() if table is None else dict(table)
        self.calls = 0

    def add(self, text: str, raw: str) -> None:
        self.table[text] = raw

    async def run(self, text: str) -> str:
        self.calls += 1
        return self.table.get(text, UNKNOWN_OUTPUT)
