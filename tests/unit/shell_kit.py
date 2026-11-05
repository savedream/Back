"""셸 시험 도구: 시험용 스피커·장치, 처리가 멈출 때까지 기다리기, 엔진 서비스를 띄운 ShellKit."""
import asyncio
import time

from app.fakes.fake_model import FakeModel
from app.schemas.device import DeviceCommand, Location
from app.schemas.event import Event
from app.schemas.utterance import Utterance
from app.services.devices import DeviceOutcome
from app.services.events import EventBus
from app.services.procedure_engine.clock import FakeClock
from app.services.procedure_engine.service import EngineService
from app.services.records import MemoryRecordStore
from tests.unit.engine_kit import DB, FIX, user_turn


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

    async def speak(self, utterance: Utterance, volume: int, on_sound) -> str:
        self.spoken.append(utterance)
        self.timeline.append(("say", utterance.utterance_id))
        on_sound()
        if self._gate is not None:
            await self._gate.wait()
        if self.fail:
            raise RuntimeError("TTS 실패 시험")
        return "done"


class ScriptDevices:
    """받은 명령을 남기고 바로 success. hang이면 응답하지 않는다(시간 초과 시험용). 음소거 관문 호출도 남긴다."""

    def __init__(self, timeline: list | None = None):
        self.commands: list[DeviceCommand] = []
        self.timeline = timeline if timeline is not None else []
        self.hang = False
        self.held: set[int] = set()
        self.holds: list[tuple[int, bool]] = []

    def set_hold(self, obj: int, held: bool) -> None:
        self.holds.append((obj, held))
        if held:
            self.held.add(obj)
        else:
            self.held.discard(obj)

    async def execute(self, command: DeviceCommand) -> DeviceOutcome:
        self.commands.append(command)
        self.timeline.append(("device", command.object, command.type))
        if self.hang:
            await asyncio.Event().wait()
        return DeviceOutcome(status="success")


async def until_idle(*parts, timeout: float = 2.0) -> None:
    """엔진·낭독 실행기가 모두 쉴 때까지 이벤트 루프를 돌린다. 남은 마감 시각은 기다리지 않는다.

    기록 쓰기 스레드처럼 루프 밖에서 끝나는 일이 있어 돈 횟수가 아니라 실제 시간(timeout)으로 끊는다.
    """
    end = time.monotonic() + timeout
    while not all(p.idle for p in parts):
        if time.monotonic() > end:
            raise AssertionError("처리가 끝나지 않음")
        await asyncio.sleep(0)


async def until_speaking(speaker: ScriptSpeaker, already: int = 0, limit: int = 1000) -> None:
    """이미 읽은 already개 다음 낭독이 시작될 때까지 이벤트 루프를 돌린다."""
    for _ in range(limit):
        if len(speaker.spoken) > already:
            return
        await asyncio.sleep(0)
    raise AssertionError("낭독이 시작되지 않음")


class ShellKit:
    """엔진 서비스를 FakeClock·이벤트 버스·메모리 기록·시험용 스피커·장치로 띄운다."""

    def __init__(self, location: Location | None = FIX):
        self.clock = FakeClock()
        self.location = location
        self.bus = EventBus(self.clock)
        self._sub = self.bus.subscribe(maxsize=0)  # 0: 크기 제한 없음
        self.speaker = ScriptSpeaker()
        self.devices = ScriptDevices()
        self.records = MemoryRecordStore()
        self.model = FakeModel()
        self.engine = EngineService(DB, self.clock, self.bus, self.speaker, self.devices, self.records,
                                    location=lambda: self.location)
        self.events: list[Event] = []
        self._turns = 0

    async def start(self) -> None:
        await self.engine.start()
        await self.settle()

    async def stop(self) -> None:
        await self.engine.stop()

    @property
    def state(self):
        return self.engine.state

    @property
    def spoken(self) -> list[Utterance]:
        """guide.say로 화면에 낸 문장(speak: false로 바로 끝낸 문장 포함)."""
        return [Utterance(**e.data) for e in self.events if e.type == "guide.say"]

    def turn(self, text: str):
        self._turns += 1
        return user_turn(self.state, text, self.model, f"t_{self._turns:04d}")

    async def settle(self) -> None:
        await until_idle(self.engine, self.engine.sequencer)
        while not self._sub.queue.empty():
            self.events.append(self._sub.queue.get_nowait())

    async def say(self, text: str, speak: bool = True) -> list[Utterance]:
        mark = len(self.spoken)
        await self.engine.submit(self.turn(text), speak=speak)
        await self.settle()
        return self.spoken[mark:]

    async def advance(self, seconds: float) -> list[Utterance]:
        mark = len(self.spoken)
        self.clock.advance(seconds)
        await self.settle()
        return self.spoken[mark:]
