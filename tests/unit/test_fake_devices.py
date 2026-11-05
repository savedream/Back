"""가짜 기기: 바로 성공, 장치 없음, 실패·응답 없음 넣기."""
import asyncio

import pytest

from app.fakes.fake_devices import FakeDevices
from app.schemas.device import DeviceCommand


def cmd(obj: int, typ: int) -> DeviceCommand:
    return DeviceCommand(object=obj, type=typ, source="engine")


async def test_success_and_sound_state():
    devices = FakeDevices()
    assert (await devices.execute(cmd(2, 1))).status == "success"
    assert devices.on == {2}
    await devices.execute(cmd(2, 2))
    assert devices.on == set() and devices.commands == [cmd(2, 1), cmd(2, 2)]


async def test_unavailable_device():
    devices = FakeDevices()
    devices.unavailable.add(4)
    assert (await devices.execute(cmd(4, 1))).status == "unavailable"
    assert devices.on == set()


async def test_fail_next_once():
    devices = FakeDevices()
    devices.fail_next("failed")
    assert (await devices.execute(cmd(2, 1))).status == "failed"
    assert (await devices.execute(cmd(2, 1))).status == "success"


async def test_timeout_never_answers():
    devices = FakeDevices()
    devices.fail_next("timeout")
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(devices.execute(cmd(2, 1)), 0.01)


def test_set_hold_is_idempotent_and_recorded():
    devices = FakeDevices()
    devices.set_hold(4, True)
    devices.set_hold(4, True)
    assert devices.held == {4}
    devices.set_hold(4, False)
    assert devices.held == set() and devices.holds == [(4, True), (4, True), (4, False)]
