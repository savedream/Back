"""AppError: ErrorCode와 상황별 메시지를 담아 던지는 예외."""
from app.common.error_code import ErrorCode


class AppError(Exception):
    def __init__(self, code: ErrorCode, message: str | None = None):
        message = message if message is not None else code.default_message
        if message is None and code.http_status is not None:
            raise ValueError(f"{code.value}는 기본 메시지가 없어 message를 넘겨야 합니다")
        super().__init__(message)
        self.code = code
        self.message = message or ""
        self.status = code.http_status or 500
