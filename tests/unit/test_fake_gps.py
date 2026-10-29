"""가짜 GPS: 위치 잡힘, 놓침(마지막 위치), 처음부터 못 잡음, 장치 없음, 바뀔 때 알림."""
from app.fakes.fake_gps import DEFAULT_FIX, FakeGps
from app.services.procedure_engine.clock import FakeClock


def test_fix_by_default():
    loc = FakeGps(FakeClock()).location()
    assert (loc.fix, loc.lat, loc.lon, loc.last_known) == (True, *DEFAULT_FIX, None)
    assert loc.read_at == "2026-10-13T14:00:00+09:00"


def test_lost_fix_keeps_last_known_with_age():
    clock = FakeClock()
    gps = FakeGps(clock)
    gps.lose_fix()
    clock.advance(312)
    loc = gps.location()
    assert loc.fix is False and loc.lat is None
    assert (loc.last_known.lat, loc.last_known.age_s, loc.last_known.read_at) == (
        DEFAULT_FIX[0], 312, "2026-10-13T14:00:00+09:00")


def test_never_fixed_has_no_last_known():
    loc = FakeGps(FakeClock(), fix=None).location()
    assert loc.fix is False and loc.last_known is None


def test_no_gps_device():
    gps = FakeGps(FakeClock())
    gps.present = False
    assert gps.location() is None


def test_listeners_hear_changes():
    gps = FakeGps(FakeClock(), fix=None)
    heard = []
    gps.listeners.append(heard.append)
    gps.set_fix(37.5, 127.0)
    gps.lose_fix()
    assert [(loc.fix, loc.lat) for loc in heard] == [(True, 37.5), (False, None)]
