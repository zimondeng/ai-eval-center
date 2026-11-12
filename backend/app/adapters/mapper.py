from __future__ import annotations

import copy
import ipaddress
import json
import os
from collections.abc import Mapping
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

import httpx
from jsonpath_ng.exceptions import JsonPathParserError
from jsonpath_ng.ext import parse as parse_jsonpath

from app.adapters.models import (
    RenderedSutRequest,
    SutAdapterConfig,
    SutExecutionError,
    SutExecutionInput,
    SutExecutionResult,
)
from app.common.crypto import mask_secret
from app.common.http_client import request_with_policy


@lru_cache(maxsize=256)
def compile_jsonpath(expr_str: str) -> Any:
    cleaned = expr_str.strip()
    if not cleaned.startswith("$"):
        raise ValueError(f"JSONPath 必须以 '$' 开头: {expr_str}")
    try:
        return parse_jsonpath(cleaned)
    except (JsonPathParserError, Exception) as exc:
        raise ValueError(f"非法 JSONPath 表达式 '{expr_str}': {exc}") from exc


def evaluate_jsonpath_matches(payload: Any, expr_str: str) -> list[Any]:
    if payload is None:
        return []
    expr = compile_jsonpath(expr_str)
    return [m.value for m in expr.find(payload)]


def evaluate_jsonpath_value(payload: Any, expr_str: str) -> Any:
    matches = evaluate_jsonpath_matches(payload, expr_str)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    return matches


def validate_mapping_jsonpaths(
    request_mapping: Mapping[str, Any] | None,
    response_mapping: Mapping[str, Any] | None,
) -> None:
    if request_mapping:
        for map_key in ("field_map", "record_field_map"):
            field_map = request_mapping.get(map_key)
            if field_map is not None:
                if not isinstance(field_map, Mapping):
                    raise ValueError(f"request_mapping_json.{map_key} 必须为对象字典")
                for key, path_expr in field_map.items():
                    if not isinstance(key, str) or not key.strip():
                        raise ValueError(f"request_mapping_json.{map_key} 包含空字段名")
                    if isinstance(path_expr, str) and path_expr.strip().startswith("$"):
                        compile_jsonpath(path_expr)

    if response_mapping:
        for key in ("actual_output", "actual_references", "sut_trace", "delta_path"):
            val = response_mapping.get(key)
            if isinstance(val, str) and val.strip():
                compile_jsonpath(val)
        structured_spec = response_mapping.get("actual_structured")
        if isinstance(structured_spec, str) and structured_spec.strip():
            compile_jsonpath(structured_spec)
        elif isinstance(structured_spec, Mapping):
            for _, sub_expr in structured_spec.items():
                if isinstance(sub_expr, str) and sub_expr.strip().startswith("$"):
                    compile_jsonpath(sub_expr)


def build_auth_headers(
    auth_config: Mapping[str, Any] | None,
) -> tuple[dict[str, str], dict[str, str]]:
    if not auth_config:
        return {}, {}

    raw_token: str | None = None
    if auth_config.get("token") is not None and str(auth_config.get("token")) != "":
        raw_token = str(auth_config["token"])
    elif auth_config.get("env_name"):
        env_key = str(auth_config["env_name"]).strip()
        if env_key:
            env_val = os.environ.get(env_key, "")
            if env_val:
                raw_token = env_val

    if not raw_token:
        return {}, {}

    header_name = str(auth_config.get("header") or "Authorization").strip()
    if not header_name:
        return {}, {}
    prefix = str(auth_config.get("prefix") if auth_config.get("prefix") is not None else "")

    headers = {header_name: f"{prefix}{raw_token}"}
    masked = {header_name: f"{prefix}{mask_secret(raw_token) or '****'}"}
    return headers, masked


def render_sut_request(
    config: SutAdapterConfig,
    exec_input: SutExecutionInput,
    *,
    url_override: str | None = None,
    default_body_template: Mapping[str, Any] | None = None,
    default_field_map: Mapping[str, str] | None = None,
) -> RenderedSutRequest:
    req_mapping = config.request_mapping_json or {}
    body: dict[str, Any] = copy.deepcopy(dict(default_body_template or {}))
    explicit_template = req_mapping.get("body_template")
    if isinstance(explicit_template, Mapping):
        body.update(copy.deepcopy(dict(explicit_template)))

    source_ctx: dict[str, Any] = {
        "input_text": exec_input.input_text,
        "conversation_turns_json": exec_input.conversation_turns_json,
        "sut_input_json": exec_input.sut_input_json or {},
        "sut_context_json": exec_input.sut_context_json,
        "run_overrides": exec_input.run_overrides,
    }

    field_map = req_mapping.get("field_map")
    active_field_map: Mapping[str, Any] = (
        field_map if isinstance(field_map, Mapping) else (default_field_map or {})
    )

    for target_field, expr_or_literal in active_field_map.items():
        if isinstance(expr_or_literal, str) and expr_or_literal.strip().startswith("$"):
            val = evaluate_jsonpath_value(source_ctx, expr_or_literal)
        else:
            val = copy.deepcopy(expr_or_literal)

        if val is None:
            # Omit None unless already populated in template (preserving template default)
            continue
        body[target_field] = copy.deepcopy(val)

    headers, masked_headers = build_auth_headers(config.auth_config_json)
    return RenderedSutRequest(
        method="POST",
        url=url_override or config.endpoint_url,
        headers=headers,
        masked_headers=masked_headers,
        body=body,
    )


def _coerce_output_string(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        return "".join(_coerce_output_string(item) for item in val if item is not None)
    if isinstance(val, (int, float, bool)):
        return str(val)
    return json.dumps(val, ensure_ascii=False)


def _coerce_reference_items(matches: list[Any]) -> list[str]:
    refs: list[str] = []
    for match_val in matches:
        items = match_val if isinstance(match_val, list) else [match_val]
        for item in items:
            if item is None:
                continue
            if isinstance(item, str):
                refs.append(item)
            elif isinstance(item, Mapping):
                if isinstance(item.get("content"), str):
                    refs.append(item["content"])
                elif isinstance(item.get("text"), str):
                    refs.append(item["text"])
                else:
                    refs.append(json.dumps(dict(item), ensure_ascii=False))
            else:
                refs.append(str(item))
    return refs


def _extract_structured(payload: Any, spec: Any) -> dict[str, Any]:
    if not spec:
        return {}
    if isinstance(spec, str):
        val = evaluate_jsonpath_value(payload, spec)
        if val is None:
            return {}
        if isinstance(val, Mapping):
            return copy.deepcopy(dict(val))
        return {"value": copy.deepcopy(val)}
    if isinstance(spec, Mapping):
        out: dict[str, Any] = {}
        for field_name, sub_spec in spec.items():
            if isinstance(sub_spec, str) and sub_spec.strip().startswith("$"):
                out[field_name] = copy.deepcopy(evaluate_jsonpath_value(payload, sub_spec))
            elif isinstance(sub_spec, Mapping):
                out[field_name] = _extract_structured(payload, sub_spec)
            else:
                out[field_name] = copy.deepcopy(sub_spec)
        return out
    return {}


def _extract_trace(payload: Any, spec: Any) -> dict[str, Any] | list[Any] | None:
    if not spec:
        return None
    if isinstance(spec, str):
        matches = evaluate_jsonpath_matches(payload, spec)
        if not matches:
            return None
        if len(matches) == 1:
            single = matches[0]
            if single is None:
                return None
            if isinstance(single, Mapping):
                return copy.deepcopy(dict(single))
            if isinstance(single, list):
                return copy.deepcopy(single)
            return {"value": copy.deepcopy(single)}
        return copy.deepcopy(matches)
    if isinstance(spec, Mapping):
        return _extract_structured(payload, spec)
    return None


def extract_sut_response(
    payload: Any,
    response_mapping: Mapping[str, Any] | None,
) -> tuple[str, list[str], dict[str, Any], dict[str, Any] | list[Any] | None]:
    mapping = response_mapping or {}

    output_expr = mapping.get("actual_output")
    if isinstance(output_expr, str) and output_expr.strip():
        raw_out = evaluate_jsonpath_value(payload, output_expr)
        actual_output = _coerce_output_string(raw_out)
    else:
        actual_output = ""

    refs_expr = mapping.get("actual_references")
    if isinstance(refs_expr, str) and refs_expr.strip():
        ref_matches = evaluate_jsonpath_matches(payload, refs_expr)
        actual_references = _coerce_reference_items(ref_matches)
    else:
        actual_references = []

    actual_structured = _extract_structured(payload, mapping.get("actual_structured"))
    sut_trace = _extract_trace(payload, mapping.get("sut_trace"))

    return actual_output, actual_references, actual_structured, sut_trace


def resolve_sut_network_policy(
    url: str,
    config: SutAdapterConfig,
) -> tuple[str, str | None]:
    req_map = config.request_mapping_json or {}
    auth_cfg = config.auth_config_json or {}
    explicit_mode = req_map.get("network_mode") or auth_cfg.get("network_mode")
    custom_proxy = req_map.get("custom_proxy_url") or auth_cfg.get("custom_proxy_url")
    if isinstance(explicit_mode, str) and explicit_mode.strip():
        return explicit_mode.strip().upper(), (
            str(custom_proxy).strip() if custom_proxy else None
        )

    host = (urlsplit(url).hostname or "").strip().lower()
    if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} or host.endswith(
        (".local", ".internal")
    ):
        return "DIRECT", None
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_loopback or ip.is_private or ip.is_link_local:
            return "DIRECT", None
    except ValueError:
        pass
    return "PROXY_FALLBACK", None


async def dispatch_json_sut_request(
    config: SutAdapterConfig,
    rendered: RenderedSutRequest,
    *,
    target_label: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[Any, int, dict[str, Any]]:
    preview = rendered.to_preview_dict()
    network_mode, custom_proxy_url = resolve_sut_network_policy(rendered.url, config)

    try:
        dispatch = await request_with_policy(
            rendered.method,
            rendered.url,
            network_mode=network_mode,
            custom_proxy_url=custom_proxy_url,
            timeout_seconds=float(config.timeout_seconds),
            headers=rendered.headers,
            json=rendered.body,
            transport=transport,
        )
    except httpx.HTTPError as exc:
        raise SutExecutionError(
            f"调用{target_label}网络异常: {exc}",
            request_preview=preview,
        ) from exc

    resp = dispatch.response
    if resp.status_code >= 400:
        body_snippet = resp.text[:500]
        raise SutExecutionError(
            f"{target_label}返回异常状态码 HTTP {resp.status_code}: {body_snippet}",
            status_code=resp.status_code,
            request_preview=preview,
            upstream_body=body_snippet,
        )

    try:
        payload = resp.json()
    except ValueError as exc:
        body_snippet = resp.text[:500]
        raise SutExecutionError(
            f"{target_label}响应非合法 JSON: {body_snippet}",
            status_code=resp.status_code,
            request_preview=preview,
            upstream_body=body_snippet,
        ) from exc

    return payload, dispatch.elapsed_ms, preview


async def execute_json_sut_request(
    config: SutAdapterConfig,
    rendered: RenderedSutRequest,
    *,
    target_label: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SutExecutionResult:
    payload, elapsed_ms, preview = await dispatch_json_sut_request(
        config,
        rendered,
        target_label=target_label,
        transport=transport,
    )
    actual_output, actual_references, actual_structured, sut_trace = extract_sut_response(
        payload,
        config.response_mapping_json,
    )
    return SutExecutionResult(
        actual_output=actual_output,
        actual_references=actual_references,
        actual_structured=actual_structured,
        sut_trace=sut_trace,
        sut_latency_ms=elapsed_ms,
        ttft_ms=None,
        raw_response=payload,
        request_preview=preview,
    )
