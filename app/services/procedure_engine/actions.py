"""엔진 출력 (명세서 6-1 EngineResult, 계획 3-3·3-4).

actions는 셸이 바깥에서 해야 하는 일만 담는다(낭독, 장치, 기록 저장). 절차 알림·표시·확인 질문은 상태 변화라
actions가 아니라 state에 반영되고, 화면에 알릴 것은 events로 나간다.
"""
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.schemas.device import DeviceCommand
from app.schemas.record import Record
from app.schemas.session import SessionState
from app.schemas.utterance import Utterance


class SayAction(BaseModel):
    """Utterance 낭독. 낭독이 끝나야 다음 action으로 넘어간다."""

    type: Literal["say"] = "say"
    action_id: str
    utterance: Utterance
    record_id: str  # 이 문장의 kit_guidance 기록 ID. 낭독 시작 때 guide.say에 함께 보낸다


class DeviceAction(BaseModel):
    """장치 명령. 응답을 기다리지 않고 다음으로, 결과는 DeviceResultInput으로 돌아온다."""

    type: Literal["device"] = "device"
    action_id: str
    command: DeviceCommand
    step_id: str | None = None  # 단계 device로 엔진이 낸 명령이면 그 단계


class RecordAction(BaseModel):
    """기록 저장. 결과가 분기에 필요하면 RecordSavedInput으로 돌아온다."""

    type: Literal["record"] = "record"
    action_id: str
    record: Record
    wants_result: bool = False  # 저장 성공이 분기 조건이면 셸이 RecordSavedInput으로 돌려준다


Action = Annotated[SayAction | DeviceAction | RecordAction, Field(discriminator="type")]


class EngineEvent(BaseModel):
    """화면에 알릴 일. 봉투(seq, at)는 이벤트 버스가 씌운다."""

    type: Literal["turn.result", "session.updated", "alert.started", "alert.ended", "ui.notice", "device.state"]
    data: dict


class EngineResult(BaseModel):
    turn_id: str | None
    state: SessionState
    actions: list[Action] = []
    events: list[EngineEvent] = []
    # replace: 위치가 바뀐 결과. 시퀀서가 이전 결과의 남은 say를 버리고(cut) device·record는 실행한 뒤 이것을 실행
    # append: 되먹임처럼 위치를 바꾸지 않는 결과. 지금 묶음이 끝난 뒤 실행
    mode: Literal["replace", "append"] = "append"
    # 읽던 안내 문장이 끝난 뒤 실행됨: append이고 이 입력 전부터 기다리던 버려지지 않은 문장이 남음(버튼 응답 queued)
    queued: bool = False
