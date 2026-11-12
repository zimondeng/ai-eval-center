from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

import httpx

from app.adapters.mapper import (
    evaluate_jsonpath_value,
    extract_sut_response,
    render_sut_request,
    resolve_sut_network_policy,
)
from app.adapters.models import (
    SutAdapterConfig,
    SutExecutionError,
    SutExecutionInput,
    SutExecutionResult,
)
from app.common.http_client import stream_with_policy


def _extract_event_delta(
    event_obj: Any,
    response_mapping: Mapping[str, Any],
    accumulated_text: str,
) -> str:
    delta_path = response_mapping.get("delta_path")
    if isinstance(delta_path, str) and delta_path.strip():
        val = evaluate_jsonpath_value(event_obj, delta_path)
        return val if isinstance(val, str) else ("" if val is None else str(val))

    if not isinstance(event_obj, Mapping):
        return ""

    if isinstance(event_obj.get("delta"), str):
        return event_obj["delta"]

    nested_data = event_obj.get("data")
    if isinstance(nested_data, Mapping) and isinstance(nested_data.get("delta"), str):
        return nested_data["delta"]

    choices = event_obj.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        if isinstance(first_choice, Mapping):
            choice_delta = first_choice.get("delta")
            if isinstance(choice_delta, Mapping) and isinstance(
                choice_delta.get("content"), str
            ):
                return choice_delta["content"]

    output_expr = response_mapping.get("actual_output")
    if isinstance(output_expr, str) and output_expr.strip():
        matched = evaluate_jsonpath_value(event_obj, output_expr)
        if isinstance(matched, str):
            if accumulated_text and matched == accumulated_text:
                return ""
            return matched

    if isinstance(event_obj.get("answer"), str):
        ans = event_obj["answer"]
        return "" if (accumulated_text and ans == accumulated_text) else ans
    if isinstance(event_obj.get("content"), str):
        cnt = event_obj["content"]
        return "" if (accumulated_text and cnt == accumulated_text) else cnt

    return ""


class HttpSseAdapter:
    def __init__(self, config: SutAdapterConfig) -> None:
        self.config = config

    async def execute(
        self,
        exec_input: SutExecutionInput,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> SutExecutionResult:
        rendered = render_sut_request(self.config, exec_input)
        headers = {"Accept": "text/event-stream", **rendered.headers}
        preview = rendered.to_preview_dict()
        network_mode, custom_proxy_url = resolve_sut_network_policy(
            rendered.url, self.config
        )
        response_mapping = self.config.response_mapping_json or {}

        structured_spec = response_mapping.get("actual_structured")
        collected_structured: dict[str, Any] = (
            {k: None for k in structured_spec}
            if isinstance(structured_spec, Mapping)
            else {}
        )
        collected_references: list[str] = []
        collected_trace: dict[str, Any] | list[Any] | None = None
        raw_events: list[Any] = []
        chunks: list[str] = []
        ttft_ms: int | None = None

        t0 = time.perf_counter()
        try:
            async with stream_with_policy(
                rendered.method,
                rendered.url,
                network_mode=network_mode,
                custom_proxy_url=custom_proxy_url,
                timeout_seconds=float(self.config.timeout_seconds),
                headers=headers,
                json=rendered.body,
                transport=transport,
            ) as (resp, _used_proxy):
                if resp.status_code >= 400:
                    err_bytes = await resp.aread()
                    body_snippet = err_bytes.decode("utf-8", errors="replace")[:500]
                    raise SutExecutionError(
                        f"被测系统 SSE 接口返回异常状态码 HTTP {resp.status_code}: {body_snippet}",
                        status_code=resp.status_code,
                        request_preview=preview,
                        upstream_body=body_snippet,
                    )

                async for raw_line in resp.aiter_lines():
                    line = raw_line.rstrip("\r")
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue

                    data_str = line[6:] if line.startswith("data: ") else line[5:]
                    if data_str.strip() == "[DONE]":
                        break

                    try:
                        event_obj = json.loads(data_str)
                    except ValueError:
                        if data_str != "":
                            if ttft_ms is None:
                                ttft_ms = max(
                                    0, int(round((time.perf_counter() - t0) * 1000))
                                )
                            chunks.append(data_str)
                        continue

                    raw_events.append(event_obj)
                    delta_text = _extract_event_delta(
                        event_obj, response_mapping, "".join(chunks)
                    )
                    if delta_text:
                        if ttft_ms is None:
                            ttft_ms = max(
                                0, int(round((time.perf_counter() - t0) * 1000))
                            )
                        chunks.append(delta_text)

                    _, ev_refs, ev_struct, ev_trace = extract_sut_response(
                        event_obj, response_mapping
                    )
                    if ev_refs:
                        collected_references = ev_refs
                    if ev_struct:
                        for k, v in ev_struct.items():
                            if v is not None:
                                collected_structured[k] = v
                    if ev_trace is not None:
                        collected_trace = ev_trace
        except httpx.HTTPError as exc:
            raise SutExecutionError(
                f"调用被测系统 HTTP_SSE 接口网络异常: {exc}",
                request_preview=preview,
            ) from exc

        sut_latency_ms = max(0, int(round((time.perf_counter() - t0) * 1000)))
        if ttft_ms is not None and sut_latency_ms < ttft_ms:
            sut_latency_ms = ttft_ms

        return SutExecutionResult(
            actual_output="".join(chunks),
            actual_references=collected_references,
            actual_structured=collected_structured,
            sut_trace=collected_trace,
            sut_latency_ms=sut_latency_ms,
            ttft_ms=ttft_ms,
            raw_response=raw_events,
            request_preview=preview,
        )
