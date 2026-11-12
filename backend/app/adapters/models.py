from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field


class SutExecutionError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_preview: dict[str, Any] | None = None,
        upstream_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.request_preview = request_preview
        self.upstream_body = upstream_body


class SutAdapterConfig(BaseModel):
    adapter_code: str
    adapter_name: str = ""
    eval_level: str = "E2E"
    adapter_type: str
    endpoint_url: str
    record_endpoint_url: str | None = None
    auth_config_json: dict[str, Any] | None = None
    request_mapping_json: dict[str, Any] = Field(default_factory=dict)
    response_mapping_json: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = 60
    max_concurrency: int = 4
    status: str = "ACTIVE"

    @classmethod
    def from_adapter(cls, adapter: Any) -> SutAdapterConfig:
        if isinstance(adapter, cls):
            return adapter
        if isinstance(adapter, Mapping):
            return cls.model_validate(dict(adapter))
        return cls(
            adapter_code=str(getattr(adapter, "adapter_code", "inline_adapter")),
            adapter_name=str(getattr(adapter, "adapter_name", "") or ""),
            eval_level=str(getattr(adapter, "eval_level", "E2E") or "E2E"),
            adapter_type=str(getattr(adapter, "adapter_type")),
            endpoint_url=str(getattr(adapter, "endpoint_url")),
            record_endpoint_url=getattr(adapter, "record_endpoint_url", None),
            auth_config_json=getattr(adapter, "auth_config_json", None),
            request_mapping_json=dict(getattr(adapter, "request_mapping_json", None) or {}),
            response_mapping_json=dict(getattr(adapter, "response_mapping_json", None) or {}),
            timeout_seconds=int(getattr(adapter, "timeout_seconds", 60) or 60),
            max_concurrency=int(getattr(adapter, "max_concurrency", 4) or 4),
            status=str(getattr(adapter, "status", "ACTIVE") or "ACTIVE"),
        )


class SutExecutionInput(BaseModel):
    input_text: str
    conversation_turns_json: list[dict[str, Any]] | None = None
    sut_input_json: dict[str, Any] | None = None
    sut_context_json: dict[str, Any] | None = None
    run_overrides: dict[str, Any] | None = None

    @classmethod
    def from_case(
        cls,
        case_input: Any,
        *,
        run_overrides: Mapping[str, Any] | None = None,
    ) -> SutExecutionInput:
        if isinstance(case_input, cls):
            base = case_input
        elif isinstance(case_input, Mapping):
            base = cls(
                input_text=str(case_input.get("input_text", case_input.get("query", ""))),
                conversation_turns_json=case_input.get("conversation_turns_json"),
                sut_input_json=case_input.get("sut_input_json"),
                sut_context_json=case_input.get("sut_context_json"),
                run_overrides=case_input.get("run_overrides"),
            )
        else:
            base = cls(
                input_text=str(getattr(case_input, "input_text")),
                conversation_turns_json=getattr(case_input, "conversation_turns_json", None),
                sut_input_json=getattr(case_input, "sut_input_json", None),
                sut_context_json=getattr(case_input, "sut_context_json", None),
                run_overrides=getattr(case_input, "run_overrides", None),
            )

        if run_overrides is not None:
            merged = dict(base.run_overrides or {})
            merged.update(dict(run_overrides))
            return base.model_copy(update={"run_overrides": merged})
        return base


class RenderedSutRequest(BaseModel):
    method: str = "POST"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    masked_headers: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] = Field(default_factory=dict)

    def to_preview_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "url": self.url,
            "headers": dict(self.masked_headers),
            "body": self.body,
        }


class SutExecutionResult(BaseModel):
    actual_output: str = ""
    actual_references: list[str] = Field(default_factory=list)
    actual_structured: dict[str, Any] = Field(default_factory=dict)
    sut_trace: dict[str, Any] | list[Any] | None = None
    sut_latency_ms: int = 0
    ttft_ms: int | None = None
    raw_response: dict[str, Any] | list[Any] | str | None = None
    request_preview: dict[str, Any] | None = None


class ComponentRecordInput(BaseModel):
    input_text: str
    conversation_turns_json: list[dict[str, Any]] | None = None
    sut_input_json: dict[str, Any] | None = None


class ComponentRecordResult(BaseModel):
    sut_context: dict[str, Any]
    reference_output: str | None = None
    raw_response: dict[str, Any] = Field(default_factory=dict)
    sut_latency_ms: int = 0
    request_preview: dict[str, Any] | None = None
