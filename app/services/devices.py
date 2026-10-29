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
