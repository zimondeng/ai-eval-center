from __future__ import annotations

import httpx

from app.adapters.mapper import (
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
from app.common.http_client import request_with_policy


class HttpJsonAdapter:
    def __init__(self, config: SutAdapterConfig) -> None:
        self.config = config

    async def execute(
        self,
        exec_input: SutExecutionInput,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> SutExecutionResult:
        rendered = render_sut_request(self.config, exec_input)
        preview = rendered.to_preview_dict()
        network_mode, custom_proxy_url = resolve_sut_network_policy(
            rendered.url, self.config
        )

        try:
            dispatch = await request_with_policy(
                rendered.method,
                rendered.url,
                network_mode=network_mode,
                custom_proxy_url=custom_proxy_url,
                timeout_seconds=float(self.config.timeout_seconds),
                headers=rendered.headers,
                json=rendered.body,
                transport=transport,
            )
        except httpx.HTTPError as exc:
            raise SutExecutionError(
                f"调用被测系统 HTTP_JSON 接口网络异常: {exc}",
                request_preview=preview,
            ) from exc

        resp = dispatch.response
        if resp.status_code >= 400:
            body_snippet = resp.text[:500]
            raise SutExecutionError(
                f"被测系统返回异常状态码 HTTP {resp.status_code}: {body_snippet}",
                status_code=resp.status_code,
                request_preview=preview,
                upstream_body=body_snippet,
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            body_snippet = resp.text[:500]
            raise SutExecutionError(
                f"被测系统响应非合法 JSON: {body_snippet}",
                status_code=resp.status_code,
                request_preview=preview,
                upstream_body=body_snippet,
            ) from exc

        actual_output, actual_references, actual_structured, sut_trace = extract_sut_response(
            payload,
            self.config.response_mapping_json,
        )
        return SutExecutionResult(
            actual_output=actual_output,
            actual_references=actual_references,
            actual_structured=actual_structured,
            sut_trace=sut_trace,
            sut_latency_ms=dispatch.elapsed_ms,
            ttft_ms=None,
            raw_response=payload,
            request_preview=preview,
        )
