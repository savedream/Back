"""가짜 종료 훅·배터리 (명세서 POST /api/system/shutdown, GET /api/devices, AC-D3·D4)."""
from app.fakes.fake_system import FakeBattery, FakeShutdown
from tests.scenario.kit import Kit


async def test_fake_shutdown_marks_request_and_resumes():
    resumed = []
    hook = FakeShutdown(lambda: resumed.append(True))
    assert hook.requested is False
    await hook.shutdown()
    assert hook.requested is True and resumed == [True]


def test_fake_battery_cannot_be_read():
    assert FakeBattery().read() is None


async def test_parts_fake_shutdown_unfreezes_engine():
    async with Kit() as kit:
        kit.parts.engine.freeze()
        await kit.parts.shutdown.shutdown()
        assert kit.parts.shutdown.requested and not kit.parts.engine.frozen
