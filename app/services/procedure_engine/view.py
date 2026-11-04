"""session.updated 데이터: SessionState에서 화면이 보는 칸 중 바뀐 것 (명세서 GET /api/events, 공통 데이터 형식 SessionState).

알림·기기·타이머는 alert.*·device.state·timer.tick으로 따로 나가므로 여기서 다루지 않는다.
"""
from app.schemas.session import SessionState
from app.services.procedure_engine.context import Ctx

# 매뉴얼·위치·관찰값·표시·확인 질문
SESSION_KEYS = ("session_id", "previous_session_id", "started_at", "mode", "active_manual_id", "step_id",
                "routing_no", "call_stack", "observation", "answer_log", "flags", "pending_prompt", "epoch")


def session_fields(state: SessionState, ctx: Ctx) -> dict:
    data = state.model_dump(include=set(SESSION_KEYS))
    prompt = state.pending_prompt
    if prompt is not None:
        # 엔진 안의 마감 시각(단조 증가 초)을 화면용 ISO 시각으로
        expires_at = ctx.iso(prompt.deadline - ctx.now) if prompt.deadline is not None else None
        data["pending_prompt"] = {
            "type": prompt.type, "template_id": prompt.template_id, "origin_step_id": prompt.origin_step_id,
            "target_manual_id": prompt.target_manual_id,
            "deferred_branch": prompt.deferred_branch.model_dump() if prompt.deferred_branch else None,
            "expires_at": expires_at,
        }
    return data


def session_changes(before: SessionState | None, after: SessionState, ctx: Ctx) -> dict:
    new = session_fields(after, ctx)
    if before is None:
        return new
    old = session_fields(before, ctx)
    return {k: v for k, v in new.items() if old[k] != v}
