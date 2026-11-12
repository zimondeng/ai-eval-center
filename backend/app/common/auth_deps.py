from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import jwt
from fastapi import Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.crypto import sha256_hex
from app.common.errors import AppError
from app.config import Settings, get_settings
from app.db.base import utcnow_naive
from app.db.models import AdminUser, ApiKey
from app.db.session import get_db


class CurrentPrincipal(BaseModel):
    principal_type: Literal["ADMIN_USER", "API_KEY"]
    principal_id: int
    identifier: str
    display_name: str
    role: str

    @property
    def username(self) -> str:
        return self.identifier


def create_access_token(
    subject: str,
    *,
    user_id: int | None = None,
    role: str = "ADMIN",
    expires_minutes: int | None = None,
    settings: Settings | None = None,
) -> str:
    cfg = settings or get_settings()
    ttl = expires_minutes if expires_minutes is not None else cfg.jwt_expire_minutes
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl)).timestamp()),
    }
    if user_id is not None:
        payload["uid"] = user_id
    return jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_algorithm)


def decode_access_token(
    token: str,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    try:
        decoded = jwt.decode(token, cfg.jwt_secret, algorithms=[cfg.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise AppError("无效或已过期的访问令牌", status_code=401, code=401) from exc
    if not isinstance(decoded, dict) or not decoded.get("sub"):
        raise AppError("访问令牌载荷不合法", status_code=401, code=401)
    return decoded


def _extract_bearer_token(request: Request) -> str | None:
    auth_header = request.headers.get("Authorization", "").strip()
    if not auth_header:
        return None
    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        return None
    return parts[1].strip()


def require_console_user(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CurrentPrincipal:
    token = _extract_bearer_token(request)
    if not token:
        raise AppError("未提供有效的控制台登录凭证", status_code=401, code=401)
    claims = decode_access_token(token, settings=settings)
    subject = str(claims["sub"])
    user = db.scalar(
        select(AdminUser).where(
            AdminUser.username == subject,
            AdminUser.is_deleted == 0,
        )
    )
    if user is None or user.is_enabled != 1:
        raise AppError("管理员账号不存在或已被禁用", status_code=401, code=401)
    uid_claim = claims.get("uid")
    if uid_claim is not None and int(uid_claim) != user.id:
        raise AppError("访问令牌身份不匹配", status_code=401, code=401)
    return CurrentPrincipal(
        principal_type="ADMIN_USER",
        principal_id=user.id,
        identifier=user.username,
        display_name=user.display_name,
        role=user.role,
    )


def require_console_or_api_key(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CurrentPrincipal:
    bearer = _extract_bearer_token(request)
    if bearer is not None:
        return require_console_user(request=request, db=db, settings=settings)

    raw_api_key = request.headers.get("X-API-Key", "").strip()
    if not raw_api_key:
        raise AppError("未提供有效的登录凭证或 X-API-Key", status_code=401, code=401)

    digest = sha256_hex(raw_api_key)
    key_row = db.scalar(
        select(ApiKey).where(
            ApiKey.key_sha256 == digest,
            ApiKey.is_deleted == 0,
        )
    )
    now = utcnow_naive()
    if key_row is None or key_row.status != "ACTIVE":
        raise AppError("API Key 无效或已被吊销", status_code=401, code=401)
    if key_row.expires_at is not None and key_row.expires_at <= now:
        raise AppError("API Key 已过期", status_code=401, code=401)

    key_row.last_used_at = now
    db.commit()

    return CurrentPrincipal(
        principal_type="API_KEY",
        principal_id=key_row.id,
        identifier=key_row.key_prefix,
        display_name=key_row.key_name,
        role="API_KEY",
    )
