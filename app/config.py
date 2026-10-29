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

    # 경로
    manual_db_path: Path = BACKEND_DIR / "data" / "manual_db.json"
    records_db_path: Path = BACKEND_DIR / "records.db"

    # 버전 (GET /health). 매뉴얼 DB 버전은 적재한 DB의 meta.version에서 읽는다
    app_version: str = "1.3.0"
    model_version: str = "v2.2"

    # TODO(미정: 임시값) 3초 지연 예산 배분과 실측 뒤 확정
    vad_silence_s: float = 0.8
    max_utterance_s: float = 15
    model_timeout_s: float = 5
    post_tts_guard_ms: int = 300
    device_timeout_s: float = 2

    # SSE(GET /api/events)에서 이벤트가 없을 때 ping 주석을 보내는 간격(초)
    sse_ping_s: float = 15

    # 기록 저장 재시도 간격(엔진 시계 기준, 시작값에서 두 배씩, 최대값까지). TODO(미정: 임시값)
    record_retry_base_s: float = 1
    record_retry_max_s: float = 60

    # 모델 프롬프트 앞에 <|im_start|>를 붙일지. TODO(미정: 모델 팀 확인)
    prompt_prefix_im_start: bool = False

    # 가짜 모드: 하드웨어·모델 없이 개발하고 테스트할 때 부품마다 가짜를 쓴다
    fake_model: bool = True
    fake_devices: bool = True
    fake_audio: bool = True
    fake_gps: bool = True
    # 가짜 모드에서 서버를 켜면 VAD·STT·TTS·LLM 적재와 예열을 바로 끝난 것으로 표시한다
    fake_auto_ready: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
