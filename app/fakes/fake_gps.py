"""가짜 GPS. 위치 잡힘·못 잡음(마지막 위치 있음/없음)·GPS 장치 없음을 바꿔 가며 시험한다.

위치가 바뀌면 listeners를 부른다. 앱은 여기에 엔진 입력(LocationUpdateInput)을 넣는 함수를 건다(GPS 재시도 중 잡히면 T_GPS_OK).
"""
from collections.abc import Callable

from app.schemas.device import LastKnown, Location
from app.services.procedure_engine.clock import Clock, iso

DEFAULT_FIX = (37.402180, 127.679420)  # 명세서 Location 예시값


class FakeGps:
    def __init__(self, clock: Clock, fix: tuple[float, float] | None = DEFAULT_FIX):
        self._clock = clock
        self.present = True  # False면 GPS 장치 없음(location()이 None)
        self._fix = fix
        self._last: tuple[float, float] | None = None
        self._lost_at: float | None = None
        self._lost_wall: str | None = None
        self.listeners: list[Callable[[Location], None]] = []

    def location(self) -> Location | None:
        if not self.present:
            return None
        if self._fix is not None:
            lat, lon = self._fix
            return Location(fix=True, lat=lat, lon=lon, accuracy_m=8, satellites=7, read_at=iso(self._clock.wall()),
                            age_s=0)
        last = None
        if self._last is not None:
            lat, lon = self._last
            last = LastKnown(lat=lat, lon=lon, read_at=self._lost_wall, age_s=int(self._clock.now() - self._lost_at))
        return Location(fix=False, last_known=last)

    def set_fix(self, lat: float = DEFAULT_FIX[0], lon: float = DEFAULT_FIX[1]) -> None:
        self.present = True
        self._fix = (lat, lon)
        self._notify()

    def lose_fix(self) -> None:
        """위치를 놓친다. 잡혀 있던 위치는 마지막 위치(last_known)로 남는다."""
        if self._fix is not None:
            self._last, self._lost_at, self._lost_wall = self._fix, self._clock.now(), iso(self._clock.wall())
        self._fix = None
        self._notify()

    def _notify(self) -> None:
        loc = self.location()
        if loc is not None:
            for listener in self.listeners:
                listener(loc)
