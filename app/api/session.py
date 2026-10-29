"""세션 API (명세서 "세션"): GET /api/session, GET /api/session/log."""
from datetime import datetime

from fastapi import APIRouter, Request

from app.common.error_code import ErrorCode
from app.common.exceptions import AppError
from app.common.response import ok
from app.schemas.record import Record
from app.schemas.session import SessionState
from app.services.manual_db import ManualDB
from app.services.procedure_engine.context import Ctx
from app.services.procedure_engine.timers import listen_state
from app.services.procedure_engine.view import session_fields

router = APIRouter()

CONVERSATION_SIZE = 50  # conversation에 넣는 최근 대화 기록 수


@router.get("/session")
async def get_session(request: Request, debug: bool = False):
    """화면이 처음 열리거나 다시 붙을 때 지금 상태를 한 번에 받는다. debug면 엔진 내부 칸도 넣는다.

    준비 전(엔진 시작 전)에도 200: 세션 칸은 null·빈 값(mode idle), mic는 blocked, conversation은 빈 목록.
    """
    parts = request.app.state.parts
    engine, seq, state = parts.engine, parts.events.seq, parts.engine.state
    ctx = Ctx(db=engine.db, now=parts.clock.now(), wall=parts.clock.wall())
    if state is None:
        data = _session_data(SessionState(session_id="", started_at=""), ctx, seq, "blocked", [], debug)
        data["session_id"] = data["started_at"] = None
        return ok(data)
    records = await parts.records.session_records(state.session_id) or []
    mic = engine.sequencer.speech_state or listen_state(state)["state"]
    return ok(_session_data(state, ctx, seq, mic, records, debug))


@router.get("/session/log")
async def get_session_log(request: Request, session_id: str | None = None):
    """세션의 처치 기록(시각순, 저장하지 못한 기록도 saved false로). session_id가 없으면 현재 세션, 없는 세션이면 404.

    elapsed_s는 현재 세션이면 지금까지, 지난 세션이면 마지막 기록까지 걸린 시간이다.
    """
    parts = request.app.state.parts
    state = parts.engine.state
    current = state.session_id if state is not None else None
    session_id = session_id or current
    info = await parts.records.session_info(session_id) if session_id else None
    if info is None and (session_id is None or session_id != current):
        raise AppError(ErrorCode.SESSION_NOT_FOUND)
    started_at = state.started_at if session_id == current else info["started_at"]
    records = await parts.records.session_records(session_id) or []
    if session_id == current:
        end = parts.clock.wall()
    else:
        end = datetime.fromisoformat(records[-1].at if records else started_at)
    return ok({
        "session_id": session_id,
        "started_at": started_at,
        "elapsed_s": max(0, int((end - datetime.fromisoformat(started_at)).total_seconds())),
        "unsaved": sum(r.session_id == session_id for r in parts.records.unsaved()),
        "records": [r.model_dump(exclude={"session_id"}) for r in records],
    })


def _session_data(state: SessionState, ctx: Ctx, seq: int, mic: str, records: list[Record], debug: bool) -> dict:
    fields = session_fields(state, ctx)
    data = {"seq": seq}
    data.update({k: fields[k] for k in ("session_id", "previous_session_id", "started_at", "mode", "active_manual_id",
                                        "step_id")})
    data.update(
        spoken_step_id=state.spoken_step_id,
        current_prompt=_current_prompt(state, ctx.db, records),
        pending_prompt=fields["pending_prompt"],
        observation=fields["observation"],
        flags=fields["flags"],
        alerts=[{"alert_id": a.alert_id, "step_id": a.step_id, "seconds": a.seconds,
                 "ends_at": ctx.iso(a.deadline - ctx.now) if a.deadline is not None else None,
                 "on_timeout": a.on_timeout} for a in state.alerts],
        devices={name: d.model_dump() for name, d in state.devices.items()},
        timer={"object": 3, "status": state.timer.status, "elapsed_s": state.timer.elapsed_s(ctx.now),
               "started_at": state.timer.started_at},
        mic=mic,
        conversation=_conversation(records),
    )
    if debug:
        data.update({k: fields[k] for k in ("routing_no", "call_stack", "answer_log")})
        data.update(
            no_speech={"count": state.no_speech.count, "running": state.no_speech.deadline is not None},
            last_utterance_id=state.last_utterance.utterance_id if state.last_utterance else None,
            last_off_device=state.last_off_device,
            epoch=state.epoch,
        )
    return data


def _current_prompt(state: SessionState, db: ManualDB, records: list[Record]) -> dict | None:
    """지금 대답을 기다리는 질문: 확인 질문이 있으면 그 질문, 없으면 현재 ask 단계의 질문, 그 밖이면 None.

    실제로 말한 문장(kit_guidance 기록, 읽는 중인 문장, 마지막 문장) 중 가장 최근 것이라 DB 원문 그대로다(템플릿은 채운 문장).
    """
    if state.pending_prompt is not None:
        key = ("template", state.pending_prompt.template_id)
    elif state.step_id is not None and db.steps_by_id[state.step_id]["kind"] == "ask":
        key = ("step", state.step_id)
    else:
        return None
    said = [r.utterance for r in records if r.kind == "kit_guidance"]
    said += [p.utterance for p in state.pending_says.values()]
    if state.last_utterance is not None:
        said.append(state.last_utterance)
    found = next((u for u in reversed(said) if (u.source, u.template_id or u.step_id) == key), None)
    return found.model_dump() if found is not None else None


def _conversation(records: list[Record]) -> list[dict]:
    """최근 사용자 발화·키트 안내 기록을 화면 대화창용으로 줄인다(시간순)."""
    rows = [r for r in records if r.kind in ("user_utterance", "kit_guidance")][-CONVERSATION_SIZE:]
    out = []
    for r in rows:
        if r.kind == "user_utterance":
            text, screen, step_id = r.text, None, None
        else:
            text, screen, step_id = r.utterance.text, r.utterance.screen, r.utterance.step_id
        out.append({"record_id": r.record_id, "kind": r.kind, "text": text, "screen": screen, "step_id": step_id,
                    "at": r.at})
    return out
