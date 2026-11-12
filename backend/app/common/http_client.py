from __future__ import annotations

import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings


@dataclass(slots=True)
class HttpDispatchResult:
    response: httpx.Response
    used_proxy: str | None
    channel_label: str
    attempts: int
    elapsed_ms: int


def resolve_proxy_chain(
    network_mode: str = "PROXY_FALLBACK",
    custom_proxy_url: str | None = None,
    proxy_candidates: Sequence[str] | None = None,
) -> list[str | None]:
    mode = (network_mode or "PROXY_FALLBACK").upper()
    if mode == "DIRECT":
        return [None]
    if mode == "CUSTOM_PROXY":
        cleaned = (custom_proxy_url or "").strip()
        if not cleaned:
            raise ValueError("CUSTOM_PROXY 模式必须提供非空 custom_proxy_url")
        return [cleaned]
    if mode == "PROXY_FALLBACK":
        candidates = (
            list(proxy_candidates)
            if proxy_candidates is not None
            else list(get_settings().proxy_candidates)
        )
        chain: list[str | None] = [c.strip() for c in candidates if c and c.strip()]
        chain.append(None)
        return chain
    raise ValueError(f"不支持的 network_mode: {network_mode}")


async def request_with_policy(
    method: str,
    url: str,
    *,
    network_mode: str = "PROXY_FALLBACK",
    custom_proxy_url: str | None = None,
    proxy_candidates: Sequence[str] | None = None,
    timeout_seconds: float = 60.0,
    headers: Mapping[str, str] | None = None,
    json: Any = None,
    content: bytes | str | None = None,
    params: Mapping[str, Any] | None = None,
    follow_redirects: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> HttpDispatchResult:
    t0 = time.perf_counter()
    req_kwargs: dict[str, Any] = {}
    if headers is not None:
        req_kwargs["headers"] = dict(headers)
    if json is not None:
        req_kwargs["json"] = json
    if content is not None:
        req_kwargs["content"] = content
    if params is not None:
        req_kwargs["params"] = dict(params)

    if transport is not None:
        async with httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            timeout=timeout_seconds,
            follow_redirects=follow_redirects,
        ) as client:
            resp = await client.request(method, url, **req_kwargs)
        elapsed_ms = int(round((time.perf_counter() - t0) * 1000))
        return HttpDispatchResult(
            response=resp,
            used_proxy=None,
            channel_label="MOCK_TRANSPORT",
            attempts=1,
            elapsed_ms=elapsed_ms,
        )

    chain = resolve_proxy_chain(
        network_mode=network_mode,
        custom_proxy_url=custom_proxy_url,
        proxy_candidates=proxy_candidates,
    )
    last_exc: httpx.TransportError | None = None
    attempts = 0
    for proxy_url in chain:
        attempts += 1
        try:
            async with httpx.AsyncClient(
                proxy=proxy_url,
                trust_env=False,
                timeout=timeout_seconds,
                follow_redirects=follow_redirects,
            ) as client:
                resp = await client.request(method, url, **req_kwargs)
            elapsed_ms = int(round((time.perf_counter() - t0) * 1000))
            label = "DIRECT" if proxy_url is None else f"PROXY:{proxy_url}"
            return HttpDispatchResult(
                response=resp,
                used_proxy=proxy_url,
                channel_label=label,
                attempts=attempts,
                elapsed_ms=elapsed_ms,
            )
        except httpx.TransportError as exc:
            last_exc = exc

    assert last_exc is not None
    raise last_exc


@asynccontextmanager
async def stream_with_policy(
    method: str,
    url: str,
    *,
    network_mode: str = "PROXY_FALLBACK",
    custom_proxy_url: str | None = None,
    proxy_candidates: Sequence[str] | None = None,
    timeout_seconds: float = 60.0,
    headers: Mapping[str, str] | None = None,
    json: Any = None,
    content: bytes | str | None = None,
    params: Mapping[str, Any] | None = None,
    follow_redirects: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[tuple[httpx.Response, str | None]]:
    req_kwargs: dict[str, Any] = {}
    if headers is not None:
        req_kwargs["headers"] = dict(headers)
    if json is not None:
        req_kwargs["json"] = json
    if content is not None:
        req_kwargs["content"] = content
    if params is not None:
        req_kwargs["params"] = dict(params)

    if transport is not None:
        client = httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            timeout=timeout_seconds,
            follow_redirects=follow_redirects,
        )
        req = client.build_request(method, url, **req_kwargs)
        resp = await client.send(req, stream=True)
        try:
            yield resp, None
        finally:
            await resp.aclose()
            await client.aclose()
        return

    chain = resolve_proxy_chain(
        network_mode=network_mode,
        custom_proxy_url=custom_proxy_url,
        proxy_candidates=proxy_candidates,
    )
    last_exc: httpx.TransportError | None = None
    for proxy_url in chain:
        client = httpx.AsyncClient(
            proxy=proxy_url,
            trust_env=False,
            timeout=timeout_seconds,
            follow_redirects=follow_redirects,
        )
        try:
            req = client.build_request(method, url, **req_kwargs)
            resp = await client.send(req, stream=True)
        except httpx.TransportError as exc:
            await client.aclose()
            last_exc = exc
            continue

        try:
            yield resp, proxy_url
        finally:
            await resp.aclose()
            await client.aclose()
        return

    assert last_exc is not None
    raise last_exc


dispatch_http_request = request_with_policy
open_http_stream = stream_with_policy
