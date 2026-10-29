"""매뉴얼 DB 적재·검사.

서버 시작과 tools/check_manual_db.py가 같은 검사를 쓴다. 표준 라이브러리만 쓴다(도구는 의존성 없이 돈다).
"""
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 엔진(명세서 ⑥)이 쓰는 템플릿과 단계 ID
ENGINE_TEMPLATES = [
    "T_GPS_OK", "T_GPS_WAIT", "T_GPS_LAST", "T_DEV_METRO_ON", "T_DEV_METRO_OFF", "T_DEV_TIMER_START",
    "T_DEV_TIMER_RESUME", "T_DEV_TIMER_CHECK", "T_DEV_TIMER_PAUSE", "T_TIMER_RESET_ASK", "T_DEV_TIMER_RESET",
    "T_DEV_TIMER_KEEP", "T_ALERT_DONE", "T_DEV_ALREADY_ON", "T_DEV_ALREADY_OFF", "T_DEV_SOS_ON", "T_DEV_SOS_OFF",
    "T_DEV_VOL_UP", "T_DEV_VOL_DOWN", "T_REPEAT_ASK", "T_OOS_1", "T_OOS_2", "T_OOS_3", "T_WHICH_ON", "T_WHICH_OFF",
    "T_NOTHING_ON", "T_NO_ASK", "T_UNKNOWN", "T_CORRECT", "T_CORRECT_KEEP", "T_SWITCH", "T_SWITCH_ASK", "T_NO_SPEECH",
    "T_RETRY", "T_NOTED", "T_WAITING", "T_MIC_ERROR", "T_WELCOME", "T_RESUME",
]
ENGINE_STEPS = ["EM01-02", "EM01-23", "MON-02", "MON-06", "EM00-09", "EM15-09", "EM01-15", "SAFE-01", "SAFE-04"]

# 하나라도 비어 있지 않으면 실패인 항목. 이 밖에 situation_code 매핑이 어긋나도 실패다
MUST_EMPTY = [
    "단계 ID 중복", "템플릿 ID 중복", "끊긴 단계 참조", "도달 못 하는 단계", "dispatch에서 쓰는데 없는 템플릿",
    "엔진(명세서)에서 쓰는데 없는 템플릿", "kind별 필드 조합 위반", "slot 프리셋 불일치", "켜는 곳 없는 표시",
    "meta.flags 설명 없는 표시", "매뉴얼 필드 누락·오류", "읽는 문장 규칙 위반 (숫자·/·영문)", "보기용 카드 오류",
]
SITUATION_MAPPING = "situation_code 매핑"

_BRANCH_FIELDS = ["on_yes", "on_no", "on_unknown", "next", "on_timeout"]
_DEVICE_RE = re.compile(r"gps:announce|metronome:on:110|metronome:off|timer:start|alert:\d+|alert:cancel|(?:un)?flag:[a-z0-9]+")
_ALERT_RE = re.compile(r"alert:\d+")


def check(db: dict) -> dict[str, Any]:
    """항목 이름 → 결과. MUST_EMPTY 항목과 situation_code 매핑 말고는 참고용이다."""
    r: dict[str, Any] = {}
    steps = {s["step_id"]: s for s in db["steps"]}
    manuals = {m["manual_id"]: m for m in db["manuals"]}
    tids = [t["template_id"] for t in db["templates"]]
    r["단계 수"] = len(db["steps"])
    r["단계 ID 중복"] = [k for k, v in Counter(s["step_id"] for s in db["steps"]).items() if v > 1]
    r["템플릿 ID 중복"] = [k for k, v in Counter(tids).items() if v > 1]

    # 1 끊긴 참조
    dangling = []
    for s in db["steps"]:
        for f in _BRANCH_FIELDS:
            if s[f] is not None and s[f] not in steps:
                dangling.append(f"{s['step_id']}.{f}={s[f]}")
        if s["call"] is not None and s["call"] not in manuals:
            dangling.append(f"{s['step_id']}.call={s['call']}")
        if s["manual_id"] not in manuals:
            dangling.append(f"{s['step_id']}.manual_id={s['manual_id']}")
    for m in db["manuals"]:
        if m["entry_step"] not in steps:
            dangling.append(f"{m['manual_id']}.entry_step={m['entry_step']}")
    for rule in db["routing"]:
        if rule["target"] not in steps and rule["target"] != "situation 표의 entry":
            dangling.append(f"{rule['rule_id']}.target={rule['target']}")
    for sid in ENGINE_STEPS:
        if sid not in steps:
            dangling.append(f"엔진 규칙의 {sid}")
    dispatch_meta = json.dumps(db["dispatch"], ensure_ascii=False) + json.dumps(db["meta"], ensure_ascii=False)
    for sid in re.findall(r"\b(?:EM\d\d|MON|C119|AED|SAFE)-\d\d\b", dispatch_meta):
        if sid not in steps:
            dangling.append(f"dispatch/meta의 {sid}")
    r["끊긴 단계 참조"] = dangling

    # 2 도달 못 하는 단계 (매뉴얼 시작 단계에서 출발)
    seen, stack = set(), [m["entry_step"] for m in db["manuals"]]
    while stack:
        x = stack.pop()
        if x in seen or x not in steps:
            continue
        seen.add(x)
        s = steps[x]
        for f in _BRANCH_FIELDS:
            if s[f]:
                stack.append(s[f])
        if s["call"] and s["call"] in manuals:
            stack.append(manuals[s["call"]]["entry_step"])
    r["도달 못 하는 단계"] = sorted(set(steps) - seen)

    # 3 템플릿
    disp = json.dumps(db["dispatch"], ensure_ascii=False)
    ref = sorted({t for t in re.findall(r"T_[A-Z0-9_]+", disp) if not t.endswith("_")})
    r["dispatch가 쓰는 템플릿"] = ref
    r["dispatch에서 쓰는데 없는 템플릿"] = [t for t in ref if t not in tids]
    r["엔진(명세서)에서 쓰는데 없는 템플릿"] = [t for t in ENGINE_TEMPLATES if t not in tids]
    r["DB에만 있고 어디서도 안 쓰는 템플릿"] = [t for t in tids if t not in ref and t not in ENGINE_TEMPLATES]
    r["dispatch의 type 4(구급대) 규칙"] = [d["output"] for d in db["dispatch"] if "type 4" in d["rule"] or "구급대" in d["rule"]]

    # 4 kind별 필드 조합
    combo, info_say_call, info_return_tts = [], [], []
    for s in db["steps"]:
        k, sid = s["kind"], s["step_id"]
        has_alert = any(_ALERT_RE.fullmatch(d) for d in s["device"])
        if k == "say":
            if not s["next"]:
                combo.append(f"{sid}: say에 next 없음")
            if any(s[f] for f in ["on_yes", "on_no", "on_unknown"]):
                combo.append(f"{sid}: say에 네/아니요 분기")
            if not s["tts"] and not has_alert:
                combo.append(f"{sid}: tts 없는 say인데 알림도 없음")
            if s["call"]:
                info_say_call.append(sid)
        elif k == "ask":
            if not (s["on_yes"] and s["on_no"] and s["on_unknown"]):
                combo.append(f"{sid}: ask 분기 누락")
            if s["next"] or s["call"]:
                combo.append(f"{sid}: ask에 next/call")
            if not s["tts"]:
                combo.append(f"{sid}: ask에 질문 문장 없음")
            if s["skip_if_known"] and not s["slot"]:
                combo.append(f"{sid}: slot 없이 skip_if_known")
        elif k == "call":
            if not (s["call"] and s["next"]):
                combo.append(f"{sid}: call에 call/next 없음")
            if s["tts"]:
                combo.append(f"{sid}: call에 tts")
            if s["device"]:
                combo.append(f"{sid}: call에 device")
        elif k == "return":
            if not s["next"]:
                combo.append(f"{sid}: return에 기본 next 없음")
            if s["tts"]:
                info_return_tts.append(sid)
        elif k == "wait":
            if s["tts"]:
                combo.append(f"{sid}: wait에 tts")
            if s["device"]:
                combo.append(f"{sid}: wait에 device")
            if s["wait_timeout_s"] and not s["next"]:
                combo.append(f"{sid}: 대기 시간이 있는데 next 없음")
        else:
            combo.append(f"{sid}: 알 수 없는 kind {k}")
        if s["on_timeout"] and not has_alert:
            combo.append(f"{sid}: on_timeout인데 alert:N 없음")
        if has_alert and s["kind"] != "say":
            combo.append(f"{sid}: say가 아닌 단계에 alert")
        if s["alert_tts"] and (not has_alert or s["on_timeout"]):
            combo.append(f"{sid}: alert_tts는 on_timeout 없는 alert 단계에만")
        if s["report_on_yes"] and k != "ask":
            combo.append(f"{sid}: report_on_yes는 ask에만")
        if s["skip_flag"] and k not in ("ask", "say"):
            combo.append(f"{sid}: skip_flag는 ask·say에만")
        if s["wait_timeout_s"] and k != "wait":
            combo.append(f"{sid}: wait_timeout_s는 wait에만")
        if "image" not in s:
            combo.append(f"{sid}: image 필드 없음")
        for d in s["device"]:
            if not _DEVICE_RE.fullmatch(d):
                combo.append(f"{sid}: 모르는 device {d}")
    r["kind별 필드 조합 위반"] = combo
    r["say + call (엔진 규칙 1이 처리)"] = info_say_call
    r["return + tts (엔진 규칙 4가 처리)"] = info_return_tts
    r["on_timeout도 alert_tts도 없는 알림 (T_ALERT_DONE)"] = [
        s["step_id"] for s in db["steps"]
        if any(_ALERT_RE.fullmatch(d) for d in s["device"]) and not s["on_timeout"] and not s["alert_tts"]
    ]

    # 5 slot 프리셋
    pres = db["meta"]["slot_presets"]
    bad = []
    for s in db["steps"]:
        if s["slot"]:
            p = pres.get(s["slot"])
            if not p or s["slot_key"] != p["slot"] or s["slot_yes"] != p["yes"] or s["slot_no"] != p["no"]:
                bad.append(s["step_id"])
    r["slot 프리셋 불일치"] = bad
    dom = {"conscious": {0, 1, 2}, "breathing": {0, 1, 2}, "bleeding": {0, 1}, "movement": {0, 1}, "scene_danger": {0, 1}}
    r["프리셋이 덮지 않는 값 (알려져 있어도 물음)"] = {
        k: sorted(dom[p["slot"]] - set(p["yes"]) - set(p["no"])) for k, p in pres.items()
        if dom[p["slot"]] - set(p["yes"]) - set(p["no"])
    }

    # 6 표시(flag)
    set_f = {d.split(":")[1] for s in db["steps"] for d in s["device"] if d.startswith("flag:")}
    unset_f = {d.split(":")[1] for s in db["steps"] for d in s["device"] if d.startswith("unflag:")}
    used_f = {s["skip_flag"] for s in db["steps"] if s["skip_flag"]}
    r["켜는 곳 없는 표시"] = sorted((used_f | unset_f | {"cpr", "call119"}) - set_f)
    r["meta.flags 설명 없는 표시"] = sorted((set_f | unset_f | used_f) - set(db["meta"]["flags"]))
    r["표시별 켜는 단계"] = {f: [s["step_id"] for s in db["steps"] if f"flag:{f}" in s["device"]] for f in sorted(set_f)}
    r["표시별 끄는 단계"] = {f: [s["step_id"] for s in db["steps"] if f"unflag:{f}" in s["device"]] for f in sorted(unset_f)}

    # 7 매뉴얼 필드
    mf, feats = [], []
    for m in db["manuals"]:
        if m["type"] == "situation":
            for f in ["spoken_name", "summary"]:
                if not m.get(f):
                    mf.append(f"{m['manual_id']}.{f}")
            if m["featured"] is not None:
                feats.append(m["featured"])
        elif any(m.get(f) is not None for f in ["spoken_name", "summary", "featured"]):
            mf.append(f"{m['manual_id']}: 공통 절차에 목록 필드")
    if sorted(feats) != list(range(1, 7)):
        mf.append(f"featured 값 {sorted(feats)}")
    r["매뉴얼 필드 누락·오류"] = mf
    codes = sorted(m["situation_code"] for m in db["manuals"] if m["type"] == "situation")
    r[SITUATION_MAPPING] = codes == sorted(list(range(1, 16)) + [-1])

    # 8 읽는 문장 규칙
    viol = []

    def spoken(where, text):
        t = re.sub(r"\{[a-z_]+\}", "", text or "")
        if re.search(r"[0-9]", t):
            viol.append(f"{where}: 아라비아 숫자")
        if "/" in t:
            viol.append(f"{where}: '/'")
        if re.search(r"[A-Za-z]", t):
            viol.append(f"{where}: 영문")

    for s in db["steps"]:
        spoken(s["step_id"], s["tts"])
        spoken(s["step_id"] + ".alert_tts", s["alert_tts"])
    for m in db["manuals"]:
        spoken(m["manual_id"] + ".intro_tts", m["intro_tts"])
        spoken(m["manual_id"] + ".spoken_name", m.get("spoken_name"))
    for t in db["templates"]:
        spoken(t["template_id"], t["tts"])
    r["읽는 문장 규칙 위반 (숫자·/·영문)"] = viol
    r['데이터에 남은 "조명"'] = re.findall(r'[^"]{0,20}조명[^"]{0,20}', json.dumps(db, ensure_ascii=False))

    # 9 템플릿 status·note
    tn = []
    for t in db["templates"]:
        if t.get("status") != "설계":
            tn.append(f"{t['template_id']}: status")
        if "note" not in t:
            tn.append(f"{t['template_id']}: note 없음")
    r["템플릿 status·note 누락"] = tn
    r["단계 status 분포"] = dict(Counter(s["status"] for s in db["steps"]))

    # 10 보기용 카드 (v1.6)
    vs_err = []
    for m in db["manuals"]:
        if m["type"] == "situation":
            if not isinstance(m.get("view_steps"), list):
                vs_err.append(f"{m['manual_id']}: view_steps 없음")
                continue
            nos = []
            for c in m["view_steps"]:
                if set(c) != {"no", "kind", "title", "text", "image"}:
                    vs_err.append(f"{m['manual_id']}: 카드 필드 {sorted(c)}")
                if c.get("kind") not in ("check", "call", "action"):
                    vs_err.append(f"{m['manual_id']}: 카드 kind {c.get('kind')}")
                nos.append(c.get("no"))
            if nos != list(range(1, len(nos) + 1)):
                vs_err.append(f"{m['manual_id']}: 카드 no {nos}")
        elif "view_steps" in m:
            vs_err.append(f"{m['manual_id']}: 공통 절차에 view_steps")
    r["보기용 카드 오류"] = vs_err
    return r


def failures(results: dict[str, Any]) -> list[str]:
    """반드시 통과해야 하는데 어긋난 항목 이름."""
    fails = [k for k in MUST_EMPTY if results[k]]
    if results[SITUATION_MAPPING] is not True:
        fails.append(SITUATION_MAPPING)
    return fails


class ManualDBError(Exception):
    """매뉴얼 DB를 쓸 수 없음. 서버는 이 예외로 시작을 멈춘다."""

    def __init__(self, message: str, failures: list[str] | None = None):
        super().__init__(message)
        self.failures = failures or []


@dataclass(frozen=True)
class ManualDB:
    """검사를 통과한 매뉴얼 DB. 원본과 ID별 찾아보기."""

    data: dict
    version: str  # meta.version 첫 토큰 (예: "v1.7")
    steps_by_id: dict[str, dict]
    manuals_by_id: dict[str, dict]
    templates_by_id: dict[str, dict]
    manual_by_situation: dict[int, dict]  # situation_code → 상황 매뉴얼 (-1 → EM-00)


def load_manual_db(path: Path) -> ManualDB:
    """읽고 검사한다. 파일이 없거나, JSON이 깨졌거나, 필수 검사가 실패하면 ManualDBError."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ManualDBError(f"매뉴얼 DB 파일이 없습니다: {path}") from None
    except json.JSONDecodeError as e:
        raise ManualDBError(f"매뉴얼 DB JSON 형식이 올바르지 않습니다: {path} ({e})") from None
    try:
        results = check(data)
    except (KeyError, TypeError, AttributeError) as e:
        raise ManualDBError(f"매뉴얼 DB 구조가 올바르지 않습니다: {path} ({type(e).__name__}: {e})") from None
    fails = failures(results)
    if fails:
        detail = "; ".join(f"{k}: {results[k]}" for k in fails)
        raise ManualDBError(f"매뉴얼 DB 검사 실패: {path} ({detail})", fails)
    return ManualDB(
        data=data,
        version=data["meta"]["version"].split()[0],
        steps_by_id={s["step_id"]: s for s in data["steps"]},
        manuals_by_id={m["manual_id"]: m for m in data["manuals"]},
        templates_by_id={t["template_id"]: t for t in data["templates"]},
        manual_by_situation={m["situation_code"]: m for m in data["manuals"] if m["type"] == "situation"},
    )
