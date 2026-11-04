"""처치 기록 4종 (명세서 공통 데이터 형식 Record). 키트가 시킨 것(kit_guidance)과 사용자가 했다고 말한 것(user_report)을 섞지 않는다."""
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.schemas.function_call import FunctionCall
from app.schemas.utterance import Utterance


class RecordBase(BaseModel):
    record_id: str
    session_id: str
    at: str  # ISO 8601, 키트 로컬 시각
    clock: Literal["gps", "rtc", "unsynced"] = "unsynced"
    turn_id: str | None
    saved: bool = False


class UserUtteranceRecord(RecordBase):
    kind: Literal["user_utterance"] = "user_utterance"
    text: str  # STT 원문(텍스트 턴이면 넣은 문장)
    normalized: str
    function_call: FunctionCall | None
    corrects: str | None = None  # 정정이면 원래 값을 넣은 기록 ID
    note: str | None = None


class KitGuidanceRecord(RecordBase):
    kind: Literal["kit_guidance"] = "kit_guidance"
    utterance: Utterance
    played: Literal["done", "cut", "text_only"]


class DeviceResultRecord(RecordBase):
    kind: Literal["device_result"] = "device_result"
    object: int
    type: int
    source: Literal["voice", "button", "engine"]
    result: str  # DeviceState.last_result 값 또는 queued
    step_id: str | None = None  # 엔진 출처면 그 단계
    detail: dict | str | None = None


class UserReportRecord(RecordBase):
    kind: Literal["user_report"] = "user_report"
    text: str
    report: str
    step_id: str
    basis: Literal["report_on_yes", "wait_answer"]
    from_record: str  # 근거가 된 user_utterance 기록


Record = Annotated[
    UserUtteranceRecord | KitGuidanceRecord | DeviceResultRecord | UserReportRecord,
    Field(discriminator="kind"),
]
