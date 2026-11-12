from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Sequence

from fastapi import APIRouter, FastAPI

ROUTE_MODULES: tuple[str, ...] = (
    "app.api.health",
    "app.api.auth",
    "app.api.providers",
    "app.api.dimensions",
    "app.api.suites",
    "app.api.adapters",
    "app.api.datasets",
    "app.api.cases",
    "app.api.runs",
)


def register_api_routers(
    app: FastAPI,
    *,
    prefix: str = "/api/v1",
    module_names: Sequence[str] = ROUTE_MODULES,
) -> list[str]:
    mounted: list[str] = []
    for mod_name in module_names:
        try:
            spec = importlib.util.find_spec(mod_name)
        except ModuleNotFoundError:
            spec = None
        if spec is None:
            continue
        module = importlib.import_module(mod_name)
        router = getattr(module, "router", None)
        if not isinstance(router, APIRouter):
            continue
        already_prefixed = router.prefix.startswith(prefix) or (
            bool(router.routes)
            and all(
                (getattr(route, "path", None) or "").startswith(prefix)
                for route in router.routes
            )
        )
        if already_prefixed:
            app.include_router(router)
        else:
            app.include_router(router, prefix=prefix)
        mounted.append(mod_name)
    return mounted
