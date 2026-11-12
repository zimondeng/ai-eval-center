from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.schemas import ApiKeyCreateRequest
from app.common.auth_deps import CurrentPrincipal, create_access_token
from app.common.crypto import sha256_hex, verify_password
from app.common.errors import AppError
from app.config import Settings, get_settings
from app.db.base import utcnow_naive
from app.db.models import AdminUser, ApiKey


def _fmt_dt(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _to_utc_naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def serialize_admin_user(user: AdminUser) -> dict[str, Any]:
    return {
        "id": user.id,
        "uuid": user.uuid,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "is_enabled": bool(user.is_enabled),
        "last_login_at": _fmt_dt(user.last_login_at),
        "create_time": _fmt_dt(user.create_time),
    }


def serialize_api_key(row: ApiKey) -> dict[str, Any]:
    return {
        "id": row.id,
        "uuid": row.uuid,
        "key_name": row.key_name,
        "key_prefix": row.key_prefix,
        "rate_limit_rpm": row.rate_limit_rpm,
        "status": row.status,
        "expires_at": _fmt_dt(row.expires_at),
        "last_used_at": _fmt_dt(row.last_used_at),
        "created_by": row.created_by,
        "create_time": _fmt_dt(row.create_time),
        "update_time": _fmt_dt(row.update_time),
    }


def generate_raw_api_key() -> tuple[str, str, str]:
    raw_key = f"evk_live_{secrets.token_hex(20)}"
    key_prefix = raw_key[:13]
    key_sha256 = sha256_hex(raw_key)
    return raw_key, key_prefix, key_sha256


def authenticate_admin_user(
    db: Session,
    username: str,
    password: str,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    cleaned_username = username.strip()
    user = db.scalar(
        select(AdminUser).where(
            AdminUser.username == cleaned_username,
            AdminUser.is_deleted == 0,
        )
    )
    if user is None or not verify_password(password, user.password_hash):
        raise AppError("用户名或密码错误", status_code=401, code=401)
    if user.is_enabled != 1:
        raise AppError("管理员账号已被禁用", status_code=401, code=401)

    user.last_login_at = utcnow_naive()
    db.commit()
    db.refresh(user)

    access_token = create_access_token(
        subject=user.username,
        user_id=user.id,
        role=user.role,
        settings=cfg,
    )
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": cfg.jwt_expire_minutes * 60,
        "user": serialize_admin_user(user),
    }


def get_current_user_profile(
    db: Session,
    principal: CurrentPrincipal,
) -> dict[str, Any]:
    user = db.scalar(
        select(AdminUser).where(
            AdminUser.id == principal.principal_id,
            AdminUser.is_deleted == 0,
        )
    )
    if user is None or user.is_enabled != 1:
        raise AppError("管理员账号不存在或已被禁用", status_code=401, code=401)
    profile = serialize_admin_user(user)
    profile.update(
        {
            "principal_type": principal.principal_type,
            "principal_id": principal.principal_id,
            "identifier": principal.identifier,
        }
    )
    return profile


def list_api_keys(db: Session) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(ApiKey)
        .where(ApiKey.is_deleted == 0)
        .order_by(ApiKey.id.desc())
    ).all()
    return [serialize_api_key(row) for row in rows]


def create_api_key_record(
    db: Session,
    payload: ApiKeyCreateRequest,
    *,
    created_by: str | None = None,
) -> dict[str, Any]:
    raw_key, key_prefix, key_sha256 = generate_raw_api_key()
    expires_at: datetime | None = None
    if payload.expires_at is not None:
        expires_at = _to_utc_naive(payload.expires_at)
    elif payload.expires_in_days is not None:
        expires_at = utcnow_naive() + timedelta(days=payload.expires_in_days)

    row = ApiKey(
        key_name=payload.key_name.strip(),
        key_prefix=key_prefix,
        key_sha256=key_sha256,
        rate_limit_rpm=payload.rate_limit_rpm,
        status="ACTIVE",
        expires_at=expires_at,
        created_by=created_by,
        is_deleted=0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    data = serialize_api_key(row)
    data["api_key_plain"] = raw_key
    return data


def revoke_api_key_record(db: Session, key_id: int) -> dict[str, Any]:
    row = db.scalar(
        select(ApiKey).where(
            ApiKey.id == key_id,
            ApiKey.is_deleted == 0,
        )
    )
    if row is None:
        raise AppError("API Key 不存在", status_code=404, code=404)
    row.status = "REVOKED"
    row.update_time = utcnow_naive()
    db.commit()
    db.refresh(row)
    return serialize_api_key(row)
