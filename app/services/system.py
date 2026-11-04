"""키트 전원·배터리 자리 (명세서 POST /api/system/shutdown, GET /api/devices). 실제 OS 종료·배터리 읽기는 키트 단계에서
같은 자리에 끼우고, 지금은 가짜(app/fakes/fake_system.py)만 둔다."""
from typing import Protocol


class ShutdownHook(Protocol):
    async def shutdown(self) -> None:
        """키트를 끈다. 기록을 다 저장하고 소리 기능을 끈 뒤, 종료 응답을 보낸 다음에 부른다."""
        ...


class BatteryReader(Protocol):
    def read(self) -> dict | None:
        """배터리 {percent, charging, low}. 못 읽으면 None(GET /api/devices의 battery: null)."""
        ...
