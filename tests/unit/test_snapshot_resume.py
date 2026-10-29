"""세션 이어가기 (명세서 6-14): 세션 ID 겹침, 전원을 켤 때 지난 세션. 같은 기록 DB로 앱을 두 번 띄워 시험한다."""
from app.services.procedure_engine.inputs import SessionResetInput
from tests.scenario.kit import Kit


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
