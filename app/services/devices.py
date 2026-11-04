"""⑦ 기기 제어 자리. 지금은 가짜(app/fakes)만 있고, 실제 부저·스피커는 키트 단계에서 같은 Protocol로 끼운다."""
from typing import Literal, Protocol

from pydantic import BaseModel

from app.schemas.device import DeviceCommand


class DeviceOutcome(BaseModel):
    status: Literal["success", "failed", "unavailable"]
    detail: dict | None = None


class DeviceController(Protocol):
    async def execute(self, command: DeviceCommand) -> DeviceOutcome:
        """명령을 실행하고 장치 응답을 돌려준다. 응답 시간 제한(device_timeout_s)은 호출하는 쪽이 건다."""
        ...

    def set_hold(self, obj: int, held: bool) -> None:
        """소리 기능(2 메트로놈, 4 구조신호)의 음소거 관문(held_by, ⑦ 소리 충돌). 닫혀 있으면 켜져 있어도 소리가 나지 않는다.
        바로 끝나고 같은 값을 다시 불러도 그대로이며, 장치 명령 전후 어느 때 불러도 된다."""
        ...
