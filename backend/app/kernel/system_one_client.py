from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from app.common.errors import AppError
from app.common.http_client import request_with_policy
from app.config import Settings, get_settings
from app.db.models import DecisionProvider
from app.kernel.batch_compiler import CompiledBatchCall
from app.kernel.scoring import parse_choice_answer, parse_score_answer
from app.providers.client import (
    build_system_one_payload,
    parse_noul_answer,
    resolve_effective_network_mode,
    resolve_provider_api_key,
)


class SystemOneUpstreamError(AppError):
    """System One 判别模型渠道上游请求或响应解析异常。"""


_FENCED_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~")


def strip_fenced_code_blocks(value: Any) -> Any:
    """递归剥离 state 字符串中的 Markdown 围栏代码块，避免边缘 WAF HTML 403 误杀。"""
    if isinstance(value, str):
        return _FENCED_CODE_BLOCK_RE.sub("", value)
    if isinstance(value, Mapping):
        return {k: strip_fenced_code_blocks(v) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [strip_fenced_code_blocks(item) for item in value]
    return value


def _extract_answers_dict(
    body: Mapping[str, Any],
    expected_keys: set[str],
) -> Mapping[str, Any]:
    for top_key in ("answers", "decisions", "results"):
        candidate = body.get(top_key)
        if isinstance(candidate, Mapping):
            return candidate

    nested_data = body.get("data")
    if isinstance(nested_data, Mapping):
        for sub_key in ("answers", "decisions", "results"):
            sub_val = nested_data.get(sub_key)
            if isinstance(sub_val, Mapping):
                return sub_val
        if expected_keys and expected_keys.issubset(set(nested_data.keys())):
            return nested_data

    if expected_keys and expected_keys.issubset(set(body.keys())):
        return body

    raise SystemOneUpstreamError("上游响应缺少 answers 决策字典", status_code=502, code=502)


def _extract_token_usage(body: Mapping[str, Any]) -> tuple[int, int]:
    usage = body.get("usage")
    if not isinstance(usage, Mapping) and isinstance(body.get("data"), Mapping):
        usage = body["data"].get("usage")
    if not isinstance(usage, Mapping):
        return 0, 0

    in_tokens = 0
    for k in ("input_tokens", "prompt_tokens", "total_input_tokens", "prompt_eval_count"):
        val = usage.get(k)
        if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
            in_tokens = val
            break

    out_tokens = 0
    for k in ("output_tokens", "completion_tokens", "eval_count"):
        val = usage.get(k)
        if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
            out_tokens = val
            break

    return in_tokens, out_tokens


def _normalize_single_answer(
    q_key: str,
    wire_spec: Mapping[str, Any],
    raw_ans: Any,
) -> dict[str, Any]:
    q_type = str(wire_spec.get("type") or "").strip().lower()
    if q_type == "noul":
        p_true, conf = parse_noul_answer(raw_ans)
        return {
            "type": "noul",
            "noul": p_true,
            "p_true": p_true,
            "confidence": round(conf, 4),
        }
    if q_type == "score":
        criteria = wire_spec.get("criteria") or []
        probs_map, conf = parse_score_answer(raw_ans, criteria)
        return {
            "type": "score",
            "probabilities": probs_map,
            "confidence": conf,
        }
    if q_type == "choice":
        criteria = wire_spec.get("criteria") or {}
        probs_map, chosen, conf = parse_choice_answer(raw_ans, criteria)
        return {
            "type": "choice",
            "probabilities": probs_map,
            "chosen": chosen,
            "confidence": conf,
        }
    raise SystemOneUpstreamError(f"不支持的题目类型 ({q_key}): {q_type!r}", status_code=400)


@dataclass(slots=True)
class SystemOneBatchResult:
    answers: dict[str, dict[str, Any]]
    model: str
    endpoint_url: str
    waf_fallback_used: bool
    upstream_request_id: str | None
    latency_ms: int
    input_tokens: int
    output_tokens: int
    channel_used: str
    attempts: int


class SystemOneClient:
    """三端统一 System One 协议客户端 (TypeSafe / EMRank / Ollama tev1)。"""

    def __init__(
        self,
        provider: DecisionProvider,
        *,
        pinned_endpoint_url: str | None = None,
        pinned_model_name: str | None = None,
        settings: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.provider = provider
        self.settings = settings or get_settings()
        self.transport = transport
        self.endpoint_url = (pinned_endpoint_url or "").strip() or provider.endpoint_url.strip()
        self.model_name = (pinned_model_name or "").strip() or provider.default_model.strip()

    async def execute_batch(self, batch: CompiledBatchCall) -> SystemOneBatchResult:
        return await self.decide(state=batch.state, questions=batch.questions)

    async def decide(
        self,
        *,
        state: Any,
        questions: Mapping[str, Mapping[str, Any]],
    ) -> SystemOneBatchResult:
        if not questions:
            return SystemOneBatchResult(
                answers={},
                model=self.model_name,
                endpoint_url=self.endpoint_url,
                waf_fallback_used=False,
                upstream_request_id=None,
                latency_ms=0,
                input_tokens=0,
                output_tokens=0,
                channel_used="SKIPPED_EMPTY",
                attempts=0,
            )

        effective_network_mode = resolve_effective_network_mode(self.provider)
        api_key = resolve_provider_api_key(self.provider, settings=self.settings)

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = build_system_one_payload(
            model=self.model_name,
            state=state,
            questions=questions,
            provider_kind=self.provider.provider_kind,
            extra_options_json=self.provider.extra_options_json,
        )

        try:
            dispatch = await request_with_policy(
                "POST",
                self.endpoint_url,
                network_mode=effective_network_mode,
                custom_proxy_url=self.provider.custom_proxy_url,
                proxy_candidates=self.settings.proxy_candidates,
                timeout_seconds=float(self.provider.timeout_seconds or 90),
                headers=headers,
                json=payload,
                transport=self.transport,
            )
        except httpx.TransportError as exc:
            raise SystemOneUpstreamError(
                f"请求判别渠道网络异常: {exc}", status_code=502, code=502
            ) from exc

        resp = dispatch.response
        total_latency_ms = dispatch.elapsed_ms
        total_attempts = dispatch.attempts
        waf_fallback_used = False

        if (
            resp.status_code == 403
            and bool(self.provider.waf_strip_code_retry)
            and resp.text.lstrip().startswith("<")
        ):
            sanitized_state = strip_fenced_code_blocks(state)
            retry_payload = build_system_one_payload(
                model=self.model_name,
                state=sanitized_state,
                questions=questions,
                provider_kind=self.provider.provider_kind,
                extra_options_json=self.provider.extra_options_json,
            )
            try:
                retry_dispatch = await request_with_policy(
                    "POST",
                    self.endpoint_url,
                    network_mode=effective_network_mode,
                    custom_proxy_url=self.provider.custom_proxy_url,
                    proxy_candidates=self.settings.proxy_candidates,
                    timeout_seconds=float(self.provider.timeout_seconds or 90),
                    headers=headers,
                    json=retry_payload,
                    transport=self.transport,
                )
            except httpx.TransportError as exc:
                raise SystemOneUpstreamError(
                    f"WAF 重试请求网络异常: {exc}", status_code=502, code=502
                ) from exc
            resp = retry_dispatch.response
            total_latency_ms += retry_dispatch.elapsed_ms
            total_attempts += retry_dispatch.attempts
            dispatch = retry_dispatch
            waf_fallback_used = True

        upstream_req_id = (
            resp.headers.get("X-Request-ID")
            or resp.headers.get("x-request-id")
            or None
        )

        if resp.status_code < 200 or resp.status_code >= 300:
            raise SystemOneUpstreamError(
                f"上游判别接口返回 HTTP {resp.status_code}: {resp.text[:300]}",
                status_code=502,
                code=502,
            )

        try:
            body = resp.json()
        except ValueError as exc:
            raise SystemOneUpstreamError("上游判别接口返回非法 JSON", status_code=502, code=502) from exc

        if not isinstance(body, Mapping):
            raise SystemOneUpstreamError("上游判别响应顶层必须为 JSON 对象", status_code=502, code=502)

        if upstream_req_id is None:
            raw_id = body.get("request_id") or body.get("id")
            if isinstance(raw_id, str) and raw_id.strip():
                upstream_req_id = raw_id.strip()

        expected_keys = set(questions.keys())
        raw_answers_map = _extract_answers_dict(body, expected_keys)

        normalized_answers: dict[str, dict[str, Any]] = {}
        for q_key, wire_spec in questions.items():
            if q_key not in raw_answers_map:
                raise SystemOneUpstreamError(
                    f"上游响应缺少题目决策结果: {q_key}", status_code=502, code=502
                )
            try:
                normalized_answers[q_key] = _normalize_single_answer(
                    q_key, wire_spec, raw_answers_map[q_key]
                )
            except ValueError as exc:
                raise SystemOneUpstreamError(
                    f"解析题目 {q_key} 概率响应失败: {exc}", status_code=502, code=502
                ) from exc

        in_tokens, out_tokens = _extract_token_usage(body)
        return SystemOneBatchResult(
            answers=normalized_answers,
            model=self.model_name,
            endpoint_url=self.endpoint_url,
            waf_fallback_used=waf_fallback_used,
            upstream_request_id=upstream_req_id,
            latency_ms=total_latency_ms,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            channel_used=dispatch.channel_label,
            attempts=total_attempts,
        )
