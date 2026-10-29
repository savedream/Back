"""기기 명령 (명세서 공통 데이터 형식 DeviceCommand)."""
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
