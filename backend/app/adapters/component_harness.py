from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

import httpx

from app.adapters.mapper import (
    build_auth_headers,
    dispatch_json_sut_request,
    evaluate_jsonpath_value,
    execute_json_sut_request,
    render_sut_request,
)
from app.adapters.models import (
    ComponentRecordInput,
    ComponentRecordResult,
    RenderedSutRequest,
    SutAdapterConfig,
    SutExecutionError,
    SutExecutionInput,
    SutExecutionResult,
)

DEFAULT_HARNESS_REPLAY_FIELD_MAP: dict[str, str] = {
    "component": "$.sut_input_json.component",
    "query": "$.input_text",
    "turns": "$.conversation_turns_json",
    "sut_context": "$.sut_context_json",
    "overrides": "$.run_overrides",
}

_STANDARD_CASE_KEYS = frozenset(
    {
        "input_text",
        "conversation_turns_json",
        "sut_input_json",
        "sut_context_json",
        "run_overrides",
        "query",
        "turns",
    }
)


def _build_record_body(
    record_input: Any,
    request_mapping: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    req_map = request_mapping or {}
    record_field_map = req_map.get("record_field_map")
    if isinstance(record_field_map, Mapping):
        exec_in = SutExecutionInput.from_case(record_input)
        source_ctx: dict[str, Any] = {
            "input_text": exec_in.input_text,
            "conversation_turns_json": exec_in.conversation_turns_json,
            "sut_input_json": exec_in.sut_input_json or {},
            "sut_context_json": exec_in.sut_context_json,
            "run_overrides": exec_in.run_overrides,
        }
        rec_template = req_map.get("record_body_template")
        mapped_body: dict[str, Any] = (
            copy.deepcopy(dict(rec_template))
            if isinstance(rec_template, Mapping)
            else {}
        )
        for target_field, expr_or_literal in record_field_map.items():
            if isinstance(expr_or_literal, str) and expr_or_literal.strip().startswith("$"):
                val = evaluate_jsonpath_value(source_ctx, expr_or_literal)
            else:
                val = copy.deepcopy(expr_or_literal)
            if val is None:
                continue
            mapped_body[target_field] = copy.deepcopy(val)
        return mapped_body

    if isinstance(record_input, (ComponentRecordInput, SutExecutionInput)):
        query = record_input.input_text
        turns = (
            list(record_input.conversation_turns_json)
            if record_input.conversation_turns_json is not None
            else []
        )
        extra = dict(record_input.sut_input_json or {})
    elif isinstance(record_input, Mapping):
        query = str(record_input.get("input_text", record_input.get("query", "")))
        turns_raw = record_input.get("conversation_turns_json", record_input.get("turns"))
        turns = list(turns_raw) if isinstance(turns_raw, list) else []
        extra = dict(record_input.get("sut_input_json") or {})
        for k, v in record_input.items():
            if k not in _STANDARD_CASE_KEYS and k not in extra:
                extra[k] = v
    else:
        query = str(getattr(record_input, "input_text", ""))
        turns_raw = getattr(record_input, "conversation_turns_json", None)
        turns = list(turns_raw) if isinstance(turns_raw, list) else []
        extra = dict(getattr(record_input, "sut_input_json", None) or {})

    body: dict[str, Any] = {"query": query}
    for k, v in extra.items():
        if k == "component" or v is None:
            continue
        body[k] = copy.deepcopy(v)
    body["turns"] = copy.deepcopy(turns)
    return body


class ComponentHarnessAdapter:
    def __init__(self, config: SutAdapterConfig) -> None:
        self.config = config

    def render_replay_request(self, exec_input: SutExecutionInput) -> RenderedSutRequest:
        rendered = render_sut_request(
            self.config,
            exec_input,
            default_body_template={"turns": [], "overrides": {}},
            default_field_map=DEFAULT_HARNESS_REPLAY_FIELD_MAP,
        )
        if (
            not rendered.body.get("component")
            and isinstance(exec_input.run_overrides, Mapping)
            and exec_input.run_overrides.get("component")
        ):
            rendered.body["component"] = exec_input.run_overrides["component"]
        return rendered

    async def replay(
        self,
        exec_input: SutExecutionInput,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> SutExecutionResult:
        rendered = self.render_replay_request(exec_input)
        return await execute_json_sut_request(
            self.config,
            rendered,
            target_label="组件回放网关",
            transport=transport,
        )

    async def record(
        self,
        record_input: Any,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> ComponentRecordResult:
        if self.config.adapter_type.upper() != "COMPONENT_HARNESS":
            raise SutExecutionError(
                f"仅 COMPONENT_HARNESS 适配器支持快照录制，当前类型: {self.config.adapter_type}"
            )
        record_url = (self.config.record_endpoint_url or "").strip()
        if not record_url:
            raise SutExecutionError(
                f"适配器 '{self.config.adapter_code}' 未配置 record_endpoint_url"
            )

        headers, masked_headers = build_auth_headers(self.config.auth_config_json)
        body = _build_record_body(record_input, self.config.request_mapping_json)
        rendered = RenderedSutRequest(
            method="POST",
            url=record_url,
            headers=headers,
            masked_headers=masked_headers,
            body=body,
        )
        payload, elapsed_ms, preview = await dispatch_json_sut_request(
            self.config,
            rendered,
            target_label="组件快照录制端点",
            transport=transport,
        )

        if not isinstance(payload, Mapping):
            raise SutExecutionError(
                "组件快照录制端点响应必须为 JSON 对象",
                status_code=200,
                request_preview=preview,
            )

        data_node = payload.get("data")
        root: Mapping[str, Any] = (
            data_node
            if isinstance(data_node, Mapping) and "sut_context" in data_node
            else payload
        )
        sut_ctx = root.get("sut_context")
        if not isinstance(sut_ctx, Mapping):
            raise SutExecutionError(
                "组件快照录制端点响应缺少合法的 sut_context 对象",
                status_code=200,
                request_preview=preview,
            )

        ref_out_raw = (
            root.get("reference_output")
            if root.get("reference_output") is not None
            else (
                root.get("actual_output")
                if root.get("actual_output") is not None
                else (
                    root.get("online_output")
                    if root.get("online_output") is not None
                    else root.get("answer")
                )
            )
        )
        reference_output = str(ref_out_raw) if ref_out_raw is not None else None

        return ComponentRecordResult(
            sut_context=copy.deepcopy(dict(sut_ctx)),
            reference_output=reference_output,
            raw_response=copy.deepcopy(dict(payload)),
            sut_latency_ms=elapsed_ms,
            request_preview=preview,
        )
