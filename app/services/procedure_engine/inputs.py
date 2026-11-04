"""엔진 입력 (명세서 6-1 EngineInput, 계획 3-1).

사용자 턴은 말소리가 잡힌 시점의 epoch·step_id를 들고 온다. 다르면 답으로 쓰지 않고 현재 질문을 다시 묻는다.
낭독·장치·기록의 되먹임은 epoch가 아니라 그 동작의 action_id로 맞춘다.
"""
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.common.error_code import ErrorCode
from app.schemas.device import DeviceCommand, Location
from app.schemas.function_call import FunctionCall


class UserTurn(BaseModel):
    turn_id: str
    epoch: int
    step_id: str | None
    text: str  # STT 원문(텍스트 턴이면 넣은 문장)
    normalized: str


class FunctionCallInput(UserTurn):
    kind: Literal["function_call"] = "function_call"
    function_call: FunctionCall


class ModelErrorInput(UserTurn):
    """해석 실패(MODEL_*)와 인식 실패(STT_*)."""

    kind: Literal["model_error"] = "model_error"
    error_code: ErrorCode


class ProgressPhraseInput(UserTurn):
    """대기 단계에서 정규화한 문장이 진행 표현 목록과 같음(모델을 거치지 않음, 6-8)."""

    kind: Literal["progress_phrase"] = "progress_phrase"


class DeviceButtonInput(BaseModel):
    kind: Literal["device_button"] = "device_button"
    turn_id: str
    command: DeviceCommand


class RepeatButtonInput(BaseModel):
    kind: Literal["repeat_button"] = "repeat_button"
    turn_id: str
    scope: int = 2
    speed: int = 0


class LocationButtonInput(BaseModel):
    kind: Literal["location_button"] = "location_button"
    turn_id: str


class BargeInInput(BaseModel):
    """낭독 중 말하기 버튼."""

    kind: Literal["barge_in"] = "barge_in"
    turn_id: str


class PlaybackInput(BaseModel):
    """시퀀서: 문장 낭독 시작·끝·끊김."""

    kind: Literal["playback"] = "playback"
    action_id: str
    event: Literal["started", "finished"]
    result: Literal["done", "cut", "text_only"] | None = None  # finished일 때


class DeviceResultInput(BaseModel):
    """장치 응답·연결 변화. 엔진이 낸 명령의 응답이면 action_id가 있다."""

    kind: Literal["device_result"] = "device_result"
    action_id: str | None
    object: int
    status: Literal["success", "failed", "timeout", "unavailable", "connected", "disconnected"]
    detail: dict | None = None


class RecordSavedInput(BaseModel):
    """기록 저장 결과. 저장 성공이 분기 조건인 곳(타이머 초기화)에서 쓴다."""

    kind: Literal["record_saved"] = "record_saved"
    action_id: str
    ok: bool


class LocationUpdateInput(BaseModel):
    kind: Literal["location_update"] = "location_update"
    location: Location


class SpeechActivityInput(BaseModel):
    """말소리 감지 시작·끝·취소. 무응답·대기 타이머 정지와 알림 미루기 기준."""

    kind: Literal["speech_activity"] = "speech_activity"
    state: Literal["started", "ended", "cancelled"]


class MicInput(BaseModel):
    """마이크 연결·끊김(mic_event)과 일시정지 설정(mic_setting)."""

    kind: Literal["mic"] = "mic"
    connected: bool | None = None
    paused: bool | None = None


class TickInput(BaseModel):
    """셸이 가장 이른 마감 시각에 깨어나 넣는다."""

    kind: Literal["tick"] = "tick"


class SessionResetInput(BaseModel):
    kind: Literal["session_reset"] = "session_reset"
    turn_id: str


EngineInput = Annotated[
    FunctionCallInput
    | ModelErrorInput
    | ProgressPhraseInput
    | DeviceButtonInput
    | RepeatButtonInput
    | LocationButtonInput
    | BargeInInput
    | PlaybackInput
    | DeviceResultInput
    | RecordSavedInput
    | LocationUpdateInput
    | SpeechActivityInput
    | MicInput
    | TickInput
    | SessionResetInput,
    Field(discriminator="kind"),
]
