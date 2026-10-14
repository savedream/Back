"""전역 예외 핸들러. AppError, 요청 검증 실패(422→400), 처리 못 한 예외(500)를
실패 봉투 {"ok": false, "error": {"code", "message"}}로 바꾼다 (명세서 "공통 응답", "오류 처리")."""
import logging
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.common.error_code import ErrorCode
from app.common.exceptions import AppError

log = logging.getLogger(__name__)


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    ok: Literal[False] = False
    error: ErrorBody


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


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
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
    return error_response(500, ErrorCode.INTERNAL_ERROR, ErrorCode.INTERNAL_ERROR.default_message)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
