from __future__ import annotations

import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.registry import register_api_routers
from app.common.envelope import (
    new_request_id,
    reset_current_request_id,
    set_current_request_id,
)
from app.common.errors import register_exception_handlers
from app.common.http_client import aclose_http_client_pools
from app.config import Settings, get_settings
from app.db.seed import init_db
from app.db.session import get_engine

_SAFE_REQ_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if cfg.auto_init_db:
            init_db(get_engine(cfg), seed=True, settings=cfg)
        try:
            yield
        finally:
            await aclose_http_client_pools()

    application = FastAPI(
        title=cfg.app_name,
        version="1.0.0",
        lifespan=lifespan,
    )

    if settings is not None:
        application.dependency_overrides[get_settings] = lambda: cfg

    @application.middleware("http")
    async def _request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get("X-Request-ID", "").strip()
        req_id = incoming if _SAFE_REQ_ID_RE.match(incoming) else new_request_id()
        request.state.request_id = req_id
        token = set_current_request_id(req_id)
        try:
            response = await call_next(request)
        finally:
            reset_current_request_id(token)
        response.headers.setdefault("X-Request-ID", req_id)
        return response

    dist_dir = Path(cfg.web_dist_dir)
    spa_enabled = (dist_dir / "index.html").is_file()

    register_exception_handlers(
        application,
        spa_dist_dir=dist_dir if spa_enabled else None,
    )
    register_api_routers(application, prefix="/api/v1")

    if spa_enabled:
        assets_dir = dist_dir / "assets"
        if assets_dir.is_dir():
            application.mount(
                "/assets",
                StaticFiles(directory=str(assets_dir)),
                name="spa-assets",
            )

        @application.get("/", include_in_schema=False)
        async def _serve_spa_root() -> FileResponse:
            return FileResponse(dist_dir / "index.html")

    return application


app = create_app()
