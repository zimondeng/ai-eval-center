from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.common.auth_deps import CurrentPrincipal, require_console_user
from app.common.envelope import ok_response
from app.config import Settings, get_settings
from app.db.session import get_db
from app.providers.schemas import (
    ProviderProbeRequest,
    ProviderUpdateRequest,
    ProviderUpsertRequest,
)
from app.providers.service import (
    fetch_provider_models,
    get_provider_by_code,
    list_providers,
    probe_provider_connectivity,
    serialize_provider,
    update_provider,
    upsert_provider,
)

router = APIRouter(tags=["providers"])


def get_provider_http_transport() -> httpx.AsyncBaseTransport | None:
    return None


@router.get("/providers")
def get_providers(
    status: str | None = Query(default=None),
    _principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(list_providers(db, status=status))


@router.get("/providers/{provider_code}")
def get_provider(
    provider_code: str,
    _principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = get_provider_by_code(db, provider_code)
    return ok_response(serialize_provider(row))


@router.post("/providers")
def create_or_upsert_provider(
    payload: ProviderUpsertRequest,
    principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return ok_response(
        upsert_provider(
            db,
            payload,
            operator=principal.username,
            settings=settings,
        )
    )


@router.put("/providers/{provider_code}")
@router.patch("/providers/{provider_code}")
def modify_provider(
    provider_code: str,
    payload: ProviderUpdateRequest,
    _principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return ok_response(
        update_provider(
            db,
            provider_code,
            payload,
            settings=settings,
        )
    )


@router.post("/providers/{provider_code}/test")
async def test_provider_connectivity(
    provider_code: str,
    payload: ProviderProbeRequest | None = Body(default=None),
    _principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    transport: httpx.AsyncBaseTransport | None = Depends(get_provider_http_transport),
) -> dict[str, Any]:
    result = await probe_provider_connectivity(
        db,
        provider_code,
        model_override=payload.model_override if payload is not None else None,
        settings=settings,
        transport=transport,
    )
    return ok_response(result)


@router.get("/providers/{provider_code}/models")
async def get_provider_models(
    provider_code: str,
    _principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    transport: httpx.AsyncBaseTransport | None = Depends(get_provider_http_transport),
) -> dict[str, Any]:
    result = await fetch_provider_models(
        db,
        provider_code,
        settings=settings,
        transport=transport,
    )
    return ok_response(result)

