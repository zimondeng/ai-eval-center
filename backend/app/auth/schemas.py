from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class ApiKeyCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    key_name: str = Field(..., min_length=1, max_length=128)
    rate_limit_rpm: int = Field(default=300, ge=1, le=100000)
    expires_at: datetime | None = None
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
