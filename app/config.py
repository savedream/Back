"""설정값. 모든 값은 SALYEO_ 접두사 환경변수로 덮어쓸 수 있다 (예: SALYEO_PORT=9000)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SALYEO_", env_file=".env", extra="ignore")

    # 서버. 기본값은 PC 개발용. 키트에서는 키트가 만든 전용 Wi-Fi(AP) 주소로 연다(SALYEO_HOST)
    # 인증은 Wi-Fi 비밀번호(WPA2)로 대신해 서버에는 두지 않는다
    host: str = "127.0.0.1"
    port: int = 8000
    # 기본 Swagger(/docs)는 CDN 스크립트를 불러오므로 개발 중에만 켠다
    enable_docs: bool = False
    # 브라우저(Expo 웹)에서 API를 부를 수 있게 허용할 화면 주소(CORS). 앱(iOS·Android)은 CORS를 검사하지 않아 필요 없다
    # 환경변수는 JSON 배열로 준다 (예: SALYEO_CORS_ORIGINS='["http://192.168.0.5:8081"]'). 빈 배열이면 CORS를 켜지 않는다
    cors_origins: list[str] = ["http://localhost:8081", "http://127.0.0.1:8081"]
    # 서버를 끌 때 열린 연결이 끝나기를 기다리는 최대 시간(초, uvicorn timeout_graceful_shutdown). TODO(미정: 임시값)
    shutdown_timeout_s: float = 3

    # 경로
    manual_db_path: Path = BACKEND_DIR / "data" / "manual_db.json"
    records_db_path: Path = BACKEND_DIR / "records.db"
    # 지금 부팅의 ID(Linux). 상태 스냅샷(6-14)이 같은 부팅의 것인지 가린다. 파일이 없으면 스냅샷에서 이어 가지 않는다
    boot_id_path: Path = Path("/proc/sys/kernel/random/boot_id")
    # 같은 부팅에서 백엔드만 다시 떴으면 상태 스냅샷에서 이어 간다(6-14). 끄면 늘 새 세션
    resume_from_snapshot: bool = True
    # 정적 파일(/static). 매뉴얼 아이콘·그림(static/manuals/)은 UI가 제공한다
    static_dir: Path = BACKEND_DIR / "static"

    # 버전 (GET /health). 매뉴얼 DB 버전은 적재한 DB의 meta.version에서 읽는다
    app_version: str = "1.3.0"
    model_version: str = "v2.2"

    # TODO(미정: 임시값) 3초 지연 예산 배분과 실측 뒤 확정
    vad_silence_s: float = 0.8
    max_utterance_s: float = 15  # 최대 발화 길이. 말하기 버튼 녹음도 여기서 자동 종료(①)
    model_timeout_s: float = 5
    post_tts_guard_ms: int = 300
    device_timeout_s: float = 2

    # SSE(GET /api/events)에서 이벤트가 없을 때 ping 주석을 보내는 간격(초)
    sse_ping_s: float = 15
    # 기기 상태(GET /api/devices)의 장치·시스템 칸을 다시 읽는 간격(초, 엔진 시계). 바뀐 부분은 status.changed로 보낸다
    device_status_interval_s: float = 5.0  # 임시값(미정)
    # 키트 끄기 때 처리 중인 턴(STT·모델)이 끝나기를 기다리는 최대 시간(모델 시간 제한 5초 + 여유). 임시값(미정)
    shutdown_turn_wait_s: float = 6.0

    # 기록 저장 재시도 간격(엔진 시계 기준, 시작값에서 두 배씩, 최대값까지). TODO(미정: 임시값)
    record_retry_base_s: float = 1
    record_retry_max_s: float = 60

    # 가짜 모드: 하드웨어·모델 없이 개발하고 테스트할 때 부품마다 가짜를 쓴다
    fake_model: bool = True
    # 실제 모델 폴더(fake_model=false일 때, transformers 형식: config.json·model.safetensors·tokenizer*.json)
    model_dir: Path | None = None
    fake_devices: bool = True
    fake_audio: bool = True
    fake_gps: bool = True
    # 가짜 모드에서 서버를 켜면 VAD·STT·TTS·LLM 적재와 예열을 바로 끝난 것으로 표시한다
    fake_auto_ready: bool = True
    # 가짜 STT가 알아들은 것으로 내는 문장(가짜 모델 표 첫 행, 화면 예시 문장). 시험은 FakeStt.queue로 다음 문장을 바꾼다
    fake_stt_text: str = "등산하다 넘어졌는데 다리를 움직이기 어려워요"


@lru_cache
def get_settings() -> Settings:
    return Settings()
