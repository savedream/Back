"""세션 이어가기 (명세서 6-14): 세션 ID 겹침, 전원을 켤 때 지난 세션, 상태 스냅샷. 같은 기록 DB로 앱을 두 번 띄워 시험한다."""
import sqlite3

from app.schemas.session import SessionState
from app.services.procedure_engine.clock import FakeClock
from app.services.procedure_engine.inputs import SessionResetInput
from tests.scenario.kit import Kit

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
