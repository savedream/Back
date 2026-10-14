#!/usr/bin/env python3
"""매뉴얼 DB 검사.

사용: python tools/check_manual_db.py [DB json] [주석판 jsonc]
기본값: data/manual_db_v1.6.json, 주석판은 docs/ 또는 상위 폴더의 docs/ (없으면 비교 생략)
반드시 통과해야 하는 항목이 하나라도 어긋나면 종료 코드 1.
"""
import json, re, sys
from collections import OrderedDict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'data' / 'manual_db_v1.6.json'
if len(sys.argv) > 2:
    jsonc_path = Path(sys.argv[2])
else:
    # 주석판은 레포 밖(상위 폴더의 docs/)에 있을 수 있다. 없으면 비교를 건너뛴다
    jsonc_path = next((p for p in [ROOT / 'docs' / 'manual_db_주석판_v1.6.jsonc',
                                   ROOT.parent / 'docs' / 'manual_db_주석판_v1.6.jsonc'] if p.exists()),
                      ROOT / 'docs' / 'manual_db_주석판_v1.6.jsonc')
db = json.loads(db_path.read_text(encoding='utf-8'))
db_c = None
if jsonc_path.exists():
    jsonc = jsonc_path.read_text(encoding='utf-8')
    db_c = json.loads('\n'.join(l for l in jsonc.split('\n') if not l.strip().startswith('//')))

# 엔진(명세서 ⑥)이 쓰는 템플릿과 단계 ID
ENGINE_TEMPLATES = ['T_GPS_OK', 'T_GPS_WAIT', 'T_GPS_LAST', 'T_DEV_METRO_ON', 'T_DEV_METRO_OFF', 'T_DEV_TIMER_START',
                    'T_DEV_TIMER_RESUME', 'T_DEV_TIMER_CHECK', 'T_DEV_TIMER_PAUSE', 'T_TIMER_RESET_ASK', 'T_DEV_TIMER_RESET',
                    'T_DEV_TIMER_KEEP', 'T_ALERT_DONE', 'T_DEV_ALREADY_ON', 'T_DEV_ALREADY_OFF', 'T_DEV_SOS_ON', 'T_DEV_SOS_OFF',
                    'T_DEV_VOL_UP', 'T_DEV_VOL_DOWN', 'T_REPEAT_ASK', 'T_OOS_1', 'T_OOS_2', 'T_OOS_3', 'T_WHICH_ON', 'T_WHICH_OFF',
                    'T_NOTHING_ON', 'T_NO_ASK', 'T_UNKNOWN', 'T_CORRECT', 'T_CORRECT_KEEP', 'T_SWITCH', 'T_SWITCH_ASK', 'T_NO_SPEECH',
                    'T_RETRY', 'T_NOTED', 'T_WAITING', 'T_MIC_ERROR', 'T_WELCOME', 'T_RESUME']
ENGINE_STEPS = ['EM01-02', 'EM01-23', 'MON-02', 'MON-06', 'EM00-09', 'EM15-09', 'EM01-15', 'SAFE-01', 'SAFE-04']

R = OrderedDict()
R['주석판과 평문 JSON 데이터 일치'] = (db == db_c) if db_c is not None else '주석판 없음(건너뜀)'
steps = {s['step_id']: s for s in db['steps']}
manuals = {m['manual_id']: m for m in db['manuals']}
tids = [t['template_id'] for t in db['templates']]
R['단계 수'] = len(db['steps'])
R['단계 ID 중복'] = [k for k, v in Counter(s['step_id'] for s in db['steps']).items() if v > 1]
R['템플릿 ID 중복'] = [k for k, v in Counter(tids).items() if v > 1]

# 1 끊긴 참조
dangling = []
for s in db['steps']:
    for f in ['on_yes', 'on_no', 'on_unknown', 'next', 'on_timeout']:
        if s[f] is not None and s[f] not in steps:
            dangling.append(f"{s['step_id']}.{f}={s[f]}")
    if s['call'] is not None and s['call'] not in manuals:
        dangling.append(f"{s['step_id']}.call={s['call']}")
    if s['manual_id'] not in manuals:
        dangling.append(f"{s['step_id']}.manual_id={s['manual_id']}")
for m in db['manuals']:
    if m['entry_step'] not in steps:
        dangling.append(f"{m['manual_id']}.entry_step={m['entry_step']}")
for r in db['routing']:
    if r['target'] not in steps and r['target'] != 'situation 표의 entry':
        dangling.append(f"{r['rule_id']}.target={r['target']}")
for sid in ENGINE_STEPS:
    if sid not in steps:
        dangling.append(f'엔진 규칙의 {sid}')
for sid in re.findall(r'\b(?:EM\d\d|MON|C119|AED|SAFE)-\d\d\b', json.dumps(db['dispatch'], ensure_ascii=False) + json.dumps(db['meta'], ensure_ascii=False)):
    if sid not in steps:
        dangling.append(f'dispatch/meta의 {sid}')
R['끊긴 단계 참조'] = dangling

# 2 도달 못 하는 단계 (매뉴얼 시작 단계에서 출발)
seen, stack = set(), [m['entry_step'] for m in db['manuals']]
while stack:
    x = stack.pop()
    if x in seen or x not in steps:
        continue
    seen.add(x)
    s = steps[x]
    for f in ['on_yes', 'on_no', 'on_unknown', 'next', 'on_timeout']:
        if s[f]:
            stack.append(s[f])
    if s['call'] and s['call'] in manuals:
        stack.append(manuals[s['call']]['entry_step'])
R['도달 못 하는 단계'] = sorted(set(steps) - seen)

# 3 템플릿
disp = json.dumps(db['dispatch'], ensure_ascii=False)
ref = sorted({t for t in re.findall(r'T_[A-Z0-9_]+', disp) if not t.endswith('_')})
R['dispatch가 쓰는 템플릿'] = ref
R['dispatch에서 쓰는데 없는 템플릿'] = [t for t in ref if t not in tids]
R['엔진(명세서)에서 쓰는데 없는 템플릿'] = [t for t in ENGINE_TEMPLATES if t not in tids]
R['DB에만 있고 어디서도 안 쓰는 템플릿'] = [t for t in tids if t not in ref and t not in ENGINE_TEMPLATES]
R['dispatch의 type 4(구급대) 규칙'] = [d['output'] for d in db['dispatch'] if 'type 4' in d['rule'] or '구급대' in d['rule']]

# 4 kind별 필드 조합
combo, info_say_call, info_return_tts = [], [], []
DEV_RE = re.compile(r'gps:announce|metronome:on:110|metronome:off|timer:start|alert:\d+|alert:cancel|(?:un)?flag:[a-z0-9]+')
for s in db['steps']:
    k, sid = s['kind'], s['step_id']
    has_alert = any(re.fullmatch(r'alert:\d+', d) for d in s['device'])
    if k == 'say':
        if not s['next']: combo.append(f'{sid}: say에 next 없음')
        if any(s[f] for f in ['on_yes', 'on_no', 'on_unknown']): combo.append(f'{sid}: say에 네/아니요 분기')
        if not s['tts'] and not has_alert: combo.append(f'{sid}: tts 없는 say인데 알림도 없음')
        if s['call']: info_say_call.append(sid)
    elif k == 'ask':
        if not (s['on_yes'] and s['on_no'] and s['on_unknown']): combo.append(f'{sid}: ask 분기 누락')
        if s['next'] or s['call']: combo.append(f'{sid}: ask에 next/call')
        if not s['tts']: combo.append(f'{sid}: ask에 질문 문장 없음')
        if s['skip_if_known'] and not s['slot']: combo.append(f'{sid}: slot 없이 skip_if_known')
    elif k == 'call':
        if not (s['call'] and s['next']): combo.append(f'{sid}: call에 call/next 없음')
        if s['tts']: combo.append(f'{sid}: call에 tts')
        if s['device']: combo.append(f'{sid}: call에 device')
    elif k == 'return':
        if not s['next']: combo.append(f'{sid}: return에 기본 next 없음')
        if s['tts']: info_return_tts.append(sid)
    elif k == 'wait':
        if s['tts']: combo.append(f'{sid}: wait에 tts')
        if s['device']: combo.append(f'{sid}: wait에 device')
        if s['wait_timeout_s'] and not s['next']: combo.append(f'{sid}: 대기 시간이 있는데 next 없음')
    else:
        combo.append(f'{sid}: 알 수 없는 kind {k}')
    if s['on_timeout'] and not has_alert: combo.append(f'{sid}: on_timeout인데 alert:N 없음')
    if has_alert and s['kind'] != 'say': combo.append(f'{sid}: say가 아닌 단계에 alert')
    if s['alert_tts'] and (not has_alert or s['on_timeout']): combo.append(f'{sid}: alert_tts는 on_timeout 없는 alert 단계에만')
    if s['report_on_yes'] and k != 'ask': combo.append(f'{sid}: report_on_yes는 ask에만')
    if s['skip_flag'] and k not in ('ask', 'say'): combo.append(f'{sid}: skip_flag는 ask·say에만')
    if s['wait_timeout_s'] and k != 'wait': combo.append(f'{sid}: wait_timeout_s는 wait에만')
    if 'image' not in s: combo.append(f'{sid}: image 필드 없음')
    for d in s['device']:
        if not DEV_RE.fullmatch(d): combo.append(f'{sid}: 모르는 device {d}')
R['kind별 필드 조합 위반'] = combo
R['say + call (엔진 규칙 1이 처리)'] = info_say_call
R['return + tts (엔진 규칙 4가 처리)'] = info_return_tts
R['on_timeout도 alert_tts도 없는 알림 (T_ALERT_DONE)'] = [s['step_id'] for s in db['steps']
                                                    if any(re.fullmatch(r'alert:\d+', d) for d in s['device']) and not s['on_timeout'] and not s['alert_tts']]

# 5 slot 프리셋
pres = db['meta']['slot_presets']
bad = []
for s in db['steps']:
    if s['slot']:
        p = pres.get(s['slot'])
        if not p or s['slot_key'] != p['slot'] or s['slot_yes'] != p['yes'] or s['slot_no'] != p['no']:
            bad.append(s['step_id'])
R['slot 프리셋 불일치'] = bad
dom = {'conscious': {0, 1, 2}, 'breathing': {0, 1, 2}, 'bleeding': {0, 1}, 'movement': {0, 1}, 'scene_danger': {0, 1}}
R['프리셋이 덮지 않는 값 (알려져 있어도 물음)'] = {k: sorted(dom[p['slot']] - set(p['yes']) - set(p['no'])) for k, p in pres.items()
                                       if dom[p['slot']] - set(p['yes']) - set(p['no'])}

# 6 표시(flag)
set_f = {d.split(':')[1] for s in db['steps'] for d in s['device'] if d.startswith('flag:')}
unset_f = {d.split(':')[1] for s in db['steps'] for d in s['device'] if d.startswith('unflag:')}
used_f = {s['skip_flag'] for s in db['steps'] if s['skip_flag']}
R['켜는 곳 없는 표시'] = sorted((used_f | unset_f | {'cpr', 'call119'}) - set_f)
R['meta.flags 설명 없는 표시'] = sorted((set_f | unset_f | used_f) - set(db['meta']['flags']))
R['표시별 켜는 단계'] = {f: [s['step_id'] for s in db['steps'] if f'flag:{f}' in s['device']] for f in sorted(set_f)}
R['표시별 끄는 단계'] = {f: [s['step_id'] for s in db['steps'] if f'unflag:{f}' in s['device']] for f in sorted(unset_f)}

# 7 매뉴얼 필드
mf, feats = [], []
for m in db['manuals']:
    if m['type'] == 'situation':
        for f in ['spoken_name', 'summary']:
            if not m.get(f): mf.append(f"{m['manual_id']}.{f}")
        if m['featured'] is not None: feats.append(m['featured'])
    elif any(m.get(f) is not None for f in ['spoken_name', 'summary', 'featured']):
        mf.append(f"{m['manual_id']}: 공통 절차에 목록 필드")
if sorted(feats) != list(range(1, 7)):
    mf.append(f'featured 값 {sorted(feats)}')
R['매뉴얼 필드 누락·오류'] = mf
R['situation_code 매핑'] = sorted(m['situation_code'] for m in db['manuals'] if m['type'] == 'situation') == sorted(list(range(1, 16)) + [-1])

# 8 읽는 문장 규칙
viol = []


def chk(where, t):
    tt = re.sub(r'\{[a-z_]+\}', '', t or '')
    if re.search(r'[0-9]', tt): viol.append(f'{where}: 아라비아 숫자')
    if '/' in tt: viol.append(f"{where}: '/'")
    if re.search(r'[A-Za-z]', tt): viol.append(f'{where}: 영문')


for s in db['steps']:
    chk(s['step_id'], s['tts'])
    chk(s['step_id'] + '.alert_tts', s['alert_tts'])
for m in db['manuals']:
    chk(m['manual_id'] + '.intro_tts', m['intro_tts'])
    chk(m['manual_id'] + '.spoken_name', m.get('spoken_name'))
for t in db['templates']:
    chk(t['template_id'], t['tts'])
R['읽는 문장 규칙 위반 (숫자·/·영문)'] = viol
R['데이터에 남은 "조명"'] = [x for x in re.findall(r'[^"]{0,20}조명[^"]{0,20}', json.dumps(db, ensure_ascii=False))]

# 9 템플릿 status·note
tn = []
for t in db['templates']:
    if t.get('status') != '설계': tn.append(f"{t['template_id']}: status")
    if 'note' not in t: tn.append(f"{t['template_id']}: note 없음")
R['템플릿 status·note 누락'] = tn
R['단계 status 분포'] = dict(Counter(s['status'] for s in db['steps']))

# 10 보기용 카드 (v1.6)
vs_err = []
for m in db['manuals']:
    if m['type'] == 'situation':
        if not isinstance(m.get('view_steps'), list):
            vs_err.append(f"{m['manual_id']}: view_steps 없음")
            continue
        nos = []
        for c in m['view_steps']:
            if set(c) != {'no', 'kind', 'title', 'text', 'image'}:
                vs_err.append(f"{m['manual_id']}: 카드 필드 {sorted(c)}")
            if c.get('kind') not in ('check', 'call', 'action'):
                vs_err.append(f"{m['manual_id']}: 카드 kind {c.get('kind')}")
            nos.append(c.get('no'))
        if nos != list(range(1, len(nos) + 1)):
            vs_err.append(f"{m['manual_id']}: 카드 no {nos}")
    elif 'view_steps' in m:
        vs_err.append(f"{m['manual_id']}: 공통 절차에 view_steps")
R['보기용 카드 오류'] = vs_err

MUST_EMPTY = ['단계 ID 중복', '템플릿 ID 중복', '끊긴 단계 참조', '도달 못 하는 단계', 'dispatch에서 쓰는데 없는 템플릿',
              '엔진(명세서)에서 쓰는데 없는 템플릿', 'kind별 필드 조합 위반', 'slot 프리셋 불일치', '켜는 곳 없는 표시',
              'meta.flags 설명 없는 표시', '매뉴얼 필드 누락·오류', '읽는 문장 규칙 위반 (숫자·/·영문)', '보기용 카드 오류']
fails = [k for k in MUST_EMPTY if R[k]]
if R['주석판과 평문 JSON 데이터 일치'] is False:
    fails.append('주석판과 평문 JSON 데이터 일치')
if R['situation_code 매핑'] is not True:
    fails.append('situation_code 매핑')

for k, v in R.items():
    mark = 'FAIL' if k in fails else ('ok' if k in MUST_EMPTY else 'info')
    print(f'[{mark}] {k}: {v}')
print()
print('결과:', '실패 ' + ', '.join(fails) if fails else '통과')
sys.exit(1 if fails else 0)
