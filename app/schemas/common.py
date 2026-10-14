"""공통 응답 형식 (명세서 "공통 규칙" 응답)."""
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class OkResponse(BaseModel, Generic[T]):
    ok: Literal[True] = True
    data: T


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    ok: Literal[False] = False
    error: ErrorBody


def ok(data: Any = None) -> dict:
    return {"ok": True, "data": data}
