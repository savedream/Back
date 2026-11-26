# [살려드림] - Backend

> 응급안내키트 백엔드  
> 통신이 안 되는 곳에서 응급 처치를 음성으로 안내하는 온디바이스 키트

---

## 📌 프로젝트 소개

살려드림은 산·계곡처럼 통신이 안 되는 곳에서 일반인에게 응급 처치를 음성으로 안내하는 키트입니다.  
사용자의 말을 키트 안에서 인식·해석하고, 매뉴얼 DB에 따라 처치 절차를 음성과 화면으로 안내합니다.  
실행 중에는 인터넷을 쓰지 않습니다.

| 저장소 | 설명 |
|---|---|
| [savedream](https://github.com/savedream) | 조직 소개 (다이어그램, 주요 기능, 데모) |
| [Front](https://github.com/savedream/Front) | 아이패드 화면 (React Native, Expo) |
| **Back** (이 저장소) | 키트 백엔드 (FastAPI, 절차 엔진, 처치 기록) |

---

## 👥 팀 소개

2026-2학기 모바일시스템응용프로젝트(산학협력, 마음AI 멘토링) 7반 1팀 「취업하고싶다」

<div align="center">

<table>
  <tr>
    <td align="center" width="20%"><a href="https://github.com/12hsh12"><img src="https://github.com/12hsh12.png?size=120" width="96" alt="12hsh12"/><br/><b>Hong Sunghwan</b></a><br/><sub>🧠 AI 모델</sub></td>
    <td align="center" width="20%"><a href="https://github.com/tpdus112"><img src="https://github.com/tpdus112.png?size=120" width="96" alt="tpdus112"/><br/><b>윤세연</b></a><br/><sub>📱 Frontend</sub></td>
    <td align="center" width="20%"><a href="https://github.com/yoominho114"><img src="https://github.com/yoominho114.png?size=120" width="96" alt="yoominho114"/><br/><b>yoominho114</b></a><br/><sub>📱 Frontend</sub></td>
    <td align="center" width="20%"><a href="https://github.com/zzuhannn"><img src="https://github.com/zzuhannn.png?size=120" width="96" alt="zzuhannn"/><br/><b>zzuhannn</b></a><br/><sub>🧰 Backend</sub></td>
    <td align="center" width="20%"><a href="https://github.com/YJEND"><img src="https://github.com/YJEND.png?size=120" width="96" alt="YJEND"/><br/><b>Yongju Choi</b></a><br/><sub>🧰 Backend</sub></td>
  </tr>
</table>

</div>

---

## ✨ 기술 스택

| 구분 | 기술 |
|------|------|
| 언어 | Python 3.10+ |
| 프레임워크 | FastAPI, uvicorn |
| 데이터 검증 | pydantic v2 |
| DB | SQLite (처치 기록), JSON (매뉴얼 DB) |
| API 통신 | REST API, SSE (키트 ↔ 아이패드 화면) |
| 음성 처리 | sherpa-onnx (VAD, STT, TTS) |
| AI 모델 처리 | Llama-3.2-1B + LoRA (발화 해석) |
| 하드웨어 | Qualcomm QCS6490 (Linux) |
| 테스트 | pytest, pytest-asyncio, httpx |

---

## 🔎 현재 상태

- 키트(QCS6490)에 올려 실제 모델·음성(VAD·STT·TTS)·GPS·스피커를 연결해 전체 흐름이 동작하는 것을 확인했습니다. 키트에 부저가 없어 메트로놈 박자도 스피커로 냅니다.
- 키트용 부품 연결 코드는 키트에만 있고 이 저장소에는 포함하지 않았습니다.
- 이 저장소의 기본 실행은 **가짜 부품 모드**입니다. 모델은 정해 둔 문장 표(`app/fakes/fake_model_table.json`), 마이크·STT·스피커·GPS·기기·전원은 가짜 부품을 써서 키트 없이 PC에서 API 전체를 시험할 수 있습니다.
- 실제 부품은 `app/services`의 자리(Protocol)에 끼웁니다. PC용 실제 모델 실행(`app/services/hf_model.py`)은 아래 "실제 모델로 실행"을 보세요.

---

## 🗂️ 폴더 구조

```
app/
├── api/        REST·SSE 엔드포인트 (/api/...)
├── core/       부품 조립, 준비 상태, 한 턴 처리(정규화 → 모델 → 파서 → 엔진), 말하기 녹음, 기기 상태 갱신
├── services/   절차 엔진(procedure_engine), 매뉴얼 DB, 모델 실행, 처치 기록(SQLite), 이벤트, 부품 자리(Protocol)
├── fakes/      가짜 부품 (모델 문장 표, 마이크·STT, 스피커, GPS, 기기, 전원)
├── schemas/    요청·응답·이벤트 데이터 형식
└── common/     응답 봉투, 오류 코드
data/           매뉴얼 DB (manual_db.json, v1.7)
tests/          unit · api · scenario · integration
tools/          매뉴얼 DB 검사, 실제 모델 출력 비교
```

---

## 🔌 API

응답은 `{"ok": true, "data": ...}` 또는 `{"ok": false, "error": {"code", "message"}}` 형식입니다. 화면 상태는 이벤트 스트림(SSE)으로 바뀝니다.

| 기능 | 엔드포인트 |
|---|---|
| 시스템 | `GET /api/health` · `POST /api/system/shutdown` |
| 말하기·마이크 | `POST /api/speech/start` · `/stop` · `/restart` · `POST /api/mic/pause` · `/resume` · `POST /api/utterances` (문장 시험용) |
| 기기 | `POST /api/metronome/on` · `/off` · `POST /api/rescue-signal/on` · `/off` · `POST /api/volume/up` · `/down` · `POST /api/timer/start` · `/pause` · `/reset` · `GET /api/devices` |
| 위치 | `GET /api/location` · `POST /api/location/announce` |
| 매뉴얼 | `GET /api/manuals` · `GET /api/manuals/{manual_id}` |
| 세션·기록 | `GET /api/session` · `GET /api/session/log` · `POST /api/session/reset` · `POST /api/records/retry` |
| 이벤트 | `GET /api/events` (SSE) |

---

## ▶️ 실행

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

| 명령어 | 설명 |
| --- | --- |
| `python -m app.run` | 개발 서버 실행 (host·port는 `SALYEO_HOST`·`SALYEO_PORT`, 기본 127.0.0.1:8000) |
| `pytest -q` | 테스트 |
| `python tools/check_manual_db.py` | 매뉴얼 DB 검사 |
| `python tools/check_model.py [모델 폴더]` | 실제 모델 출력을 가짜 모델 표와 비교 |

설정값은 `SALYEO_` 접두사 환경변수로 바꿀 수 있습니다 (예: `SALYEO_PORT=9000`).
`uvicorn app.main:app`으로도 뜨지만, 그때는 이벤트 스트림(`/api/events`)을 연 채 끄면 `--timeout-graceful-shutdown`까지(안 주면 끝없이) 기다립니다.

웹 브라우저(Expo 웹)에서 API를 부르려면 화면 주소를 `SALYEO_CORS_ORIGINS`에 JSON 배열로 줍니다 (기본 `["http://localhost:8081", "http://127.0.0.1:8081"]`, 빈 배열이면 끔). iOS·Android 앱은 필요 없습니다.

#### 도커로 실행 (가짜 부품)

파이썬 없이 Docker Desktop(Windows는 WSL2)만 있으면 됩니다.

```bash
git clone https://github.com/savedream/Back.git
cd Back
docker compose up --build
```

`http://localhost:8000/api/health`에서 `"ready": true`가 보이면 준비된 것입니다. 처치 기록은 컨테이너 안에 저장되어 `docker compose down`을 하면 지워집니다.

#### 실제 모델로 실행 (PC)

기본은 가짜 모델(정해 둔 문장 표)입니다. 받은 모델 폴더(`config.json`, `model.safetensors`, `tokenizer.json`, `tokenizer_config.json`, `generation_config.json`)를 저장소 밖에 두고 실행합니다.

```bash
pip install -e ".[dev,model]"
SALYEO_FAKE_MODEL=false SALYEO_MODEL_DIR=../models/maum python -m app.run
```

서버를 켠 뒤 모델 적재와 예열 추론이 끝나야 `/api/health`가 `ready: true`가 됩니다(맥에서 약 8초). 맥 GPU(MPS)가 있으면 쓰고, 없으면 CPU입니다(CPU는 문장 하나에 2~6초라 모델 시간 제한 5초에 걸릴 수 있습니다).
같은 환경변수를 주고 `pytest tests/scenario`를 돌리면 시나리오 시험을 실제 모델로 돌립니다.

---

## 🌿 Git Flow 브랜치 전략 (with `main`) 🌿


### 🌴 기본 브랜치
| 브랜치 | 역할 |
|--------|------|
| `main`     | 최종 배포용 브랜치 (stable) |


<br>

### 🌱 **작업 브랜치 네이밍 규칙**

```
type/#issue번호  (작업 단위는 기능/수정/리팩토링 등으로 구분)
```


| prefix       | 설명                         | 예시                              |
|--------------|------------------------------|-----------------------------------|
| `feat/`   | ✨ 새로운 기능 개발             | `feat/#15`           |
| `fix/`       | 🐛 버그 수정                    | `fix/#42`     |
| `refactor/`  | ♻️ 코드 리팩토링                | `refactor/#23`       |
| `chore/`     | 🔧 설정 변경, 잡일              | `chore/#25`        |
| `perf/`      | ⚡ 성능 개선                    | `perf/#94`        |
| `hotfix/`    | 🚑 급한 수정 (main에서 바로 분기) | `hotfix/#102`          |
| `test/`      | 🧪 테스트 코드 추가/수정        | `test/#55`    |

<br>

### ✍️ **커밋 메시지 규칙**

```
type : 한국어 설명
```

| type | 예시 |
|------|------|
| `feat` | `feat : 매뉴얼 DB 로더 추가` |
| `fix` | `fix : 준비 전 health 응답 수정` |
| `refactor` | `refactor : 오류 처리기 정리` |
| `chore` | `chore : pyproject 의존성 추가` |
| `perf` | `perf : 모델 예열 시간 단축` |
| `hotfix` | `hotfix : 서버 시작 오류 수정` |
| `test` | `test : 절차 엔진 시나리오 테스트 추가` |

<br>


---
