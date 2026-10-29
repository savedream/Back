#!/usr/bin/env python3
"""매뉴얼 DB 검사.

사용: python tools/check_manual_db.py [DB json] [주석판 jsonc]
기본값: data/manual_db.json, 주석판은 docs/ 또는 상위 폴더의 docs/의 manual_db_주석판_{DB 버전}.jsonc (없으면 비교 생략)
반드시 통과해야 하는 항목이 하나라도 어긋나면 종료 코드 1.
검사 항목은 서버 시작 때와 같은 app/services/manual_db.py를 쓴다. 주석판 비교만 이 도구에서 한다.
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.services.manual_db import MUST_EMPTY, check, failures  # noqa: E402

db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'data' / 'manual_db.json'
db = json.loads(db_path.read_text(encoding='utf-8'))
# meta.version 예: "v1.7 (2026-10-14, …)" → "v1.7"
db_version = db['meta']['version'].split()[0]
if len(sys.argv) > 2:
    jsonc_path = Path(sys.argv[2])
else:
    # 주석판은 레포 밖(상위 폴더의 docs/)에 있을 수 있다. 없으면 비교를 건너뛴다
    jsonc_name = f'manual_db_주석판_{db_version}.jsonc'
    jsonc_path = next((p for p in [ROOT / 'docs' / jsonc_name, ROOT.parent / 'docs' / jsonc_name] if p.exists()),
                      ROOT / 'docs' / jsonc_name)
db_c = None
if jsonc_path.exists():
    jsonc = jsonc_path.read_text(encoding='utf-8')
    db_c = json.loads('\n'.join(l for l in jsonc.split('\n') if not l.strip().startswith('//')))

JSONC_KEY = '주석판과 평문 JSON 데이터 일치'
results = check(db)
R = {JSONC_KEY: (db == db_c) if db_c is not None else '주석판 없음(건너뜀)', **results}
fails = failures(results)
if R[JSONC_KEY] is False:
    fails.append(JSONC_KEY)

for k, v in R.items():
    mark = 'FAIL' if k in fails else ('ok' if k in MUST_EMPTY else 'info')
    print(f'[{mark}] {k}: {v}')
print()
print('결과:', '실패 ' + ', '.join(fails) if fails else '통과')
sys.exit(1 if fails else 0)
