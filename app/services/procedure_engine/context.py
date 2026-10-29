"""리듀서 작업 공간. 상태 사본 위에서 규칙을 적용하고 actions·events를 모은다.

reduce()는 입력 하나마다 Run 하나를 만들고 끝에 EngineResult로 돌려준다. 원래 상태는 바꾸지 않는다.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from app.schemas.device import DeviceCommand, Location
from app.schemas.record import Record
from app.schemas.session import PendingSay, SessionState
from app.schemas.utterance import Utterance
from app.services.manual_db import ManualDB
from app.services.procedure_engine.actions import DeviceAction, EngineEvent, EngineResult, RecordAction, SayAction
from app.services.procedure_engine.clock import iso
from app.services.procedure_engine.templates import fill

Role = Literal["question", "instruction", "notice"]

# 대답을 기다리는 확인 질문 템플릿 (명세서 Utterance.role)
QUESTION_TEMPLATES = {"T_SWITCH_ASK", "T_TIMER_RESET_ASK", "T_WHICH_ON", "T_WHICH_OFF"}


@dataclass(frozen=True)
class Ctx:
    """리듀서가 읽기만 하는 바깥 정보."""

    db: ManualDB
    now: float  # 엔진 시계(단조 증가 초)
    wall: datetime  # 기록·화면용 벽시계
    location: Location | None = None  # 지금 GPS 스냅샷. GPS 모듈이 없으면 None
    clock_source: Literal["gps", "rtc", "unsynced"] = "unsynced"

    def iso(self, after_s: float = 0) -> str:
        return iso(self.wall + timedelta(seconds=after_s))


class Run:
    def __init__(self, state: SessionState, ctx: Ctx, turn_id: str | None):
        self.s = state.model_copy(deep=True)
        self.ctx = ctx
        self.db = ctx.db
        self.turn_id = turn_id
        self.actions: list = []
        self.events: list[EngineEvent] = []
        self._start_epoch = state.epoch
        self._earlier_says = set(state.pending_says)  # 이 입력 전부터 읽기를 기다리던 문장
        self._force_replace = False
        self.input_obs_keys: list[str] = []  # 이번 입력이 저장한 관찰값(라우팅을 일으키면 새 routing_no로 다시 매김)
        self.user_record_id: str | None = None  # 이번 사용자 발화 기록

    # 번호
    def next_id(self, kind: str, prefix: str, width: int = 4) -> str:
        value = getattr(self.s.counters, kind) + 1
        setattr(self.s.counters, kind, value)
        return f"{prefix}_{value:0{width}d}"

    # 위치: 단계·확인 질문·활성 매뉴얼이 바뀔 때만 epoch가 오른다
    def set_step(self, step_id: str | None) -> None:
        if self.s.step_id != step_id:
            self.s.step_id = step_id
            self.s.epoch += 1

    def set_manual(self, manual_id: str | None) -> None:
        if self.s.active_manual_id != manual_id:
            self.s.active_manual_id = manual_id
            self.s.epoch += 1

    def position_changed(self) -> None:
        self.s.epoch += 1

    def preempt(self) -> None:
        """위치는 그대로지만 진행 중인 낭독을 끊고 이 결과를 바로 읽어야 할 때(재질문 등)."""
        self._force_replace = True

    # 말하기
    def say(self, text: str, role: Role, source: str, *, screen=None, manual_id=None, step_id=None,
            template_id=None, speed: int = 0) -> str:
        u = Utterance(utterance_id=self.next_id("utterance", "u"), role=role, text=text, screen=screen, source=source,
                      manual_id=manual_id, step_id=step_id, template_id=template_id, speed=speed)
        return self.say_utterance(u)

    def say_utterance(self, u: Utterance) -> str:
        action_id = self.next_id("action", "x")
        record_id = self.next_id("record", "r")
        self.actions.append(SayAction(action_id=action_id, utterance=u, record_id=record_id))
        self.s.pending_says[action_id] = PendingSay(utterance=u, record_id=record_id, turn_id=self.turn_id,
                                                    call_stack=list(self.s.call_stack))
        self.s.last_utterance = u
        return action_id

    def say_step(self, step: dict, role: Role, speed: int = 0) -> str:
        return self.say(step["tts"], role, "step", screen=step["screen"], manual_id=step["manual_id"],
                        step_id=step["step_id"], speed=speed)

    def say_template(self, template_id: str, speed: int = 0, **values: str) -> str:
        text = fill(self.db.templates_by_id[template_id]["tts"], values)
        role: Role = "question" if template_id in QUESTION_TEMPLATES else "notice"
        return self.say(text, role, "template", template_id=template_id, speed=speed)

    def template_text(self, template_id: str, **values: str) -> str:
        return fill(self.db.templates_by_id[template_id]["tts"], values)

    # 장치·기록·이벤트
    def device(self, obj: int, typ: int, source: str, step_id: str | None = None) -> str:
        action_id = self.next_id("action", "x")
        self.actions.append(DeviceAction(action_id=action_id, command=DeviceCommand(object=obj, type=typ, source=source),
                                         step_id=step_id))
        return action_id

    def record(self, record: Record, wants_result: bool = False) -> str:
        action_id = self.next_id("action", "x")
        self.actions.append(RecordAction(action_id=action_id, record=record, wants_result=wants_result))
        return action_id

    def record_base(self, record_id: str | None = None) -> dict:
        return {"record_id": record_id or self.next_id("record", "r"), "session_id": self.s.session_id,
                "at": self.ctx.iso(), "clock": self.ctx.clock_source, "turn_id": self.turn_id}

    def event(self, type: str, data: dict) -> None:
        self.events.append(EngineEvent(type=type, data=data))

    def notice(self, code: str, template_id: str | None = None, text: str | None = None) -> None:
        """화면에만 띄우는 알림(ui.notice)."""
        self.event("ui.notice", {"code": code, "template_id": template_id, "text": text})

    def result(self) -> EngineResult:
        mode = "replace" if self._force_replace or self.s.epoch != self._start_epoch else "append"
        if mode == "replace":
            for action_id in self._earlier_says & set(self.s.pending_says):
                self.s.pending_says[action_id].dropped = True  # 시퀀서가 cut으로 돌려줄 문장
        return EngineResult(turn_id=self.turn_id, state=self.s, actions=self.actions, events=self.events, mode=mode)
