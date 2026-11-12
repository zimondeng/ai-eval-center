from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from app.common.http_client import request_with_policy
from app.config import Settings, get_settings

_TAG_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_:-]{0,63}$")
_FORBIDDEN_SUMMARY_KEYS = frozenset(
    {
        "TOTAL",
        "TOTAL_HITS",
        "TOTAL_COUNT",
        "RAW",
        "RAW_TEXT",
        "ORIGINAL",
        "PLAINTEXT",
        "TEXT",
        "VALUE",
        "MASKED_TEXT",
        "ID",
    }
)


class DesensitizationError(Exception):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class DesensitizedItem(BaseModel):
    item_id: str
    masked_text: str
    tag_counts: dict[str, int] = Field(default_factory=dict)


class DesensitizeBatchResult(BaseModel):
    policy_snapshot: str
    items: list[DesensitizedItem] = Field(default_factory=list)


def _normalize_tag_name(raw_tag: Any) -> str | None:
    if not isinstance(raw_tag, str):
        return None
    cleaned = raw_tag.strip()
    if not cleaned or not _TAG_KEY_RE.match(cleaned):
        return None
    upper = cleaned.upper()
    if upper in _FORBIDDEN_SUMMARY_KEYS:
        return None
    return upper


def sanitize_desensitize_summary(raw: Any) -> dict[str, int]:
    """Extract strictly integer tag hit counts and discard any string/PII values."""
    if raw is None:
        return {}

    counts: dict[str, int] = {}

    if isinstance(raw, Mapping):
        nested_counts = (
            raw.get("tag_counts")
            if isinstance(raw.get("tag_counts"), Mapping)
            else (
                raw.get("counts")
                if isinstance(raw.get("counts"), Mapping)
                else (
                    raw.get("summary")
                    if isinstance(raw.get("summary"), Mapping)
                    else None
                )
            )
        )
        target_map = nested_counts if nested_counts is not None else raw
        for k, v in target_map.items():
            tag = _normalize_tag_name(k)
            if tag is None:
                continue
            if isinstance(v, bool):
                continue
            if isinstance(v, int) and v > 0:
                counts[tag] = counts.get(tag, 0) + v

        entities = raw.get("entities") if isinstance(raw.get("entities"), Sequence) else None
        if entities is None and isinstance(raw.get("hits"), Sequence):
            entities = raw.get("hits")
        if entities is not None and not isinstance(entities, (str, bytes)):
            for ent in entities:
                if isinstance(ent, Mapping):
                    ent_tag = _normalize_tag_name(
                        ent.get("tag") or ent.get("type") or ent.get("label")
                    )
                    if ent_tag is not None:
                        ent_cnt = ent.get("count", 1)
                        inc = (
                            ent_cnt
                            if isinstance(ent_cnt, int) and not isinstance(ent_cnt, bool)
                            else 1
                        )
                        if inc > 0:
                            counts[ent_tag] = counts.get(ent_tag, 0) + inc
        return counts

    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        for ent in raw:
            if isinstance(ent, Mapping):
                ent_tag = _normalize_tag_name(
                    ent.get("tag") or ent.get("type") or ent.get("label")
                )
                if ent_tag is not None:
                    ent_cnt = ent.get("count", 1)
                    inc = (
                        ent_cnt
                        if isinstance(ent_cnt, int) and not isinstance(ent_cnt, bool)
                        else 1
                    )
                    if inc > 0:
                        counts[ent_tag] = counts.get(ent_tag, 0) + inc
        return counts

    return {}


def merge_tag_counts(*summaries: Mapping[str, int] | None) -> dict[str, int]:
    merged: dict[str, int] = {}
    for summary in summaries:
        if not summary:
            continue
        for tag, cnt in summary.items():
            norm = _normalize_tag_name(tag)
            if norm and isinstance(cnt, int) and not isinstance(cnt, bool) and cnt > 0:
                merged[norm] = merged.get(norm, 0) + cnt
    return merged


def _resolve_default_network_mode(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host in {"127.0.0.1", "localhost", "::1"}:
        return "DIRECT"
    return "PROXY_FALLBACK"


class DesensitizationClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        network_mode: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        cfg = settings or get_settings()
        raw_base = (base_url if base_url is not None else cfg.desensitize_base_url).strip()
        if not raw_base:
            raise DesensitizationError("未配置数据脱敏中心地址 (desensitize_base_url)", status_code=500)
        if raw_base.endswith("/api/v1/mask/batch-inline"):
            self.endpoint_url = raw_base
        else:
            self.endpoint_url = f"{raw_base.rstrip('/')}/api/v1/mask/batch-inline"
        self.api_key = (
            api_key if api_key is not None else cfg.desensitize_api_key
        ).strip()
        self.timeout_seconds = timeout_seconds
        self.network_mode = network_mode or _resolve_default_network_mode(self.endpoint_url)

    async def mask_batch(
        self,
        texts: Sequence[str],
        *,
        policy_code: str = "gov_eval_corpus",
        item_ids: Sequence[str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> DesensitizeBatchResult:
        cleaned_policy = (policy_code or "").strip() or "gov_eval_corpus"
        if not texts:
            return DesensitizeBatchResult(
                policy_snapshot=f"{cleaned_policy}:v1",
                items=[],
            )
        if item_ids is not None and len(item_ids) != len(texts):
            raise ValueError("item_ids 长度必须与 texts 一致")

        req_items = [
            {
                "id": str(item_ids[idx]) if item_ids is not None else str(idx),
                "text": str(text),
            }
            for idx, text in enumerate(texts)
        ]
        headers: dict[str, str] = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        try:
            dispatch = await request_with_policy(
                "POST",
                self.endpoint_url,
                network_mode=self.network_mode,
                timeout_seconds=self.timeout_seconds,
                headers=headers,
                json={
                    "policy_code": cleaned_policy,
                    "items": req_items,
                },
                transport=transport,
            )
        except httpx.HTTPError as exc:
            raise DesensitizationError(
                f"调用数据脱敏中心网络异常: {exc}",
                status_code=502,
            ) from exc

        resp = dispatch.response
        if resp.status_code >= 400:
            snippet = resp.text[:300]
            raise DesensitizationError(
                f"数据脱敏中心返回异常 HTTP {resp.status_code}: {snippet}",
                status_code=502,
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise DesensitizationError(
                "数据脱敏中心返回非合法 JSON 载荷",
                status_code=502,
            ) from exc

        if not isinstance(payload, Mapping):
            raise DesensitizationError(
                "数据脱敏中心响应必须为 JSON 对象",
                status_code=502,
            )

        if "code" in payload and payload.get("code") != 200:
            msg = str(payload.get("message") or "上游脱敏失败")
            raise DesensitizationError(
                f"数据脱敏中心返回业务错误(code={payload.get('code')}): {msg}",
                status_code=502,
            )

        data_node = payload.get("data")
        root: Mapping[str, Any] = data_node if isinstance(data_node, Mapping) else payload

        raw_snapshot = (
            root.get("policy_snapshot")
            or root.get("policy_version_snapshot")
            or payload.get("policy_snapshot")
        )
        if isinstance(raw_snapshot, str) and raw_snapshot.strip():
            policy_snapshot = raw_snapshot.strip()[:96]
        else:
            ver = root.get("policy_version") or root.get("version") or "v1"
            ver_str = str(ver).strip()
            if ver_str and not ver_str.startswith("v") and ver_str.isdigit():
                ver_str = f"v{ver_str}"
            policy_snapshot = f"{cleaned_policy}:{ver_str or 'v1'}"[:96]

        raw_items = root.get("items")
        if raw_items is None:
            raw_items = root.get("results")
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
            raise DesensitizationError(
                "数据脱敏中心响应缺少 items 数组",
                status_code=502,
            )
        if len(raw_items) != len(req_items):
            raise DesensitizationError(
                f"数据脱敏中心返回条目数({len(raw_items)})与请求条目数({len(req_items)})不一致",
                status_code=502,
            )

        by_id: dict[str, Mapping[str, Any]] = {}
        positional: list[Mapping[str, Any]] = []
        for idx, entry in enumerate(raw_items):
            if not isinstance(entry, Mapping):
                raise DesensitizationError(
                    f"数据脱敏中心第 {idx} 条结果格式非法",
                    status_code=502,
                )
            positional.append(entry)
            if entry.get("id") is not None:
                by_id[str(entry.get("id"))] = entry

        parsed_items: list[DesensitizedItem] = []
        for idx, req_entry in enumerate(req_items):
            req_id = req_entry["id"]
            matched = by_id.get(req_id, positional[idx])
            if matched.get("success") is False or matched.get("masked") is False:
                raise DesensitizationError(
                    f"数据脱敏中心标记条目 {req_id} 脱敏失败",
                    status_code=502,
                )
            masked_text = matched.get("masked_text")
            if not isinstance(masked_text, str):
                raise DesensitizationError(
                    f"数据脱敏中心条目 {req_id} 缺少合法的 masked_text 字符串",
                    status_code=502,
                )

            summary_source: Any = matched
            if (
                "summary" in matched
                and "tag_counts" not in matched
                and "entities" not in matched
                and "hits" not in matched
            ):
                summary_source = matched["summary"]
            tag_counts = sanitize_desensitize_summary(summary_source)
            parsed_items.append(
                DesensitizedItem(
                    item_id=req_id,
                    masked_text=masked_text,
                    tag_counts=tag_counts,
                )
            )

        return DesensitizeBatchResult(
            policy_snapshot=policy_snapshot,
            items=parsed_items,
        )
