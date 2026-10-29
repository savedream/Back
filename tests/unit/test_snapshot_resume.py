"""세션 이어가기 (명세서 6-14): 세션 ID 겹침, 전원을 켤 때 지난 세션, 상태 스냅샷. 같은 기록 DB로 앱을 두 번 띄워 시험한다."""
import sqlite3
from datetime import timedelta

import pytest

from app.schemas.session import SessionState
from app.services.procedure_engine.clock import FakeClock
from app.services.procedure_engine.context import Ctx
from app.services.procedure_engine.core import resume_session
from app.services.procedure_engine.inputs import SessionResetInput
from tests.scenario.kit import Kit, ids
from tests.unit.engine_kit import DB, EngineKit

START = FakeClock().wall()


async def at_s5b(kit: Kit) -> None:
    """가슴압박 대기(EM01-15): 메트로놈 켜짐, 타이머 돎, 2분 알림(끝나면 EM01-10)."""
    await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    await kit.say("아니요 대답 없어요")
    await kit.say("아니요")


def snapshots(path) -> list[tuple]:
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT session_id, saved_wall, saved_now, boot_id, json FROM snapshots "
                            "ORDER BY rowid").fetchall()


async def test_same_minute_session_ids_get_suffix(tmp_path):
    path = tmp_path / "records.db"
    async with Kit(records_db_path=path) as kit:
        first = kit.state.session_id
        assert first == "s_20261013_1400" and kit.state.previous_session_id is None
        await kit.parts.engine.submit(SessionResetInput(turn_id="t_reset"), speak=False)
        assert kit.state.session_id == "s_20261013_1400_2" and kit.state.previous_session_id == first
    async with Kit(records_db_path=path) as kit:  # 같은 분에 다시 켬
        assert kit.state.session_id == "s_20261013_1400_3"
        assert kit.state.previous_session_id == "s_20261013_1400_2"  # 세션 표의 마지막 세션
        sessions = await kit.parts.records.run(lambda c: c.execute("SELECT session_id, previous_session_id "
                                                                   "FROM sessions ORDER BY rowid").fetchall())
        assert sessions == [("s_20261013_1400", None), ("s_20261013_1400_2", "s_20261013_1400"),
                            ("s_20261013_1400_3", "s_20261013_1400_2")]


async def test_snapshot_is_saved_whenever_state_changes(tmp_path):
    path, boot = tmp_path / "records.db", tmp_path / "boot_id"
    boot.write_text("boot-1\n")
    async with Kit(records_db_path=path, boot_id_path=boot) as kit:
        first = kit.state
        await kit.parts.engine.submit(SessionResetInput(turn_id="t_reset"), speak=False)
        await at_s5b(kit)
        await kit.advance(30)
        last = kit.state
    rows = snapshots(path)  # 세션마다 최신 하나. 닫을 때 밀린 쓰기를 마친다
    assert [r[0] for r in rows] == [first.session_id, last.session_id]
    session_id, saved_wall, saved_now, boot_id, data = rows[-1]
    assert SessionState.model_validate_json(data) == last and boot_id == "boot-1"
    assert saved_wall == START.timestamp() + saved_now  # 같은 시각의 벽시계와 엔진 시계
    assert SessionState.model_validate_json(rows[0][4]).session_id == first.session_id


def boot_file(tmp_path, boot_id: str | None):
    """boot_id 파일. None이면 없는 파일(개발 PC처럼 읽지 못함)."""
    path = tmp_path / "boot_id"
    path.unlink(missing_ok=True)
    if boot_id is not None:
        path.write_text(boot_id + "\n")
    return path


def relaunch(tmp_path, after_s: float, boot_id: str | None = "boot-1", resume: bool = True) -> Kit:
    """같은 기록 DB로 앱을 다시 띄운다. 벽시계는 첫 실행을 시작한 뒤 after_s초, 엔진 시계는 0부터."""
    return Kit(FakeClock(start_wall=START + timedelta(seconds=after_s)), records_db_path=tmp_path / "records.db",
               boot_id_path=boot_file(tmp_path, boot_id), resume_from_snapshot=resume)


async def cpr_then_power_off(tmp_path, boot_id: str | None = "boot-1") -> tuple[SessionState, list[int]]:
    """가슴압박 대기에서 30초 지난 뒤 백엔드를 끈다. (마지막 상태, 기록 번호들)."""
    async with relaunch(tmp_path, 0, boot_id) as kit:
        await at_s5b(kit)
        await kit.advance(30)
        return kit.state, [int(r.record_id[2:]) for r in kit.records]


async def test_same_boot_resumes_from_snapshot(tmp_path):
    before, numbers = await cpr_then_power_off(tmp_path)
    remaining = before.alerts[0].deadline - 30
    assert remaining == 90
    async with relaunch(tmp_path, 30 + 10) as kit:  # 10초 끊김
        s = kit.state
        assert s.session_id == before.session_id and s.step_id == "EM01-15" and s.flags == before.flags
        assert ids(kit.spoken) == [("template", "T_RESUME"), ("step", "EM01-09")]  # wait: 마지막으로 읽은 단계
        assert kit.parts.devices.on == {2} and s.devices["metronome"].status == "on"  # 메트로놈을 다시 켬
        assert s.alerts[0].deadline == kit.clock.now() + remaining - 10  # 끊긴 시간만큼 지난 것으로
        assert s.timer.elapsed_s(kit.clock.now()) == 40
        records = kit.parts.records
        assert min(int(r.record_id[2:]) for r in kit.records) > max(numbers)  # 기록 번호를 이어서 쓴다
        assert records.unsaved() == [] and all(r.saved for r in kit.records)
        logged = await records.session_records(s.session_id)  # 끊기기 전 기록부터 이어진다
        assert [int(r.record_id[2:]) for r in logged] == numbers + [int(r.record_id[2:]) for r in kit.records]
        sessions = await records.run(lambda c: c.execute("SELECT session_id FROM sessions").fetchall())
        assert sessions == [(before.session_id,)]  # 이어 가는 세션은 새 행을 만들지 않는다
        assert await kit.advance(remaining - 10 - 1) == []
        assert ids(await kit.advance(1)) == [("step", "EM01-10"), ("step", "EM01-11")]


async def test_alert_that_ended_while_down_is_handled_at_once(tmp_path):
    before, _ = await cpr_then_power_off(tmp_path)
    async with relaunch(tmp_path, 30 + 100) as kit:  # 남은 90초보다 오래 끊김
        assert kit.state.step_id == "EM01-11" and kit.state.alerts == []
        assert ("step", "EM01-10") in ids(kit.spoken)
        ended = [e.data for e in kit.events if e.type == "alert.ended"]
        assert ended == [{"alert_id": before.alerts[0].alert_id, "reason": "timeout"}]


async def test_resume_rereads_question(tmp_path):
    async with relaunch(tmp_path, 0) as kit:
        await kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
        before = kit.state
    async with relaunch(tmp_path, 20) as kit:
        assert ids(kit.spoken) == [("template", "T_RESUME"), ("step", before.step_id)]
        assert kit.state.no_speech.deadline == kit.clock.now() + 15  # 다시 읽은 질문부터 무응답을 잰다


@pytest.mark.parametrize("first, second, resume", [("boot-1", "boot-2", True), ("boot-1", None, True),
                                                   (None, None, True), ("boot-1", "boot-1", False)])
async def test_new_session_unless_same_known_boot(tmp_path, first, second, resume):
    before, _ = await cpr_then_power_off(tmp_path, first)
    async with relaunch(tmp_path, 40, second, resume) as kit:
        s = kit.state
        assert s.session_id == before.session_id + "_2" and s.previous_session_id == before.session_id
        assert ids(kit.spoken) == [("template", "T_WELCOME")]
        assert kit.parts.devices.on == set() and s.alerts == [] and s.step_id is None


def test_resume_drops_cut_reading_and_starts_its_alert_now():
    kit = EngineKit()
    kit.say("사람이 쓰러졌는데 숨을 안 쉬어")
    kit.say("아니요 대답 없어요")
    kit.hold()  # 가슴압박 안내를 읽는 중에 끊긴다
    kit.say("아니요")
    before = kit.state
    assert before.alerts[0].waits_for_action in before.pending_says
    ctx = Ctx(db=DB, now=7.0, wall=kit.clock.wall() + timedelta(seconds=3))
    s = resume_session(before, ctx, saved_now=kit.clock.now(), downtime_s=3).state
    assert s.alerts[0].deadline == 7.0 + 120 and s.alerts[0].waits_for_action is None  # 끊긴 문장 대신 지금부터 잰다
    said = [p.utterance.template_id or p.utterance.step_id for p in s.pending_says.values()]
    assert said == ["T_RESUME", before.spoken_step_id] and s.step_id == "EM01-15"
    assert s.counters.record > before.counters.record


@pytest.mark.parametrize("broken", ["{", "unknown_step"])
async def test_unusable_snapshot_opens_new_session(tmp_path, broken):
    before, _ = await cpr_then_power_off(tmp_path)
    data = before.model_copy(update={"step_id": "NOPE-01"}).model_dump_json() if broken == "unknown_step" else "{"
    conn = sqlite3.connect(tmp_path / "records.db")
    conn.execute("UPDATE snapshots SET json = ?", (data,))
    conn.commit()
    conn.close()
    async with relaunch(tmp_path, 40) as kit:  # 읽지 못하거나 이어 가지 못하면 새 세션
        assert kit.state.session_id == before.session_id + "_2" and kit.state.previous_session_id == before.session_id
        assert ids(kit.spoken) == [("template", "T_WELCOME")]
