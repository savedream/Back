"""세션 상태 (명세서 공통 데이터 형식 ObservedValue, SessionState).

엔진 안에서 시간은 단조 증가 시계의 초로 다룬다(deadline). 화면에 내보낼 ISO 시각(ends_at, expires_at)은 API에서 만든다.
"""
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.schemas.device import DeviceState, TimerState
from app.schemas.function_call import OBSERVATION_VALUES, FunctionCall
from app.schemas.utterance import Utterance


class ObservedValue(BaseModel):
    """관찰값은 숫자 하나가 아니라 가능한 값 묶음과 출처로 저장한다(정정 되돌림, 질문 건너뛰기, 처치 기록)."""

    allowed: list[int]
    source: Literal["situation", "answer", "correction"]
    step_id: str | None
    record_id: str | None
    routing_no: int
    at: str


class StepFrame(BaseModel):
    """단계 call: return에서 돌아올 단계."""

    type: Literal["step"] = "step"
    return_to: str


class RoutingFrame(BaseModel):
    """라우팅 call(R4 → COM-SAFE): 돌아오면 이 규칙부터 라우팅을 이어서 평가한다."""

    type: Literal["routing"] = "routing"
    resume_rule: str
    situation: int


Frame = Annotated[StepFrame | RoutingFrame, Field(discriminator="type")]


class AnswerLogEntry(BaseModel):
    """슬롯으로 분기한 기록. 정정은 여기서 그 slot_key로 분기한 가장 최근 질문을 찾는다."""

    step_id: str
    slot: str
    slot_key: str
    allowed: list[int]
    how: Literal["answer", "skip"]
    branch: Literal["on_yes", "on_no", "on_unknown"]
    record_id: str | None
    at: str


class PendingPrompt(BaseModel):
    """걸려 있는 확인 질문 (6-7)."""

    type: Literal["timer_reset", "switch_confirm", "which_on", "which_off"]
    template_id: str
    origin_step_id: str | None
    target_manual_id: str | None = None  # switch_confirm의 새 매뉴얼
    deferred_branch: FunctionCall | None = None  # switch_confirm에 아니요면 이 입력으로 현재 질문 분기를 이어 감
    deadline: float | None = None  # 15초 무응답 마감. 확인 질문을 다 읽고 조용해지면 건다
    waits_for_action: str | None = None  # 아직 다 읽지 않은 확인 질문 say 액션


class PendingSay(BaseModel):
    """낭독이 아직 안 끝난 문장과 그 kit_guidance 기록 ID."""

    utterance: Utterance
    record_id: str
    turn_id: str | None = None  # 이 문장을 낸 턴. kit_guidance 기록에 쓴다(guide.say와 같은 값)
    call_stack: list[Frame] = []  # 이 문장을 낼 때의 call_stack. 말하기 버튼으로 이 단계에 되돌아올 때 되살린다
    dropped: bool = False  # 위치가 바뀌어(replace) 버려졌는데 아직 cut 되먹임이 오지 않은 문장


class Alert(BaseModel):
    """절차 알림 (6-12). 그 단계 문장의 낭독이 끝나야 잰다: 끝나기 전에는 deadline이 None."""

    alert_id: str
    step_id: str
    seconds: int
    on_timeout: str | None
    deadline: float | None = None
    waits_for_action: str | None = None  # 이 say 액션의 낭독이 끝나면 deadline을 건다
    log_index: int = 0  # 걸 때의 answer_log 길이. 정정으로 되돌린 지점 이후에 건 알림을 찾는 데 쓴다


class NoSpeech(BaseModel):
    """무응답 (6-9): 질문 낭독이 끝나고 마이크가 열린 뒤 15초."""

    count: int = 0
    deadline: float | None = None


class MicState(BaseModel):
    connected: bool = True
    paused: bool = False  # 사용자 일시정지(설정)
    error_announced: bool = False  # 마이크가 없어진 뒤 T_MIC_ERROR를 읽었는지


class GpsRetry(BaseModel):
    """위치 미수신이면 GPS 읽기가 10초 간격으로 조용히 다시 시도한다(최대 3분, 6-11). 엔진은 그사이 잡히면 낭독한다."""

    give_up_at: float


class Counters(BaseModel):
    """ID 번호. 엔진이 결정적으로 매기도록 상태에 둔다."""

    utterance: int = 0
    record: int = 0
    alert: int = 0
    action: int = 0
    kit_turn: int = 0


def _empty_observation() -> dict[str, ObservedValue | None]:
    return {k: None for k in OBSERVATION_VALUES}


def _default_devices() -> dict[str, DeviceState]:
    return {
        "metronome": DeviceState(object=2, name="metronome", detail={"bpm": 110}),
        "rescue_signal": DeviceState(object=4, name="rescue_signal", detail={"output": "speaker"}),
    }


class SessionState(BaseModel):
    session_id: str
    previous_session_id: str | None = None
    started_at: str
    mode: Literal["idle", "guiding"] = "idle"
    active_manual_id: str | None = None  # 현재 상황 매뉴얼(COM-* 안이면 그 전 상황 매뉴얼)
    step_id: str | None = None  # 엔진 위치(다음 입력을 해석할 기준 단계)
    spoken_step_id: str | None = None  # 낭독을 마친 마지막 단계
    routing_no: int = 0
    call_stack: list[Frame] = []
    observation: dict[str, ObservedValue | None] = Field(default_factory=_empty_observation)
    answer_log: list[AnswerLogEntry] = []
    flags: list[str] = []  # aed, cpr, call119 중 켜진 것
    pending_prompt: PendingPrompt | None = None
    no_speech: NoSpeech = Field(default_factory=NoSpeech)
    wait_deadline: float | None = None  # wait_timeout_s가 있는 대기의 마감
    wait_timeouts: dict[str, int] = {}  # 대기 단계별 시간 초과로 넘어간 횟수(세 번이면 그 뒤로는 기다리기만)
    last_utterance: Utterance | None = None  # 반복 요청 scope 1 대상
    pending_says: dict[str, PendingSay] = {}  # 낭독이 아직 안 끝난 say 액션(action_id → 문장), 순서 유지
    speech_active: bool = False  # 말소리를 받는 중(무응답·대기 타이머 정지, 알림 미룸)
    timer_reset_record: str | None = None  # 타이머 초기화 전에 저장 중인 기록 액션
    pending_devices: dict[str, dict] = {}  # 응답을 기다리는 장치 명령(action_id → object·type·source·step_id)
    last_off_device: int | None = None  # 대상 없는 켜기용 직전에 끈 소리 기능(2 또는 4)
    alerts: list[Alert] = []
    gps_retry: GpsRetry | None = None
    devices: dict[str, DeviceState] = Field(default_factory=_default_devices)
    timer: TimerState = Field(default_factory=TimerState)
    volume: int = 70  # 안내 음성 음량 0~100 (명세서 /api/devices 예시값)
    mic: MicState = Field(default_factory=MicState)
    epoch: int = 0  # 엔진 위치(단계·확인 질문·활성 매뉴얼)가 바뀔 때만 오른다
    counters: Counters = Field(default_factory=Counters)
