"""가짜 기기(부저 메트로놈·스피커 구조신호·음량). 실제 장치 없이 명령을 받아 바로 성공으로 답한다.

시험에서는 장치 없음(unavailable), 다음 명령 실패(failed), 응답 없음(timeout: 호출한 쪽의 시간 제한에 걸림)을 넣는다.
"""
import asyncio
from typing import Literal

from app.schemas.device import DeviceCommand
from app.services.devices import DeviceOutcome


class FakeDevices:
    def __init__(self) -> None:
        self.commands: list[DeviceCommand] = []
        self.on: set[int] = set()  # 지금 울리는 소리 기능(2 메트로놈, 4 구조신호)
        self.unavailable: set[int] = set()  # 연결되지 않은 장치
        self._next: list[str] = []

    def fail_next(self, status: Literal["failed", "timeout"]) -> None:
        self._next.append(status)

    async def execute(self, command: DeviceCommand) -> DeviceOutcome:
        self.commands.append(command)
        if command.object in self.unavailable:
            return DeviceOutcome(status="unavailable")
        if self._next:
            status = self._next.pop(0)
            if status == "timeout":
                await asyncio.Event().wait()  # 응답하지 않는다
            return DeviceOutcome(status="failed")
        if command.object in (2, 4):
            if command.type == 1:
                self.on.add(command.object)
            else:
                self.on.discard(command.object)
        return DeviceOutcome(status="success")
