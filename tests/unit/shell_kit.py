"""셸 시험 도구: 시험용 스피커·장치와 처리가 멈출 때까지 기다리기."""
import asyncio

from app.schemas.device import DeviceCommand
from app.schemas.utterance import Utterance
from app.services.devices import DeviceOutcome


class ScriptSpeaker:
    """읽은 문장을 남긴다. hold()를 걸면 release()까지 낭독이 끝나지 않는다(끊기 시험용)."""

    def __init__(self, timeline: list | None = None):
        self.spoken: list[Utterance] = []
        self.timeline = timeline if timeline is not None else []
        self.fail = False
        self._gate: asyncio.Event | None = None

    def hold(self) -> None:
        self._gate = asyncio.Event()

    def release(self) -> None:
        if self._gate is not None:
            self._gate.set()
            self._gate = None

    async def speak(self, utterance: Utterance, volume: int) -> str:
        self.spoken.append(utterance)
        self.timeline.append(("say", utterance.utterance_id))
        if self._gate is not None:
            await self._gate.wait()
        if self.fail:
            raise RuntimeError("TTS 실패 시험")
        return "done"


class ScriptDevices:
    """받은 명령을 남기고 바로 success. hang이면 응답하지 않는다(시간 초과 시험용)."""

    def __init__(self, timeline: list | None = None):
        self.commands: list[DeviceCommand] = []
        self.timeline = timeline if timeline is not None else []
        self.hang = False

    async def execute(self, command: DeviceCommand) -> DeviceOutcome:
        self.commands.append(command)
        self.timeline.append(("device", command.object, command.type))
        if self.hang:
            await asyncio.Event().wait()
        return DeviceOutcome(status="success")


async def until_idle(*parts, limit: int = 1000) -> None:
    """엔진·낭독 실행기가 모두 쉴 때까지 이벤트 루프를 돌린다. 남은 마감 시각은 기다리지 않는다."""
    for _ in range(limit):
        if all(p.idle for p in parts):
            return
        await asyncio.sleep(0)
    raise AssertionError("처리가 끝나지 않음")


async def until_speaking(speaker: ScriptSpeaker, limit: int = 1000) -> None:
    """낭독이 하나 시작될 때까지 이벤트 루프를 돌린다."""
    for _ in range(limit):
        if speaker.spoken:
            return
        await asyncio.sleep(0)
    raise AssertionError("낭독이 시작되지 않음")
