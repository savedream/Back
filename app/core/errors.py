"""오류 코드와 예외 처리기 (명세서 "공통 응답", "오류 처리")."""
import logging
from enum import Enum

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas.common import ErrorBody, ErrorResponse

log = logging.getLogger(__name__)


class ErrorCode(str, Enum):
    """명세서 "오류 처리" 표의 코드. 여기에 없는 코드는 쓰지 않는다."""

    STT_EMPTY = "STT_EMPTY"
    STT_FAILED = "STT_FAILED"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_FAILED = "MODEL_FAILED"
    MODEL_PARSE_ERROR = "MODEL_PARSE_ERROR"
    MODEL_OUT_OF_RANGE = "MODEL_OUT_OF_RANGE"
    MIC_UNAVAILABLE = "MIC_UNAVAILABLE"
    SPEAKER_UNAVAILABLE = "SPEAKER_UNAVAILABLE"
    TTS_FAILED = "TTS_FAILED"
    DEVICE_UNAVAILABLE = "DEVICE_UNAVAILABLE"
    GPS_NO_FIX = "GPS_NO_FIX"
    AUDIO_BUSY = "AUDIO_BUSY"
    NOT_LISTENING = "NOT_LISTENING"
    NOT_READY = "NOT_READY"
    INVALID_REQUEST = "INVALID_REQUEST"
    CONFIRM_REQUIRED = "CONFIRM_REQUIRED"
    MANUAL_NOT_FOUND = "MANUAL_NOT_FOUND"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SAVE_FAILED = "SAVE_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# REST로 나가는 코드의 HTTP 상태. 내부 전용 코드(STT_EMPTY 등)는 없다
HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.MIC_UNAVAILABLE: 503,
    ErrorCode.DEVICE_UNAVAILABLE: 503,
    ErrorCode.AUDIO_BUSY: 409,
    ErrorCode.NOT_LISTENING: 409,
    ErrorCode.NOT_READY: 503,
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.CONFIRM_REQUIRED: 400,
    ErrorCode.MANUAL_NOT_FOUND: 404,
    ErrorCode.SESSION_NOT_FOUND: 404,
    ErrorCode.SAVE_FAILED: 500,
    ErrorCode.INTERNAL_ERROR: 500,
}


class ApiError(Exception):
    def __init__(self, code: ErrorCode, message: str, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status if status is not None else HTTP_STATUS.get(code, 500)


def error_response(status: int, code: ErrorCode, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code.value, message=message))
    return JSONResponse(status_code=status, content=body.model_dump())


# pydantic 오류 type → 한국어 메시지
_VALIDATION_MESSAGES = {
    "missing": "필수 값입니다",
    "int_type": "정수여야 합니다",
    "int_parsing": "정수여야 합니다",
    "int_from_float": "정수여야 합니다",
    "float_type": "숫자여야 합니다",
    "float_parsing": "숫자여야 합니다",
    "bool_type": "true 또는 false여야 합니다",
    "bool_parsing": "true 또는 false여야 합니다",
    "string_type": "문자열이어야 합니다",
    "literal_error": "허용되지 않는 값입니다",
    "enum": "허용되지 않는 값입니다",
    "greater_than_equal": "값이 너무 작습니다",
    "less_than_equal": "값이 너무 큽니다",
    "json_invalid": "JSON 형식이 올바르지 않습니다",
    "model_attributes_type": "객체여야 합니다",
    "dict_type": "객체여야 합니다",
    "extra_forbidden": "알 수 없는 필드입니다",
}


def _validation_message(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "요청 형식이 올바르지 않습니다"
    first = errors[0]
    text = _VALIDATION_MESSAGES.get(first.get("type", ""), "형식이 올바르지 않습니다")
    # loc 첫 칸은 body·query·path 같은 위치라서 뺀다
    field = ".".join(str(p) for p in first.get("loc", ())[1:])
    return f"{field}: {text}" if field else text


async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return error_response(exc.status, exc.code, exc.message)


async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(400, ErrorCode.INVALID_REQUEST, _validation_message(exc))


async def _http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # 없는 경로(404)·허용되지 않은 메서드(405) 등. 명세서에 따로 코드가 없어 INVALID_REQUEST로 낸다
    message = {404: "없는 경로입니다", 405: "허용되지 않는 메서드입니다"}.get(
        exc.status_code, "요청 형식이 올바르지 않습니다"
    )
    return error_response(exc.status_code, ErrorCode.INVALID_REQUEST, message)


async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("처리하지 못한 예외: %s %s", request.method, request.url.path)
    return error_response(500, ErrorCode.INTERNAL_ERROR, "잠시 후 다시 시도해 주세요")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
