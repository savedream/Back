"""엔진 시계. 경과 시간·알림·무응답은 시스템 시계가 아니라 단조 증가 시계로 잰다(명세서 공통 규칙 "시간 길이").

시험에서는 FakeClock을 끼워 advance()로 시간을 진행한다(실제로 기다리지 않음).
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """단조 증가 초."""
        ...

    def wall(self) -> datetime:
        """키트 로컬 벽시계(시간대 포함). 기록·화면의 ISO 시각용."""
        ...

    async def sleep_until(self, deadline: float) -> None: ...


def iso(dt: datetime) -> str:
    """명세서 시각 형식: 2026-10-13T14:03:22+09:00"""
    return dt.isoformat(timespec="seconds")


class SystemClock:
    def now(self) -> float:
        return time.monotonic()

    def wall(self) -> datetime:
        return datetime.now().astimezone()

    async def sleep_until(self, deadline: float) -> None:
        await asyncio.sleep(max(0.0, deadline - self.now()))


KST = timezone(timedelta(hours=9))


class FakeClock:
    def __init__(self, start_wall: datetime | None = None):
        self._now = 0.0
        self._start_wall = start_wall or datetime(2026, 10, 13, 14, 0, 0, tzinfo=KST)
        self._changed = asyncio.Event()

    def now(self) -> float:
        return self._now

    def wall(self) -> datetime:
        return self._start_wall + timedelta(seconds=self._now)

    def advance(self, seconds: float) -> None:
        self._now += seconds
        self._changed.set()

    async def sleep_until(self, deadline: float) -> None:
        while self._now < deadline:
            self._changed.clear()
            await self._changed.wait()
