"""모델 출력을 파싱한 결과 (명세서 공통 데이터 형식 FunctionCall, Observation, 상황 코드)."""
from typing import NamedTuple

from pydantic import BaseModel

from app.schemas.device import DEVICE_TYPES

# 관찰 슬롯과 허용 값. -1은 "말하지 않음"
OBSERVATION_VALUES: dict[str, set[int]] = {
    "conscious": {1, 2, 0, -1},  # 정상, 저하, 없음
    "breathing": {1, 2, 0, -1},  # 정상, 곤란, 없음·비정상
    "bleeding": {1, 0, -1},  # 있음, 없음
    "movement": {1, 0, -1},  # 가능, 불가
    "scene_danger": {1, 0, -1},  # 위험, 안전
}

# 상황 코드 1~15는 EM-01~EM-15, -1은 EM-00(상황 불명확)
SITUATIONS = set(range(1, 16)) | {-1}


class FunctionSpec(NamedTuple):
    name: str  # DB dispatch의 function
    args: dict[str, set[int]]  # 인자 → 허용 값
    observation: bool  # 관찰값 다섯 키를 함께 내는지


FUNCTIONS: dict[int, FunctionSpec] = {
    0: FunctionSpec("report_situation", {"situation": SITUATIONS}, True),
    1: FunctionSpec("answer_check", {"answer": {1, 2, 3}}, True),  # 네, 아니요, 모름
    2: FunctionSpec("correct_observation", {}, True),
    3: FunctionSpec("repeat_guidance", {"scope": {1, 2, 3}, "speed": {0, 1}}, False),
    4: FunctionSpec("request_device_function", {"object": set(DEVICE_TYPES), "type": {1, 2, 3, 4}}, False),
    5: FunctionSpec("out_of_scope", {"type": {1, 2, 3, 4}}, False),  # 잡담·미지원, 신고 요청, 처치 질문, 구급대 도착
}


class Observation(BaseModel):
    conscious: int
    breathing: int
    bleeding: int
    movement: int
    scene_danger: int


class FunctionCall(BaseModel):
    func: int
    name: str
    args: dict[str, int]
    observation: Observation | None  # func 0~2는 다섯 키 모두, func 3~5는 None
    raw: str
