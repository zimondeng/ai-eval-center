from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.common.envelope import build_envelope, get_current_request_id


class AppError(Exception):
    def __init__(
        self,
        message: str,
        status_code: int = 400,
        code: int | None = None,
        data: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code if code is not None else status_code
        self.data = data


def _resolve_req_id(request: Request) -> str:
    state_req_id = getattr(request.state, "request_id", None)
    if isinstance(state_req_id, str) and state_req_id:
        return state_req_id
    return get_current_request_id()


def _try_serve_spa_fallback(request: Request, dist_dir: Path | None) -> Response | None:
    if dist_dir is None:
        return None
    if request.method not in ("GET", "HEAD"):
        return None
    path = request.url.path
    if path == "/api" or path.startswith("/api/"):
        return None
    index_file = dist_dir / "index.html"
    if not index_file.is_file():
        return None
    rel_part = path.lstrip("/")
    if rel_part:
        try:
            resolved_root = dist_dir.resolve()
            candidate = (resolved_root / rel_part).resolve()
            if candidate.is_relative_to(resolved_root) and candidate.is_file():
                return FileResponse(candidate)
        except OSError:
            pass
    return FileResponse(index_file)


def register_exception_handlers(app: FastAPI, *, spa_dist_dir: Path | None = None) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        req_id = _resolve_req_id(request)
        return JSONResponse(
            status_code=exc.status_code,
            content=build_envelope(
                exc.data,
                code=exc.code,
                message=exc.message,
                request_id=req_id,
            ),
            headers={"X-Request-ID": req_id},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> Response:
        if exc.status_code == 404:
            spa_resp = _try_serve_spa_fallback(request, spa_dist_dir)
            if spa_resp is not None:
                return spa_resp
        req_id = _resolve_req_id(request)
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=build_envelope(
                None,
                code=exc.status_code,
                message=detail,
                request_id=req_id,
            ),
            headers={"X-Request-ID": req_id},
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        req_id = _resolve_req_id(request)
        return JSONResponse(
            status_code=422,
            content=build_envelope(
                {"errors": jsonable_encoder(exc.errors())},
                code=422,
                message="请求参数校验失败",
                request_id=req_id,
            ),
            headers={"X-Request-ID": req_id},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        req_id = _resolve_req_id(request)
        return JSONResponse(
            status_code=500,
            content=build_envelope(
                None,
                code=500,
                message="服务器内部错误",
                request_id=req_id,
            ),
            headers={"X-Request-ID": req_id},
        )
