"""낭독 실행기: 순서, 끊기(replace), 장치·기록 되먹임, speak: false (계획 3-3·3-4, 명세서 GET /api/events 순서)."""
import asyncio

import pytest

from app.schemas.device import DeviceCommand
from app.schemas.record import UserReportRecord
from app.schemas.session import SessionState
from app.schemas.utterance import Utterance
from app.services.events import EventBus
from app.services.procedure_engine.actions import DeviceAction, EngineResult, RecordAction, SayAction
from app.services.procedure_engine.clock import FakeClock
from app.services.procedure_engine.inputs import DeviceResultInput, PlaybackInput, RecordSavedInput
from app.services.procedure_engine.sequencer import Sequencer
from app.services.records import MemoryRecordStore
from tests.unit.engine_kit import DB
from tests.unit.shell_kit import ScriptDevices, ScriptSpeaker, until_idle, until_speaking

STATE = SessionState(session_id="s_1", started_at="t")


def say(n: int, step_id: str | None = None) -> SayAction:
    if step_id:
        step = DB.steps_by_id[step_id]
        u = Utterance(utterance_id=f"u_{n}", role="question", text=step["tts"], screen=step["screen"], source="step",
                      manual_id=step["manual_id"], step_id=step_id, template_id=None)
    else:
        u = Utterance(utterance_id=f"u_{n}", role="notice", text="문장", screen=None, source="template",
                      manual_id=None, step_id=None, template_id="T_NOTED")
    return SayAction(action_id=f"x_{n}", utterance=u, record_id=f"r_{n}")


def device(n: int, obj: int = 2, typ: int = 1) -> DeviceAction:
    return DeviceAction(action_id=f"x_{n}", command=DeviceCommand(object=obj, type=typ, source="engine"))


def record(n: int, wants_result: bool = False) -> RecordAction:
    rec = UserReportRecord(record_id=f"r_{n}", session_id="s_1", at="t", turn_id=None, text="네", report="네",
                           step_id="EM01-15", basis="wait_answer", from_record="r_0")
    return RecordAction(action_id=f"x_{n}", record=rec, wants_result=wants_result)


def result(*actions, mode: str = "append", turn_id: str | None = "t_0001") -> EngineResult:
    return EngineResult(turn_id=turn_id, state=STATE, actions=list(actions), mode=mode)


class Rig:
    def __init__(self, device_timeout_s: float = 2.0):
        self.timeline: list = []
        self.fed: list = []
        self.speaker = ScriptSpeaker(self.timeline)
        self.devices = ScriptDevices(self.timeline)
        self.records = MemoryRecordStore()
        self.clock = FakeClock()
        self.bus = EventBus(self.clock)
        self.sub = self.bus.subscribe(maxsize=0)  # 0: 크기 제한 없음
        self.seq = Sequencer(DB, self.bus, self.speaker, self.devices, self.records, self.feed, lambda: STATE,
                             self.clock, device_timeout_s)

    async def feed(self, inp):
        self.fed.append(inp)
        if isinstance(inp, PlaybackInput):
            self.timeline.append(("finished", inp.action_id, inp.result))

    def events(self) -> list[tuple]:
        out = []
        while not self.sub.queue.empty():
            e = self.sub.queue.get_nowait()
            key = e.data.get("step_id") or e.data.get("state") if e.type != "guide.say" else e.data["utterance_id"]
            out.append((e.type, key))
        return out

    def finished(self) -> list[tuple]:
        return [(i.action_id, i.result) for i in self.fed if isinstance(i, PlaybackInput)]


@pytest.fixture
async def rig():
    r = Rig()
    r.seq.start()
    yield r
    await r.seq.stop()


async def test_says_in_order_with_screen_events(rig):
    rig.seq.submit(result(say(1), say(2, "EM03-01")))
    await until_idle(rig.seq)
    assert [u.utterance_id for u in rig.speaker.spoken] == ["u_1", "u_2"]
    assert rig.finished() == [("x_1", "done"), ("x_2", "done")]
    assert rig.events() == [("guide.say", "u_1"), ("speech.state", "speaking"), ("guide.step", "EM03-01"),
                            ("guide.say", "u_2"), ("speech.state", "armed")]


async def test_guide_say_carries_record_id_and_guide_step_fields(rig):
    rig.seq.submit(result(say(1, "EM03-01")))
    await until_idle(rig.seq)
    step, said = rig.sub.queue.get_nowait(), rig.sub.queue.get_nowait()
    assert step.data == {"manual_id": "EM-03", "step_id": "EM03-01", "kind": DB.steps_by_id["EM03-01"]["kind"],
                         "screen": DB.steps_by_id["EM03-01"]["screen"], "image": None}
    assert said.data["record_id"] == "r_1" and said.data["text"] == DB.steps_by_id["EM03-01"]["tts"]
    assert step.turn_id == said.turn_id == "t_0001"


async def test_same_step_again_does_not_repeat_guide_step(rig):
    rig.seq.submit(result(say(1, "EM03-04")))
    rig.seq.submit(result(say(2), say(3, "EM03-04")))
    await until_idle(rig.seq)
    assert [k for t, k in rig.events() if t == "guide.step"] == ["EM03-04"]


async def test_replace_cuts_remaining_says_but_runs_devices(rig):
    rig.speaker.hold()
    rig.seq.submit(result(say(1), device(2), say(3)))
    await until_speaking(rig.speaker)
    rig.seq.submit(result(say(4), mode="replace", turn_id="t_0002"))
    rig.speaker.release()
    await until_idle(rig.seq)
    assert rig.finished() == [("x_1", "cut"), ("x_3", "cut"), ("x_4", "done")]
    assert [u.utterance_id for u in rig.speaker.spoken] == ["u_1", "u_4"]  # 버린 문장은 읽지도 화면에 내지도 않음
    assert rig.devices.commands == [DeviceCommand(object=2, type=1, source="engine")]


async def test_append_keeps_current_batch(rig):
    rig.speaker.hold()
    rig.seq.submit(result(say(1), say(2)))
    await until_speaking(rig.speaker)
    rig.seq.submit(result(say(3)))
    rig.speaker.release()
    await until_idle(rig.seq)
    assert rig.finished() == [("x_1", "done"), ("x_2", "done"), ("x_3", "done")]


async def test_device_runs_after_preceding_say_and_does_not_block_next_say(rig):
    rig.devices.hang = True
    rig.seq.submit(result(say(1), device(2), say(3)))
    while len(rig.timeline) < 4:
        await asyncio.sleep(0)
    assert not rig.seq.idle  # 장치 응답을 기다리는 중
    rig.clock.advance(2)  # 응답 제한 2초(엔진 시계)
    await until_idle(rig.seq)
    assert rig.timeline[:4] == [("say", "u_1"), ("finished", "x_1", "done"), ("device", 2, 1), ("say", "u_3")]
    [res] = [i for i in rig.fed if isinstance(i, DeviceResultInput)]
    assert (res.action_id, res.object, res.status) == ("x_2", 2, "timeout")


async def test_record_result_is_reported_only_when_wanted(rig):
    rig.records.fail_next_save()
    rig.seq.submit(result(record(1, wants_result=True), record(2)))
    await until_idle(rig.seq)
    assert [(i.action_id, i.ok) for i in rig.fed if isinstance(i, RecordSavedInput)] == [("x_1", False)]
    assert [(r.record_id, r.saved) for r in rig.records.records] == [("r_1", False), ("r_2", True)]


async def test_speak_false_finishes_says_without_speaker(rig):
    batch = rig.seq.submit(result(say(1), say(2, "EM03-01")), speak=False)
    await batch.done
    assert rig.speaker.spoken == [] and rig.finished() == [("x_1", "done"), ("x_2", "done")]
    assert ("guide.say", "u_2") in rig.events()


async def test_tts_failure_is_text_only(rig):
    rig.speaker.fail = True
    rig.seq.submit(result(say(1)))
    await until_idle(rig.seq)
    assert rig.finished() == [("x_1", "text_only")]


async def test_empty_result_is_done_at_once(rig):
    assert rig.seq.submit(result()).done.done()


async def test_unexpected_error_still_reports_finished(rig, monkeypatch):
    def broken_publish(type, turn_id, data):
        raise RuntimeError("시험용 오류")

    monkeypatch.setattr(rig.bus, "publish", broken_publish)
    rig.seq.submit(result(say(1)))
    await until_idle(rig.seq)
    assert rig.finished() == [("x_1", "text_only")]


async def test_first_sound_marks_batch_once(rig):
    batch = rig.seq.submit(result(say(1), say(2)))
    await until_idle(rig.seq)
    assert batch.first_sound.done()


async def test_silent_batch_has_no_sound(rig):
    batch = rig.seq.submit(result(say(1)), speak=False)
    await batch.done
    assert not batch.first_sound.done()


async def test_stop_cancels_unanswered_device_call():
    r = Rig()
    r.seq.start()
    r.devices.hang = True
    r.seq.submit(result(device(1)))
    while not r.devices.commands:
        await asyncio.sleep(0)
    pending = set(r.seq._side)
    await r.seq.stop()
    assert pending and all(t.done() for t in pending)
