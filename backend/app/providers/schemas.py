from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ProviderKind = Literal["OLLAMA_LOCAL", "DECISION_RELAY", "TYPESAFE_OFFICIAL"]
ProviderAuthType = Literal["NONE", "BEARER", "ENV_REF"]
ProviderNetworkMode = Literal["DIRECT", "PROXY_FALLBACK", "CUSTOM_PROXY"]
ProviderStatus = Literal["ACTIVE", "DISABLED"]


class ProviderUpsertRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    provider_code: str = Field(..., min_length=1, max_length=64)
    provider_name: str = Field(..., min_length=1, max_length=128)
    provider_kind: ProviderKind
    endpoint_url: str = Field(..., min_length=1, max_length=255)
    models_list_url: str | None = Field(default=None, max_length=255)
    default_model: str = Field(..., min_length=1, max_length=128)
    auth_type: ProviderAuthType | None = None
    api_key_plain: str | None = None
    api_key_env_name: str | None = Field(default=None, max_length=64)
    network_mode: ProviderNetworkMode | None = None
    custom_proxy_url: str | None = Field(default=None, max_length=255)
    timeout_seconds: int = Field(default=90, ge=1, le=600)
    max_concurrency: int = Field(default=8, ge=1, le=256)
    max_questions_per_call: int = Field(default=64, ge=1, le=128)
    max_options_per_question: int = Field(default=24, ge=2, le=64)
    waf_strip_code_retry: bool | None = None
    cost_per_1m_input_tokens: float = Field(default=0.0, ge=0.0)
    extra_options_json: dict[str, Any] | None = Field(default=None)
    is_default: bool = False
    status: ProviderStatus = "ACTIVE"


class ProviderUpdateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    provider_code: str | None = Field(default=None, min_length=1, max_length=64)
    provider_name: str | None = Field(default=None, min_length=1, max_length=128)
    provider_kind: ProviderKind | None = None
    endpoint_url: str | None = Field(default=None, min_length=1, max_length=255)
    models_list_url: str | None = Field(default=None, max_length=255)
    default_model: str | None = Field(default=None, min_length=1, max_length=128)
    auth_type: ProviderAuthType | None = None
    api_key_plain: str | None = None
    api_key_env_name: str | None = Field(default=None, max_length=64)
    network_mode: ProviderNetworkMode | None = None
    custom_proxy_url: str | None = Field(default=None, max_length=255)
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)
    max_concurrency: int | None = Field(default=None, ge=1, le=256)
    max_questions_per_call: int | None = Field(default=None, ge=1, le=128)
    max_options_per_question: int | None = Field(default=None, ge=2, le=64)
    waf_strip_code_retry: bool | None = None
    cost_per_1m_input_tokens: float | None = Field(default=None, ge=0.0)
    extra_options_json: dict[str, Any] | None = Field(default=None)
    is_default: bool | None = None
    status: ProviderStatus | None = None


class ProviderProbeRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    model_override: str | None = Field(default=None, min_length=1, max_length=128)
