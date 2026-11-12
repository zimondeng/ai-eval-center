from __future__ import annotations

import uuid
from contextvars import ContextVar, Token
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")

_request_id_ctx: ContextVar[str | None] = ContextVar("eval_request_id", default=None)


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:16]}"


def set_current_request_id(request_id: str) -> Token[str | None]:
    return _request_id_ctx.set(request_id)


def reset_current_request_id(token: Token[str | None]) -> None:
    _request_id_ctx.reset(token)


def get_current_request_id() -> str:
    current = _request_id_ctx.get()
    if current:
        return current
    return new_request_id()


class ApiEnvelope(BaseModel, Generic[T]):
    code: int = 200
    message: str = "ok"
    request_id: str
    data: T | None = None


def build_envelope(
    data: Any = None,
    *,
    code: int = 200,
    message: str = "ok",
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "request_id": request_id or get_current_request_id(),
        "data": data,
    }


ok_response = build_envelope
