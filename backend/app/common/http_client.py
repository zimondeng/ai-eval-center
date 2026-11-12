from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
import threading
import time
from typing import Any

import httpx

from app.config import get_settings

DEFAULT_PROXY_CACHE_TTL_SECONDS = 300.0
_UNSET = object()


@dataclass(slots=True)
class HttpDispatchResult:
    response: httpx.Response
    used_proxy: str | None
    channel_label: str
    attempts: int
    elapsed_ms: int


@dataclass(slots=True)
class _CachedProxyEntry:
    proxy_url: str | None
    expires_at: float


_pool_lock = threading.Lock()
_client_pools: dict[tuple[str | None, bool], tuple[int | None, httpx.AsyncClient]] = {}
_proxy_cache: dict[tuple[str | None, ...], _CachedProxyEntry] = {}


def _current_loop_id() -> int | None:
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return None


def get_pooled_async_client(
    proxy_url: str | None,
    follow_redirects: bool = False,
) -> httpx.AsyncClient:
    key = (proxy_url, bool(follow_redirects))
    cur_loop_id = _current_loop_id()
    with _pool_lock:
        existing = _client_pools.get(key)
        if existing is not None:
            stored_loop_id, client = existing
            same_loop = (
                cur_loop_id is None
                or stored_loop_id is None
                or stored_loop_id == cur_loop_id
            )
            if not client.is_closed and same_loop:
                if stored_loop_id is None and cur_loop_id is not None:
                    _client_pools[key] = (cur_loop_id, client)
                return client

        client = httpx.AsyncClient(
            proxy=proxy_url,
            trust_env=False,
            follow_redirects=bool(follow_redirects),
        )
        _client_pools[key] = (cur_loop_id, client)
        return client


async def aclose_http_client_pools() -> None:
    cur_loop_id = _current_loop_id()
    with _pool_lock:
        entries = list(_client_pools.values())
        _client_pools.clear()
        _proxy_cache.clear()

    for stored_loop_id, client in entries:
        if client.is_closed:
            continue
        if stored_loop_id is None or stored_loop_id == cur_loop_id:
            with suppress(RuntimeError):
                await client.aclose()


def reset_http_client_pools() -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    cur_loop_id = id(loop) if loop is not None else None

    with _pool_lock:
        entries = list(_client_pools.values())
        _client_pools.clear()
        _proxy_cache.clear()

    if loop is not None:
        for stored_loop_id, client in entries:
            if not client.is_closed and (
                stored_loop_id is None or stored_loop_id == cur_loop_id
            ):
                with suppress(RuntimeError):
                    loop.create_task(client.aclose())


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


def _prepare_candidate_sequence(
    network_mode: str,
    chain: Sequence[str | None],
) -> tuple[list[str | None], tuple[str | None, ...] | None, object]:
    mode = (network_mode or "PROXY_FALLBACK").upper()
    if mode != "PROXY_FALLBACK" or len(chain) <= 1:
        return list(chain), None, _UNSET

    cache_key = tuple(chain)
    now_mono = time.monotonic()
    with _pool_lock:
        cached = _proxy_cache.get(cache_key)
        if cached is not None:
            if now_mono < cached.expires_at and cached.proxy_url in chain:
                ordered = [cached.proxy_url] + [
                    p for p in chain if p != cached.proxy_url
                ]
                return ordered, cache_key, cached.proxy_url
            _proxy_cache.pop(cache_key, None)
    return list(chain), cache_key, _UNSET


def _record_cached_proxy(
    cache_key: tuple[str | None, ...] | None,
    proxy_url: str | None,
    *,
    ttl_seconds: float = DEFAULT_PROXY_CACHE_TTL_SECONDS,
) -> None:
    if cache_key is None:
        return
    with _pool_lock:
        _proxy_cache[cache_key] = _CachedProxyEntry(
            proxy_url=proxy_url,
            expires_at=time.monotonic() + ttl_seconds,
        )


def _invalidate_cached_proxy(
    cache_key: tuple[str | None, ...] | None,
    failed_proxy: str | None,
) -> None:
    if cache_key is None:
        return
    with _pool_lock:
        cur = _proxy_cache.get(cache_key)
        if cur is not None and cur.proxy_url == failed_proxy:
            _proxy_cache.pop(cache_key, None)


def _build_request_kwargs(
    *,
    headers: Mapping[str, str] | None = None,
    json: Any = None,
    content: bytes | str | None = None,
    params: Mapping[str, Any] | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    req_kwargs: dict[str, Any] = {}
    if headers is not None:
        req_kwargs["headers"] = dict(headers)
    if json is not None:
        req_kwargs["json"] = json
    if content is not None:
        req_kwargs["content"] = content
    if params is not None:
        req_kwargs["params"] = dict(params)
    if timeout_seconds is not None:
        req_kwargs["timeout"] = timeout_seconds
    return req_kwargs


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

    if transport is not None:
        isolated_kwargs = _build_request_kwargs(
            headers=headers,
            json=json,
            content=content,
            params=params,
        )
        async with httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            timeout=timeout_seconds,
            follow_redirects=follow_redirects,
        ) as client:
            resp = await client.request(method, url, **isolated_kwargs)
        elapsed_ms = int(round((time.perf_counter() - t0) * 1000))
        return HttpDispatchResult(
            response=resp,
            used_proxy=None,
            channel_label="MOCK_TRANSPORT",
            attempts=1,
            elapsed_ms=elapsed_ms,
        )

    pooled_kwargs = _build_request_kwargs(
        headers=headers,
        json=json,
        content=content,
        params=params,
        timeout_seconds=timeout_seconds,
    )
    chain = resolve_proxy_chain(
        network_mode=network_mode,
        custom_proxy_url=custom_proxy_url,
        proxy_candidates=proxy_candidates,
    )
    ordered_chain, cache_key, cached_hit = _prepare_candidate_sequence(
        network_mode, chain
    )

    last_exc: httpx.TransportError | None = None
    attempts = 0
    for idx, proxy_url in enumerate(ordered_chain):
        attempts += 1
        client = get_pooled_async_client(proxy_url, follow_redirects=follow_redirects)
        try:
            resp = await client.request(method, url, **pooled_kwargs)
            _record_cached_proxy(cache_key, proxy_url)
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
            if idx == 0 and cached_hit is not _UNSET and proxy_url == cached_hit:
                _invalidate_cached_proxy(cache_key, proxy_url)
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
    if transport is not None:
        isolated_kwargs = _build_request_kwargs(
            headers=headers,
            json=json,
            content=content,
            params=params,
        )
        client = httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            timeout=timeout_seconds,
            follow_redirects=follow_redirects,
        )
        req = client.build_request(method, url, **isolated_kwargs)
        resp = await client.send(req, stream=True)
        try:
            yield resp, None
        finally:
            await resp.aclose()
            await client.aclose()
        return

    pooled_kwargs = _build_request_kwargs(
        headers=headers,
        json=json,
        content=content,
        params=params,
        timeout_seconds=timeout_seconds,
    )
    chain = resolve_proxy_chain(
        network_mode=network_mode,
        custom_proxy_url=custom_proxy_url,
        proxy_candidates=proxy_candidates,
    )
    ordered_chain, cache_key, cached_hit = _prepare_candidate_sequence(
        network_mode, chain
    )

    last_exc: httpx.TransportError | None = None
    for idx, proxy_url in enumerate(ordered_chain):
        client = get_pooled_async_client(proxy_url, follow_redirects=follow_redirects)
        try:
            req = client.build_request(method, url, **pooled_kwargs)
            resp = await client.send(req, stream=True)
        except httpx.TransportError as exc:
            if idx == 0 and cached_hit is not _UNSET and proxy_url == cached_hit:
                _invalidate_cached_proxy(cache_key, proxy_url)
            last_exc = exc
            continue

        _record_cached_proxy(cache_key, proxy_url)
        try:
            yield resp, proxy_url
        finally:
            await resp.aclose()
        return

    assert last_exc is not None
    raise last_exc


dispatch_http_request = request_with_policy
open_http_stream = stream_with_policy
