"""기기 명령·상태·위치 (명세서 공통 데이터 형식 DeviceCommand, DeviceState, TimerState, Location)."""
from typing import Literal

from pydantic import BaseModel

# object → 허용 type
DEVICE_TYPES: dict[int, set[int]] = {
    1: {1},  # GPS: 현재 위치 읽기
    2: {1, 2},  # 메트로놈(부저): 켜기, 끄기
    3: {1, 2, 3, 4},  # 타이머: 시작·재개, 경과 확인, 일시정지, 초기화
    4: {1, 2},  # 구조신호(스피커): 켜기, 끄기
    5: {1, 2},  # 음량(안내 음성): 올리기, 내리기
    -1: {1, 2},  # 대상 없음(소리 기능만): 켜기, 끄기
}


class DeviceCommand(BaseModel):
    """음성(모델 func 4)·버튼(화면)·엔진(단계 device) 공용 기기 명령."""

    object: int
    type: int
    source: Literal["voice", "button", "engine"]


DeviceStatus = Literal["on", "off", "pending", "unknown"]
DeviceResultValue = Literal["success", "failed", "timeout", "unavailable", "already", "cancelled"]


class DeviceState(BaseModel):
    """소리 기능(메트로놈·구조신호) 상태. 장치 응답을 확인하기 전에는 성공으로 표시하지 않는다."""

    object: int  # 2 메트로놈 · 4 구조신호
    name: Literal["metronome", "rescue_signal"]
    connected: bool = True
    status: DeviceStatus = "off"
    held_by: Literal["tts", "recording"] | None = None
    queued: dict | None = None  # 지금 읽는 문장이 끝나면 실행할 요청 {"type", "source"}
    last_result: DeviceResultValue | None = None
    last_error: str | None = None
    detail: dict = {}


class TimerState(BaseModel):
    """경과 시간 타이머(하나). 시간은 엔진 시계(단조 증가 초) 기준이고 화면용 elapsed_s·started_at은 API에서 만든다."""

    status: Literal["idle", "running", "paused"] = "idle"
    accumulated_s: float = 0.0  # 일시정지 전까지 잰 시간
    running_since: float | None = None  # running이면 이번 측정 구간 시작 시각
    started_at: str | None = None  # 처음 시작한 벽시계 시각(ISO 8601)

    def elapsed_s(self, now: float) -> int:
        running = now - self.running_since if self.running_since is not None else 0.0
        return int(self.accumulated_s + running)


class LastKnown(BaseModel):
    lat: float
    lon: float
    read_at: str
    age_s: int


class Location(BaseModel):
    """GPS 위치. fix가 false면 lat·lon은 None이고 last_known에 마지막으로 잡은 위치."""

    fix: bool
    lat: float | None = None
    lon: float | None = None
    accuracy_m: int | None = None
    satellites: int | None = None
    read_at: str | None = None
    age_s: int | None = None
    last_known: LastKnown | None = None
