from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.schemas import ApiKeyCreateRequest, LoginRequest
from app.auth.service import (
    authenticate_admin_user,
    create_api_key_record,
    get_current_user_profile,
    list_api_keys,
    revoke_api_key_record,
)
from app.common.auth_deps import CurrentPrincipal, require_console_user
from app.common.envelope import ok_response
from app.config import Settings, get_settings
from app.db.session import get_db

router = APIRouter(tags=["auth"])


@router.post("/auth/login")
def login_admin(
    payload: LoginRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    result = authenticate_admin_user(
        db,
        username=payload.username,
        password=payload.password,
        settings=settings,
    )
    return ok_response(result)


@router.get("/auth/me")
def get_me(
    principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(get_current_user_profile(db, principal))


@router.get("/api-keys")
def get_api_keys(
    _principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(list_api_keys(db))


@router.post("/api-keys")
def create_api_key(
    payload: ApiKeyCreateRequest,
    principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(
        create_api_key_record(db, payload, created_by=principal.username)
    )


@router.post("/api-keys/{key_id}/revoke")
def revoke_api_key(
    key_id: int,
    _principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(revoke_api_key_record(db, key_id))


@router.delete("/api-keys/{key_id}")
def delete_api_key(
    key_id: int,
    _principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(revoke_api_key_record(db, key_id))
