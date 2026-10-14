# [살려드림] - Backend

> 응급안내키트 백엔드  
> 통신이 안 되는 곳에서 응급 처치를 음성으로 안내하는 온디바이스 키트

---

## 📌 프로젝트 소개

살려드림은 산·계곡처럼 통신이 안 되는 곳에서 일반인에게 응급 처치를 음성으로 안내하는 키트입니다.  
사용자의 말을 키트 안에서 인식·해석하고, 매뉴얼 DB에 따라 처치 절차를 음성과 화면으로 안내합니다.  
실행 중에는 인터넷을 쓰지 않습니다.

---
## ✨기술 스택

### 백엔드 (FastAPI, 온디바이스)
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

### 실행

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

| 명령어 | 설명 |
| --- | --- |
| `uvicorn app.main:app --host 127.0.0.1 --port 8000` | 개발 서버 실행 |
| `pytest -q` | 테스트 |
| `python tools/check_manual_db.py` | 매뉴얼 DB 검사 |

설정값은 `SALYEO_` 접두사 환경변수로 바꿀 수 있습니다 (예: `SALYEO_PORT=9000`).

---

### Git 명령어

| 명령어 | 설명 |
| --- | --- |
| `git clone <url>` | 원격 저장소 복제 |
| `git add .` | 전체 변경 파일 스테이징 |
| `git add <파일명>` | 특정 파일만 스테이징 |
| `git commit -m "메시지"` | 커밋 메시지와 함께 커밋 |
| `git log` | 커밋 히스토리 확인 |
| `git branch` | 현재 브랜치 목록 확인 |
| `git checkout <이름>` | 해당 브랜치로 이동 |
| `git checkout -b <이름>` | 새 브랜치 생성 + 이동 |
| `git push` | 현재 브랜치 내용을 원격 `main`에 푸시 |
| `git pull origin main` | 원격 `main` 브랜치 내용 가져오기 (병합) |

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
