"""기기 버튼 API (명세서 "기기"): 구조신호, 음량, 타이머."""
import asyncio

import pytest

from app.services.procedure_engine.inputs import DeviceResultInput
from tests.scenario.kit import Kit
from tests.unit.shell_kit import ScriptSpeaker, until_speaking

DEVICE_KEYS = ["object", "name", "connected", "status", "held_by", "queued", "last_result", "last_error", "detail"]


@pytest.fixture
async def kit():
    async with Kit() as k:
        yield k


async def press(kit: Kit, path: str, **kwargs) -> dict:
    res = await kit.http.post(path, **kwargs)
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True
    return res.json()["data"]


def hold_speaker(kit: Kit) -> ScriptSpeaker:
    """낭독이 release() 전까지 끝나지 않는 스피커로 바꾼다(안내 문장을 읽는 중에 버튼을 누르는 시험)."""
    speaker = ScriptSpeaker()
    kit.parts.engine.sequencer._speaker = speaker
    speaker.hold()
    return speaker


async def reading_guidance(kit: Kit) -> ScriptSpeaker:
    """안내 문장을 읽는 중인 상태를 만든다."""
    speaker = hold_speaker(kit)
    await kit.say("사람이 쓰러졌는데 숨을 안 쉬어", speak=True, settle=False)
    await until_speaking(speaker)
    return speaker


def templates_since(kit: Kit, mark: int) -> list[str | None]:
    return [u.template_id for u in kit.spoken_since(mark)]


async def test_rescue_signal_on_shape_and_result(kit):
    mark = len(kit.spoken)
    data = await press(kit, "/api/rescue-signal/on")
    assert list(data) == ["turn_id", "result", "device"]
    assert data["turn_id"].startswith("t_") and data["result"] == "pending"
    assert list(data["device"]) == DEVICE_KEYS  # 엔진 내부 칸(want)은 내보내지 않는다
    assert (data["device"]["object"], data["device"]["name"], data["device"]["status"]) == (4, "rescue_signal",
                                                                                            "pending")
    await kit.settle()
    assert kit.parts.devices.on == {4}
    assert templates_since(kit, mark) == ["T_DEV_SOS_ON"]
    states = [e.data for e in kit.events if e.type == "device.state" and e.data["name"] == "rescue_signal"]
    assert states[-1]["status"] == "on" and states[-1]["last_result"] == "success"


async def test_rescue_signal_off_after_on(kit):
    await press(kit, "/api/rescue-signal/on")
    await kit.settle()
    mark = len(kit.spoken)
    data = await press(kit, "/api/rescue-signal/off")
    assert data["result"] == "pending" and data["device"]["status"] == "pending"
    await kit.settle()
    assert kit.parts.devices.on == set()
    assert templates_since(kit, mark) == ["T_DEV_SOS_OFF"]
    assert kit.state.devices["rescue_signal"].status == "off"


@pytest.mark.parametrize(("path", "template"), [("/api/rescue-signal/off", "T_DEV_ALREADY_OFF"),
                                                ("/api/rescue-signal/on", "T_DEV_ALREADY_ON")])
async def test_rescue_signal_already(kit, path, template):
    """이미 같은 상태면 already. 장치에 명령을 내지 않고 이미 그렇다는 문장만 읽는다."""
    if path.endswith("/on"):
        await press(kit, "/api/rescue-signal/on")
        await kit.settle()
    sent, mark = len(kit.parts.devices.commands), len(kit.spoken)
    data = await press(kit, path)
    assert data["result"] == "already"
    await kit.settle()
    assert len(kit.parts.devices.commands) == sent
    assert templates_since(kit, mark) == [template]


async def test_rescue_signal_queued_while_reading(kit):
    """안내 문장을 읽는 중에 누르면 queued. 읽던 문장이 끝난 뒤 확인 문장을 읽고 켜고, 원래 질문을 다시 묻는다."""
    speaker = await reading_guidance(kit)
    data = await press(kit, "/api/rescue-signal/on")
    assert data["result"] == "queued"
    assert data["device"]["queued"] == {"type": 1, "source": "button"}  # 6-10 queued(AC-E3)
    assert kit.parts.devices.on == set()  # 아직 켜지 않았다
    speaker.release()
    await kit.settle()
    assert kit.parts.devices.on == {4} and kit.state.devices["rescue_signal"].queued is None
    said = [u.template_id or u.step_id for u in speaker.spoken]
    question = kit.state.step_id
    assert said.index("T_DEV_SOS_ON") > said.index(question)  # 읽던 질문 뒤에 확인 문장
    assert said[-2:] == ["T_DEV_SOS_ON", question]  # 동작 뒤 질문 다시(6-10)


async def test_rescue_signal_off_cancels_queued_on(kit):
    """대기 중인 켜기에 끄기를 누르면 대기를 취소한다(6-10). 장치는 켜기·끄기 명령을 받지 않고, 확인 문장은 둘 다 읽는다."""
    speaker = await reading_guidance(kit)
    before, last_off = kit.state.devices["rescue_signal"].model_copy(), kit.state.last_off_device
    await press(kit, "/api/rescue-signal/on")
    data = await press(kit, "/api/rescue-signal/off")
    assert data["device"]["queued"] is None and data["device"]["status"] == before.status
    rescue = kit.state.devices["rescue_signal"]
    assert [c for c in kit.state.pending_devices.values() if c["object"] == 4] == []
    assert (rescue.want, rescue.status, kit.state.last_off_device) == (before.want, before.status, last_off)
    speaker.release()
    await kit.settle()
    assert [c for c in kit.parts.devices.commands if c.object == 4] == [] and kit.parts.devices.on == set()
    cancelled = [(r.object, r.type, r.source) for r in kit.records if r.kind == "device_result" and r.result == "cancelled"]
    assert cancelled == [(4, 1, "button")]
    said = [u.template_id for u in speaker.spoken]
    assert said.index("T_DEV_SOS_ON") < said.index("T_DEV_SOS_OFF")  # 이미 큐에 있던 켜기 확인 문장도 읽는다(기본값 2)


async def until_commanded(kit: Kit, obj: int, typ: int) -> None:
    for _ in range(1000):
        if any((c.object, c.type) == (obj, typ) for c in kit.parts.devices.commands):
            return
        await asyncio.sleep(0)
    raise AssertionError("장치 명령이 나가지 않음")


async def held_rescue_signal(kit: Kit) -> ScriptSpeaker:
    """구조신호를 켜 두고 안내 문장을 읽는 중(held_by tts, 음소거 관문 닫힘)."""
    await press(kit, "/api/rescue-signal/on")
    await kit.settle()
    devices = kit.parts.devices
    assert devices.on == {4} and devices.held == set()
    speaker = await reading_guidance(kit)
    assert kit.state.devices["rescue_signal"].held_by == "tts" and devices.held == {4}
    return speaker


@pytest.mark.parametrize("failure", ["failed", "timeout"])
async def test_rescue_signal_stays_muted_when_off_fails_while_held(kit, failure):
    """멈춘 동안 끄기 요청이 오고 장치가 실패하면 다시 울리지 않는다(⑦). 다음 성공으로 꺼짐이 확정되면 관문을 연다."""
    devices = kit.parts.devices
    speaker = await held_rescue_signal(kit)
    devices.fail_next(failure)
    await press(kit, "/api/rescue-signal/off")
    mark = len(devices.holds)
    speaker.release()
    await until_commanded(kit, 4, 2)
    await kit.advance(2)  # 장치 응답 제한
    rescue = kit.state.devices["rescue_signal"]
    assert (rescue.status, rescue.last_result, rescue.held_by) == ("unknown", failure, None)
    assert devices.on == {4} and devices.held == {4} and devices.holds[mark:] == []  # 관문을 닫아 둔다
    await press(kit, "/api/rescue-signal/off")  # 확인되지 않은(unknown) 기능이라 다시 명령한다
    await kit.settle()
    assert kit.state.devices["rescue_signal"].status == "off" and devices.on == set()
    assert devices.held == set() and devices.holds[mark:] == [(4, False)]


async def test_rescue_signal_stays_muted_after_disconnect_while_held(kit):
    """멈춘 동안 장치가 끊기면 안내가 끝나도 관문을 열지 않는다. 다시 연결돼 꺼짐(off)이 확정되면 연다."""
    devices = kit.parts.devices
    speaker = await held_rescue_signal(kit)
    mark = len(devices.holds)
    await kit.parts.engine.submit(DeviceResultInput(action_id=None, object=4, status="disconnected"))
    speaker.release()
    await kit.settle()
    assert kit.state.devices["rescue_signal"].held_by is None and devices.held == {4} and devices.holds[mark:] == []
    await kit.parts.engine.submit(DeviceResultInput(action_id=None, object=4, status="connected"))
    await kit.settle()
    assert kit.state.devices["rescue_signal"].status == "off" and devices.holds[mark:] == [(4, False)]


async def test_rescue_signal_speaker_unavailable(kit):
    kit.state.devices["rescue_signal"].connected = False
    res = await kit.http.post("/api/rescue-signal/on")
    assert res.status_code == 503
    assert res.json() == {"ok": False, "error": {"code": "DEVICE_UNAVAILABLE", "message": "스피커가 연결되지 않았습니다"}}
    assert kit.parts.devices.commands == []


@pytest.mark.parametrize("path", ["/api/rescue-signal/on", "/api/rescue-signal/off"])
def test_rescue_signal_before_ready_is_503(client, path):
    res = client.post(path)
    assert res.status_code == 503 and res.json()["error"]["code"] == "NOT_READY"


def volume_events(kit: Kit) -> list[dict]:
    return [e.data for e in kit.events if e.type == "status.changed" and "speaker" in e.data]


@pytest.mark.parametrize(("path", "volume", "template"), [("/api/volume/up", 80, "T_DEV_VOL_UP"),
                                                          ("/api/volume/down", 60, "T_DEV_VOL_DOWN")])
async def test_volume_shape_and_result(kit, path, volume, template):
    """기본 음량 70에서 한 번에 10씩. 응답에 바꿀 음량, 바뀐 음량은 status.changed로도."""
    mark = len(kit.spoken)
    data = await press(kit, path)
    assert data == {"turn_id": data["turn_id"], "result": "pending", "speaker": {"connected": True, "volume": volume}}
    await kit.settle()
    assert kit.state.volume == volume
    assert [c.object for c in kit.parts.devices.commands] == [5]
    assert templates_since(kit, mark) == [template]
    assert volume_events(kit) == [{"speaker": {"volume": volume}}]


@pytest.mark.parametrize(("path", "steps", "end"), [("/api/volume/up", 3, 100), ("/api/volume/down", 6, 10)])
async def test_volume_already_at_end(kit, path, steps, end):
    """끝값(10, 100)에서는 바꾸지 않고 already. 장치 명령과 status.changed도 없다."""
    for _ in range(steps):
        await press(kit, path)
        await kit.settle()
    assert kit.state.volume == end
    sent, changed = len(kit.parts.devices.commands), len(volume_events(kit))
    data = await press(kit, path)
    assert data["result"] == "already" and data["speaker"]["volume"] == end
    await kit.settle()
    assert len(kit.parts.devices.commands) == sent and len(volume_events(kit)) == changed


async def test_volume_queued_while_reading(kit):
    speaker = await reading_guidance(kit)
    data = await press(kit, "/api/volume/up")
    assert data["result"] == "queued"
    speaker.release()
    await kit.settle()
    assert kit.state.volume == 80
    assert "T_DEV_VOL_UP" in [u.template_id for u in speaker.spoken]


@pytest.mark.parametrize("path", ["/api/volume/up", "/api/volume/down"])
async def test_volume_speaker_unavailable(kit, path):
    kit.state.devices["rescue_signal"].connected = False
    res = await kit.http.post(path)
    assert res.status_code == 503 and res.json()["error"]["code"] == "DEVICE_UNAVAILABLE"
    assert kit.state.volume == 70


@pytest.mark.parametrize("path", ["/api/volume/up", "/api/volume/down"])
def test_volume_before_ready_is_503(client, path):
    res = client.post(path)
    assert res.status_code == 503 and res.json()["error"]["code"] == "NOT_READY"


async def test_timer_start_pause_resume(kit):
    """시작 → 30초 뒤 일시정지 → 이어서 재기. 응답의 timer는 엔진이 받아들인 뒤의 상태."""
    mark = len(kit.spoken)
    data = await press(kit, "/api/timer/start")
    assert list(data) == ["turn_id", "result", "timer"] and data["result"] == "pending"
    started_at = kit.state.timer.started_at
    assert data["timer"] == {"object": 3, "status": "running", "elapsed_s": 0, "started_at": started_at}
    assert started_at is not None
    await kit.settle()
    await kit.advance(30)

    data = await press(kit, "/api/timer/pause")
    assert data["result"] == "pending"
    assert data["timer"] == {"object": 3, "status": "paused", "elapsed_s": 30, "started_at": started_at}
    await kit.settle()
    await kit.advance(10)  # 일시정지 중에는 늘지 않는다

    data = await press(kit, "/api/timer/start")
    assert data["result"] == "pending"
    assert data["timer"] == {"object": 3, "status": "running", "elapsed_s": 30, "started_at": started_at}
    await kit.settle()
    assert templates_since(kit, mark) == ["T_DEV_TIMER_START", "T_DEV_TIMER_PAUSE", "T_DEV_TIMER_RESUME"]


@pytest.mark.parametrize(("path", "template"), [("/api/timer/start", "T_DEV_ALREADY_ON"),
                                                ("/api/timer/pause", "T_DEV_ALREADY_OFF")])
async def test_timer_already(kit, path, template):
    """도는 중에 start, 도는 중이 아닌데 pause면 already."""
    if path.endswith("/start"):
        await press(kit, "/api/timer/start")
        await kit.settle()
    before, mark = kit.state.timer, len(kit.spoken)
    data = await press(kit, path)
    assert data["result"] == "already" and data["timer"]["status"] == before.status
    await kit.settle()
    assert kit.state.timer == before
    assert templates_since(kit, mark) == [template]


async def test_timer_queued_while_reading(kit):
    speaker = await reading_guidance(kit)
    data = await press(kit, "/api/timer/start")
    assert data["result"] == "queued"
    speaker.release()
    await kit.settle()
    assert kit.state.timer.status == "running"
    assert "T_DEV_TIMER_START" in [u.template_id for u in speaker.spoken]


@pytest.mark.parametrize("path", ["/api/timer/start", "/api/timer/pause"])
def test_timer_before_ready_is_503(client, path):
    res = client.post(path)
    assert res.status_code == 503 and res.json()["error"]["code"] == "NOT_READY"


async def running_timer(kit: Kit, seconds: float) -> None:
    await press(kit, "/api/timer/start")
    await kit.settle()
    await kit.advance(seconds)


def timer_records(kit: Kit) -> list:
    return [r for r in kit.records if r.kind == "device_result" and (r.object, r.type) == (3, 4)]


async def test_timer_reset_saves_then_resets(kit):
    """지금 구간을 기록에 남기고 시작 전으로 돌린다. 저장 결과를 기다렸다가 done."""
    await running_timer(kit, 42)
    mark = len(kit.spoken)
    data = await press(kit, "/api/timer/reset", json={"confirm": True})
    assert data == {"turn_id": data["turn_id"], "result": "done",
                    "timer": {"object": 3, "status": "idle", "elapsed_s": 0, "started_at": None}}
    await kit.settle()
    [record] = timer_records(kit)
    assert record.saved and record.source == "button" and record.detail == {"elapsed_s": 42}
    assert templates_since(kit, mark) == ["T_DEV_TIMER_RESET"]


@pytest.mark.parametrize("kwargs", [{}, {"json": {}}, {"json": {"confirm": False}}, {"json": {"confirm": "true"}}])
async def test_timer_reset_needs_confirm(kit, kwargs):
    await running_timer(kit, 5)
    res = await kit.http.post("/api/timer/reset", **kwargs)
    assert res.status_code == 400
    assert res.json() == {"ok": False, "error": {"code": "CONFIRM_REQUIRED",
                                                 "message": "타이머 초기화는 confirm: true가 필요합니다"}}
    assert kit.state.timer.status == "running"


async def test_timer_reset_already_when_idle(kit):
    """저장할 구간이 없으면 already. 버튼을 누른 기록만 already로 남는다."""
    data = await press(kit, "/api/timer/reset", json={"confirm": True})
    assert data["result"] == "already" and data["timer"]["status"] == "idle"
    await kit.settle()
    assert [(r.result, r.detail) for r in timer_records(kit)] == [("already", None)]


async def test_timer_reset_save_failed_keeps_timer(kit):
    """저장에 실패하면 초기화하지 않고 T_DEV_TIMER_KEEP, 500 SAVE_FAILED."""
    await running_timer(kit, 20)
    mark = len(kit.spoken)
    kit.parts.records.fail_next_save("device_result")
    res = await kit.http.post("/api/timer/reset", json={"confirm": True})
    assert res.status_code == 500
    assert res.json() == {"ok": False, "error": {"code": "SAVE_FAILED",
                                                 "message": "기록을 저장하지 못해 타이머를 초기화하지 않았습니다"}}
    await kit.settle()
    assert kit.state.timer.status == "running" and kit.state.timer.elapsed_s(kit.clock.now()) == 20
    assert templates_since(kit, mark) == ["T_DEV_TIMER_KEEP"]


async def test_timer_reset_waits_for_reading(kit):
    """안내 문장을 읽는 중이면 저장은 그 뒤에 차례가 온다. 응답도 저장 결과가 나올 때까지 기다린다."""
    await running_timer(kit, 10)
    speaker = await reading_guidance(kit)
    reply = asyncio.ensure_future(kit.http.post("/api/timer/reset", json={"confirm": True}))
    for _ in range(50):
        await asyncio.sleep(0)
    assert not reply.done() and kit.state.timer.status == "running"
    speaker.release()
    res = await reply
    assert res.status_code == 200 and res.json()["data"]["result"] == "done"
    assert kit.state.timer.status == "idle"


def test_timer_reset_before_ready_is_503(client):
    res = client.post("/api/timer/reset", json={"confirm": True})
    assert res.status_code == 503 and res.json()["error"]["code"] == "NOT_READY"
