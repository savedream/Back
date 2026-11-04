"""절차 엔진 1층: 데이터 형식, 시계, 엔진 상수 (명세서 공통 데이터 형식, 6-1)."""
import asyncio

from pydantic import TypeAdapter

from app.config import BACKEND_DIR
from app.schemas.device import TimerState
from app.schemas.record import Record, UserReportRecord
from app.schemas.session import SessionState
from app.services.manual_db import load_manual_db
from app.services.normalizer import normalize
from app.services.procedure_engine import engine_constants as C
from app.services.procedure_engine.clock import FakeClock, iso
from app.services.procedure_engine.inputs import EngineInput, FunctionCallInput, TickInput


def test_new_session_defaults():
    s = SessionState(session_id="s_20261013_1400", started_at="2026-10-13T14:00:00+09:00")
    assert (s.mode, s.step_id, s.epoch, s.flags, s.pending_prompt) == ("idle", None, 0, [], None)
    assert s.observation == {k: None for k in ["conscious", "breathing", "bleeding", "movement", "scene_danger"]}
    assert {k: (d.status, d.connected) for k, d in s.devices.items()} == {
        "metronome": ("off", True),
        "rescue_signal": ("off", True),
    }
    assert s.timer.status == "idle"


def test_sessions_do_not_share_mutable_defaults():
    a = SessionState(session_id="s_a", started_at="t")
    b = SessionState(session_id="s_b", started_at="t")
    a.flags.append("cpr")
    a.devices["metronome"].status = "on"
    assert b.flags == [] and b.devices["metronome"].status == "off"


def test_timer_elapsed():
    t = TimerState(status="running", accumulated_s=30, running_since=100.0)
    assert t.elapsed_s(now=112.5) == 42


def test_engine_input_is_chosen_by_kind():
    adapter = TypeAdapter(EngineInput)
    assert isinstance(adapter.validate_python({"kind": "tick"}), TickInput)
    fc = adapter.validate_python({
        "kind": "function_call", "turn_id": "t_0001", "epoch": 0, "step_id": None, "text": "네", "normalized": "네",
        "function_call": {"func": 1, "name": "answer_check", "args": {"answer": 1}, "observation": None, "raw": ""},
    })
    assert isinstance(fc, FunctionCallInput)


def test_record_is_chosen_by_kind():
    r = TypeAdapter(Record).validate_python({
        "kind": "user_report", "record_id": "r_0046", "session_id": "s_1", "at": "t", "turn_id": "t_0004",
        "text": "누르고 있어요", "report": "누르고 있어요", "step_id": "EM01-15", "basis": "wait_answer", "from_record": "r_0045",
    })
    assert isinstance(r, UserReportRecord) and r.saved is False


async def test_fake_clock_advances_without_waiting():
    clock = FakeClock()
    assert iso(clock.wall()) == "2026-10-13T14:00:00+09:00"
    woke = asyncio.Event()

    async def sleeper():
        await clock.sleep_until(15)
        woke.set()

    task = asyncio.create_task(sleeper())
    await asyncio.sleep(0)
    clock.advance(10)
    await asyncio.sleep(0)
    assert not woke.is_set()
    clock.advance(5)
    await asyncio.wait_for(task, timeout=1)
    assert clock.now() == 15 and iso(clock.wall()) == "2026-10-13T14:00:15+09:00"


def test_engine_constants_exist_in_manual_db():
    db = load_manual_db(BACKEND_DIR / "data" / "manual_db.json")
    steps = [C.I1_STEP_WITH_CALL119, C.I1_STEP_WITHOUT_CALL119, C.I2_STEP, *C.I2_EXCLUDED_STEPS,
             C.OBSERVE_WAIT_STEP, C.OBSERVE_RECHECK_STEP, C.DISTRESS_WAIT_STEP]
    assert all(s in db.steps_by_id for s in steps)
    assert all(m in db.manuals_by_id for m in [C.CARDIAC_MANUAL, C.UNKNOWN_MANUAL, C.AED_MANUAL])
    assert db.manual_by_situation[C.CARDIAC_SITUATION]["manual_id"] == C.CARDIAC_MANUAL
    assert db.steps_by_id[C.OBSERVE_WAIT_STEP]["kind"] == "wait"


def test_progress_phrases_are_normalized():
    """진행 표현은 정규화한 문장 전체와 비교하므로 목록도 정규화된 모양이어야 한다."""
    assert all(normalize(p).text == p for p in C.PROGRESS_PHRASES)
