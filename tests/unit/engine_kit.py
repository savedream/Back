"""리듀서 시험 도구: 셸 없이 reduce()를 돌리며 낭독·장치·기록 되먹임을 바로 넣는다(speak: false와 같은 순서)."""
import re
from datetime import timedelta

from app.config import BACKEND_DIR
from app.fakes.fake_model import FakeModel
from app.schemas.device import LastKnown, Location
from app.schemas.utterance import Utterance
from app.services.manual_db import load_manual_db
from app.services.normalizer import normalize
from app.services.output_parser import ModelOutputError, parse
from app.services.procedure_engine.actions import DeviceAction, EngineResult, RecordAction, SayAction
from app.services.procedure_engine.clock import FakeClock
from app.services.procedure_engine.context import Ctx
from app.services.procedure_engine.core import new_session, reduce
from app.services.procedure_engine.engine_constants import PROGRESS_PHRASES
from app.services.procedure_engine.inputs import (
    DeviceResultInput, FunctionCallInput, ModelErrorInput, PlaybackInput, ProgressPhraseInput, RecordSavedInput,
    TickInput,
)
from app.services.procedure_engine.timers import next_deadline

DB = load_manual_db(BACKEND_DIR / "data" / "manual_db.json")
FIX = Location(fix=True, lat=37.402180, lon=127.679420, accuracy_m=8, satellites=7, read_at="t", age_s=1)
NO_FIX_LAST = Location(fix=False, last_known=LastKnown(lat=37.40216, lon=127.67938, read_at="t", age_s=312))


class EngineKit:
    def __init__(self, location: Location | None = FIX):
        self.clock = FakeClock()
        self.location = location
        self.model = FakeModel()
        self.save_ok = True
        self.spoken: list[Utterance] = []
        self.records: list = []
        self.devices: list = []
        self.events: list = []
        self.state = None
        self._apply(new_session(self.ctx()))

    def ctx(self) -> Ctx:
        return Ctx(db=DB, now=self.clock.now(), wall=self.clock.wall(), location=self.location)

    def feed(self, inp) -> EngineResult:
        res = reduce(self.state, inp, self.ctx())
        self._apply(res)
        return res

    def _apply(self, res: EngineResult) -> None:
        self.state = res.state
        self.events.extend(res.events)
        for action in res.actions:
            if isinstance(action, SayAction):
                self.spoken.append(action.utterance)
                self.feed(PlaybackInput(action_id=action.action_id, event="finished", result="done"))
            elif isinstance(action, DeviceAction):
                self.devices.append(action.command)
                self.feed(DeviceResultInput(action_id=action.action_id, object=action.command.object, status="success"))
            elif isinstance(action, RecordAction):
                self.records.append(action.record)
                if action.action_id == self.state.timer_reset_record:
                    self.feed(RecordSavedInput(action_id=action.action_id, ok=self.save_ok))

    def say(self, text: str) -> list[Utterance]:
        start = len(self.spoken)
        n = normalize(text).text
        base = {"turn_id": f"t_{len(self.records):04d}", "epoch": self.state.epoch, "step_id": self.state.step_id,
                "text": text, "normalized": n}
        step = DB.steps_by_id.get(self.state.step_id) if self.state.step_id else None
        if step is not None and step["kind"] == "wait" and n in PROGRESS_PHRASES:
            self.feed(ProgressPhraseInput(**base))
        else:
            raw = self.model.table.get(n, "<maum_9>")
            try:
                self.feed(FunctionCallInput(**base, function_call=parse(raw)))
            except ModelOutputError as e:
                self.feed(ModelErrorInput(**base, error_code=e.code))
        return self.spoken[start:]

    def advance(self, seconds: float) -> list[Utterance]:
        start = len(self.spoken)
        self.clock.advance(seconds)
        while (d := next_deadline(self.state)) is not None and d <= self.clock.now():
            self.feed(TickInput())
        return self.spoken[start:]


def ids(utterances: list[Utterance]) -> list[tuple[str, str]]:
    return [(u.source, u.template_id or u.step_id or u.manual_id) for u in utterances]


def assert_from_db(u: Utterance) -> None:
    """모든 안내 문장은 매뉴얼 DB 원문이다(템플릿은 중괄호 자리만 채움)."""
    if u.source == "step":
        assert u.text == DB.steps_by_id[u.step_id]["tts"]
    elif u.source == "intro":
        assert u.text == DB.manuals_by_id[u.manual_id]["intro_tts"]
    elif u.source == "alert":
        assert u.text == DB.steps_by_id[u.step_id]["alert_tts"]
    else:
        pattern = re.escape(DB.templates_by_id[u.template_id]["tts"])
        pattern = re.sub(r"\\\{[a-z_]+\\\}", "[가-힣 ,]+", pattern)
        assert re.fullmatch(pattern, u.text), (u.template_id, u.text)
        assert not re.search(r"[0-9A-Za-z/]", u.text)


__all__ = ["EngineKit", "ids", "assert_from_db", "FIX", "NO_FIX_LAST", "timedelta"]
