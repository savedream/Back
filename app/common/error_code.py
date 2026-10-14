"""오류 코드 (명세서 "오류 처리" 표). 여기에 없는 코드는 쓰지 않는다.

코드마다 HTTP 상태와 기본 메시지를 둔다.
- HTTP 상태가 None이면 REST로 나가지 않는 내부 코드다.
- 기본 메시지는 명세서 응답 예시 중 상황과 상관없이 늘 같은 문구만 둔다.
  None이면 던질 때 해당 API 절의 응답 예시 문구를 넘긴다.
"""
from enum import Enum


class ErrorCode(str, Enum):
    http_status: int | None
    default_message: str | None

    def __new__(cls, code: str, http_status: int | None, default_message: str | None):
        obj = str.__new__(cls, code)
        obj._value_ = code
        obj.http_status = http_status
        obj.default_message = default_message
        return obj

    STT_EMPTY = ("STT_EMPTY", None, None)
    STT_FAILED = ("STT_FAILED", None, None)
    MODEL_TIMEOUT = ("MODEL_TIMEOUT", None, None)
    MODEL_FAILED = ("MODEL_FAILED", None, None)
    MODEL_PARSE_ERROR = ("MODEL_PARSE_ERROR", None, None)
    MODEL_OUT_OF_RANGE = ("MODEL_OUT_OF_RANGE", None, None)
    MIC_UNAVAILABLE = ("MIC_UNAVAILABLE", 503, "마이크가 연결되지 않았습니다")
    SPEAKER_UNAVAILABLE = ("SPEAKER_UNAVAILABLE", None, None)
    TTS_FAILED = ("TTS_FAILED", None, None)
    DEVICE_UNAVAILABLE = ("DEVICE_UNAVAILABLE", 503, None)
    GPS_NO_FIX = ("GPS_NO_FIX", None, None)
    AUDIO_BUSY = ("AUDIO_BUSY", 409, None)
    NOT_LISTENING = ("NOT_LISTENING", 409, "녹음 중이 아닙니다")
    NOT_READY = ("NOT_READY", 503, "준비 중입니다")
    INVALID_REQUEST = ("INVALID_REQUEST", 400, None)
    CONFIRM_REQUIRED = ("CONFIRM_REQUIRED", 400, "confirm: true가 필요합니다")
    MANUAL_NOT_FOUND = ("MANUAL_NOT_FOUND", 404, None)
    SESSION_NOT_FOUND = ("SESSION_NOT_FOUND", 404, "세션이 없습니다")
    SAVE_FAILED = ("SAVE_FAILED", 500, None)
    INTERNAL_ERROR = ("INTERNAL_ERROR", 500, "잠시 후 다시 시도해 주세요")
