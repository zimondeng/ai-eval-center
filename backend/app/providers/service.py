from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.common.crypto import encrypt_secret, mask_secret
from app.common.errors import AppError
from app.common.http_client import request_with_policy
from app.config import Settings, get_settings
from app.db.base import utcnow_naive
from app.db.models import DecisionProvider
from app.providers.client import (
    build_system_one_payload,
    normalize_models_payload,
    parse_noul_answer,
    resolve_effective_network_mode,
    resolve_provider_api_key,
)
from app.providers.schemas import ProviderUpdateRequest, ProviderUpsertRequest


def _fmt_dt(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _validate_http_url(url: str, field_name: str) -> str:
    cleaned = url.strip()
    if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
        raise AppError(f"{field_name} 必须以 http:// 或 https:// 开头", status_code=400)
    return cleaned


def serialize_provider(provider: DecisionProvider) -> dict[str, Any]:
    last_health_bool = (
        None if provider.last_health_ok is None else bool(provider.last_health_ok)
    )
    return {
        "id": provider.id,
        "uuid": provider.uuid,
        "provider_code": provider.provider_code,
        "provider_name": provider.provider_name,
        "provider_kind": provider.provider_kind,
        "endpoint_url": provider.endpoint_url,
        "models_list_url": provider.models_list_url,
        "default_model": provider.default_model,
        "auth_type": provider.auth_type,
        "api_key_env_name": provider.api_key_env_name,
        "api_key_masked": provider.api_key_masked,
        "has_api_key": bool(provider.api_key_cipher or provider.api_key_env_name),
        "network_mode": provider.network_mode,
        "custom_proxy_url": provider.custom_proxy_url,
        "timeout_seconds": provider.timeout_seconds,
        "max_concurrency": provider.max_concurrency,
        "max_questions_per_call": provider.max_questions_per_call,
        "max_options_per_question": provider.max_options_per_question,
        "waf_strip_code_retry": bool(provider.waf_strip_code_retry),
        "cost_per_1m_input_tokens": float(provider.cost_per_1m_input_tokens or 0.0),
        "extra_options_json": provider.extra_options_json,
        "is_default": bool(provider.is_default),
        "status": provider.status,
        "last_health_ok": last_health_bool,
        "last_checked_at": _fmt_dt(provider.last_checked_at),
        "created_by": provider.created_by,
        "create_time": _fmt_dt(provider.create_time),
        "update_time": _fmt_dt(provider.update_time),
    }


def list_providers(
    db: Session,
    *,
    status: str | None = None,
) -> list[dict[str, Any]]:
    stmt = select(DecisionProvider).where(DecisionProvider.is_deleted == 0)
    if status:
        stmt = stmt.where(DecisionProvider.status == status.strip().upper())
    stmt = stmt.order_by(DecisionProvider.is_default.desc(), DecisionProvider.id.asc())
    rows = db.scalars(stmt).all()
    return [serialize_provider(row) for row in rows]


def get_provider_by_code(db: Session, provider_code: str) -> DecisionProvider:
    cleaned = provider_code.strip()
    row = db.scalar(
        select(DecisionProvider).where(
            DecisionProvider.provider_code == cleaned,
            DecisionProvider.is_deleted == 0,
        )
    )
    if row is None:
        raise AppError(f"判别模型渠道不存在: {cleaned}", status_code=404, code=404)
    return row


def _apply_secret_configuration(
    row: DecisionProvider,
    *,
    auth_type: str,
    api_key_plain: str | None,
    api_key_env_name: str | None,
    env_name_supplied: bool,
    cfg: Settings,
) -> None:
    row.auth_type = auth_type
    if auth_type == "NONE":
        row.api_key_cipher = None
        row.api_key_masked = None
        row.api_key_env_name = None
        return

    if auth_type == "ENV_REF":
        effective_env = (
            (api_key_env_name or "").strip()
            if env_name_supplied
            else (row.api_key_env_name or "").strip()
        )
        if not effective_env:
            raise AppError("ENV_REF 鉴权模式必须提供 api_key_env_name", status_code=400)
        row.api_key_env_name = effective_env
        row.api_key_cipher = None
        row.api_key_masked = None
        return

    if auth_type == "BEARER":
        row.api_key_env_name = None
        cleaned_plain = (api_key_plain or "").strip()
        if cleaned_plain:
            row.api_key_cipher = encrypt_secret(
                cleaned_plain, master_key=cfg.master_encryption_key
            )
            row.api_key_masked = mask_secret(cleaned_plain)
        return

    raise AppError(f"不支持的鉴权类型: {auth_type}", status_code=400)


def _enforce_exclusive_default(db: Session, target_provider_code: str) -> None:
    db.execute(
        update(DecisionProvider)
        .where(
            DecisionProvider.provider_code != target_provider_code,
            DecisionProvider.is_deleted == 0,
        )
        .values(is_default=0)
    )


def upsert_provider(
    db: Session,
    payload: ProviderUpsertRequest,
    *,
    operator: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    code = payload.provider_code.strip()
    endpoint_url = _validate_http_url(payload.endpoint_url, "endpoint_url")
    models_list_url = (
        _validate_http_url(payload.models_list_url, "models_list_url")
        if payload.models_list_url and payload.models_list_url.strip()
        else None
    )

    effective_network_mode = payload.network_mode
    if effective_network_mode is None:
        effective_network_mode = (
            "DIRECT" if payload.provider_kind == "OLLAMA_LOCAL" else "PROXY_FALLBACK"
        )

    custom_proxy_url = (payload.custom_proxy_url or "").strip() or None
    if effective_network_mode == "CUSTOM_PROXY" and not custom_proxy_url:
        raise AppError("CUSTOM_PROXY 模式必须提供 custom_proxy_url", status_code=400)

    row = db.scalar(
        select(DecisionProvider).where(DecisionProvider.provider_code == code)
    )
    is_new = row is None

    effective_auth_type = payload.auth_type
    if effective_auth_type is None:
        if payload.api_key_plain and payload.api_key_plain.strip():
            effective_auth_type = "BEARER"
        elif payload.api_key_env_name and payload.api_key_env_name.strip():
            effective_auth_type = "ENV_REF"
        elif row is not None and row.auth_type:
            effective_auth_type = row.auth_type
        elif payload.provider_kind == "OLLAMA_LOCAL":
            effective_auth_type = "NONE"
        else:
            effective_auth_type = "BEARER"

    effective_waf_retry = payload.waf_strip_code_retry
    if effective_waf_retry is None:
        effective_waf_retry = False if payload.provider_kind == "OLLAMA_LOCAL" else True

    if row is None:
        row = DecisionProvider(
            provider_code=code,
            provider_name=payload.provider_name.strip(),
            provider_kind=payload.provider_kind,
            endpoint_url=endpoint_url,
            default_model=payload.default_model.strip(),
            created_by=operator,
            is_deleted=0,
        )
        db.add(row)
    else:
        row.is_deleted = 0
        row.provider_name = payload.provider_name.strip()
        row.provider_kind = payload.provider_kind
        row.endpoint_url = endpoint_url
        row.default_model = payload.default_model.strip()

    row.models_list_url = models_list_url
    row.network_mode = effective_network_mode
    row.custom_proxy_url = custom_proxy_url
    row.timeout_seconds = payload.timeout_seconds
    row.max_concurrency = payload.max_concurrency
    row.max_questions_per_call = payload.max_questions_per_call
    row.max_options_per_question = payload.max_options_per_question
    row.waf_strip_code_retry = 1 if effective_waf_retry else 0
    row.cost_per_1m_input_tokens = payload.cost_per_1m_input_tokens
    if is_new or "extra_options_json" in payload.model_fields_set:
        row.extra_options_json = payload.extra_options_json
    row.status = payload.status

    _apply_secret_configuration(
        row,
        auth_type=effective_auth_type,
        api_key_plain=payload.api_key_plain,
        api_key_env_name=payload.api_key_env_name,
        env_name_supplied=is_new or ("api_key_env_name" in payload.model_fields_set),
        cfg=cfg,
    )

    if payload.is_default:
        _enforce_exclusive_default(db, code)
        row.is_default = 1
    elif "is_default" in payload.model_fields_set or is_new:
        row.is_default = 0

    row.update_time = utcnow_naive()
    db.commit()
    db.refresh(row)
    return serialize_provider(row)


def update_provider(
    db: Session,
    provider_code: str,
    payload: ProviderUpdateRequest,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    code = provider_code.strip()
    if payload.provider_code is not None and payload.provider_code.strip() != code:
        raise AppError("请求体 provider_code 与路径参数不一致", status_code=400)

    row = get_provider_by_code(db, code)
    fields = payload.model_fields_set

    if "provider_name" in fields and payload.provider_name is not None:
        row.provider_name = payload.provider_name.strip()
    if "provider_kind" in fields and payload.provider_kind is not None:
        row.provider_kind = payload.provider_kind
        if "network_mode" not in fields and payload.provider_kind == "OLLAMA_LOCAL":
            row.network_mode = "DIRECT"
    if "endpoint_url" in fields and payload.endpoint_url is not None:
        row.endpoint_url = _validate_http_url(payload.endpoint_url, "endpoint_url")
    if "models_list_url" in fields:
        row.models_list_url = (
            _validate_http_url(payload.models_list_url, "models_list_url")
            if payload.models_list_url and payload.models_list_url.strip()
            else None
        )
    if "default_model" in fields and payload.default_model is not None:
        row.default_model = payload.default_model.strip()
    if "network_mode" in fields and payload.network_mode is not None:
        row.network_mode = payload.network_mode
    if "custom_proxy_url" in fields:
        row.custom_proxy_url = (payload.custom_proxy_url or "").strip() or None
    if row.network_mode == "CUSTOM_PROXY" and not row.custom_proxy_url:
        raise AppError("CUSTOM_PROXY 模式必须提供 custom_proxy_url", status_code=400)

    if "timeout_seconds" in fields and payload.timeout_seconds is not None:
        row.timeout_seconds = payload.timeout_seconds
    if "max_concurrency" in fields and payload.max_concurrency is not None:
        row.max_concurrency = payload.max_concurrency
    if "max_questions_per_call" in fields and payload.max_questions_per_call is not None:
        row.max_questions_per_call = payload.max_questions_per_call
    if "max_options_per_question" in fields and payload.max_options_per_question is not None:
        row.max_options_per_question = payload.max_options_per_question
    if "waf_strip_code_retry" in fields and payload.waf_strip_code_retry is not None:
        row.waf_strip_code_retry = 1 if payload.waf_strip_code_retry else 0
    if "cost_per_1m_input_tokens" in fields and payload.cost_per_1m_input_tokens is not None:
        row.cost_per_1m_input_tokens = payload.cost_per_1m_input_tokens
    if "extra_options_json" in fields:
        row.extra_options_json = payload.extra_options_json
    if "status" in fields and payload.status is not None:
        row.status = payload.status

    if {"auth_type", "api_key_plain", "api_key_env_name"} & fields:
        target_auth = payload.auth_type if payload.auth_type is not None else row.auth_type
        _apply_secret_configuration(
            row,
            auth_type=target_auth,
            api_key_plain=payload.api_key_plain,
            api_key_env_name=payload.api_key_env_name,
            env_name_supplied="api_key_env_name" in fields,
            cfg=cfg,
        )

    if "is_default" in fields and payload.is_default is not None:
        if payload.is_default:
            _enforce_exclusive_default(db, code)
            row.is_default = 1
        else:
            row.is_default = 0

    row.update_time = utcnow_naive()
    db.commit()
    db.refresh(row)
    return serialize_provider(row)


async def probe_provider_connectivity(
    db: Session,
    provider_code: str,
    *,
    model_override: str | None = None,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    row = get_provider_by_code(db, provider_code)
    target_model = (model_override or "").strip() or row.default_model
    effective_network_mode = resolve_effective_network_mode(row)

    payload = build_system_one_payload(
        model=target_model,
        state="探活测试",
        questions={
            "ping": {
                "type": "noul",
                "instructions": "该文本是否为中文？",
            }
        },
        provider_kind=row.provider_kind,
        extra_options_json=row.extra_options_json,
    )

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        api_key = resolve_provider_api_key(row, settings=cfg)
    except ValueError as exc:
        now = utcnow_naive()
        row.last_health_ok = 0
        row.last_checked_at = now
        row.update_time = now
        db.commit()
        return {
            "ok": False,
            "provider_code": row.provider_code,
            "provider_kind": row.provider_kind,
            "endpoint_url": row.endpoint_url,
            "model": target_model,
            "effective_network_mode": effective_network_mode,
            "channel_used": None,
            "used_proxy": None,
            "attempts": 0,
            "latency_ms": 0,
            "status_code": None,
            "upstream_request_id": None,
            "probability_sample": None,
            "usage": None,
            "last_health_ok": False,
            "last_checked_at": _fmt_dt(now),
            "error": str(exc),
        }

    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    t0 = time.perf_counter()
    try:
        dispatch = await request_with_policy(
            "POST",
            row.endpoint_url,
            network_mode=effective_network_mode,
            custom_proxy_url=row.custom_proxy_url,
            proxy_candidates=cfg.proxy_candidates,
            timeout_seconds=float(row.timeout_seconds or 30),
            headers=headers,
            json=payload,
            transport=transport,
        )
    except (httpx.TransportError, ValueError) as exc:
        elapsed_ms = int(round((time.perf_counter() - t0) * 1000))
        now = utcnow_naive()
        row.last_health_ok = 0
        row.last_checked_at = now
        row.update_time = now
        db.commit()
        return {
            "ok": False,
            "provider_code": row.provider_code,
            "provider_kind": row.provider_kind,
            "endpoint_url": row.endpoint_url,
            "model": target_model,
            "effective_network_mode": effective_network_mode,
            "channel_used": None,
            "used_proxy": None,
            "attempts": 0,
            "latency_ms": elapsed_ms,
            "status_code": None,
            "upstream_request_id": None,
            "probability_sample": None,
            "usage": None,
            "last_health_ok": False,
            "last_checked_at": _fmt_dt(now),
            "error": f"网络连接失败: {exc}",
        }

    resp = dispatch.response
    upstream_req_id = (
        resp.headers.get("X-Request-ID")
        or resp.headers.get("x-request-id")
        or None
    )

    if resp.status_code < 200 or resp.status_code >= 300:
        now = utcnow_naive()
        row.last_health_ok = 0
        row.last_checked_at = now
        row.update_time = now
        db.commit()
        snippet = resp.text[:300]
        return {
            "ok": False,
            "provider_code": row.provider_code,
            "provider_kind": row.provider_kind,
            "endpoint_url": row.endpoint_url,
            "model": target_model,
            "effective_network_mode": effective_network_mode,
            "channel_used": dispatch.channel_label,
            "used_proxy": dispatch.used_proxy,
            "attempts": dispatch.attempts,
            "latency_ms": dispatch.elapsed_ms,
            "status_code": resp.status_code,
            "upstream_request_id": upstream_req_id,
            "probability_sample": None,
            "usage": None,
            "last_health_ok": False,
            "last_checked_at": _fmt_dt(now),
            "error": f"上游返回 HTTP {resp.status_code}: {snippet}",
        }

    try:
        body = resp.json()
        if not isinstance(body, Mapping):
            raise ValueError("上游响应不是 JSON 对象")
        if upstream_req_id is None:
            raw_id = body.get("request_id") or body.get("id")
            if isinstance(raw_id, str) and raw_id.strip():
                upstream_req_id = raw_id.strip()
        answers = body.get("answers")
        if not isinstance(answers, Mapping) or "ping" not in answers:
            raise ValueError("上游响应缺少 answers.ping 决策字段")
        p_true, confidence = parse_noul_answer(answers["ping"])
        usage = body.get("usage") if isinstance(body.get("usage"), Mapping) else None
    except ValueError as exc:
        now = utcnow_naive()
        row.last_health_ok = 0
        row.last_checked_at = now
        row.update_time = now
        db.commit()
        return {
            "ok": False,
            "provider_code": row.provider_code,
            "provider_kind": row.provider_kind,
            "endpoint_url": row.endpoint_url,
            "model": target_model,
            "effective_network_mode": effective_network_mode,
            "channel_used": dispatch.channel_label,
            "used_proxy": dispatch.used_proxy,
            "attempts": dispatch.attempts,
            "latency_ms": dispatch.elapsed_ms,
            "status_code": resp.status_code,
            "upstream_request_id": upstream_req_id,
            "probability_sample": None,
            "usage": None,
            "last_health_ok": False,
            "last_checked_at": _fmt_dt(now),
            "error": f"解析 System One 概率响应失败: {exc}",
        }

    now = utcnow_naive()
    row.last_health_ok = 1
    row.last_checked_at = now
    row.update_time = now
    db.commit()

    return {
        "ok": True,
        "provider_code": row.provider_code,
        "provider_kind": row.provider_kind,
        "endpoint_url": row.endpoint_url,
        "model": target_model,
        "effective_network_mode": effective_network_mode,
        "channel_used": dispatch.channel_label,
        "used_proxy": dispatch.used_proxy,
        "attempts": dispatch.attempts,
        "latency_ms": dispatch.elapsed_ms,
        "status_code": resp.status_code,
        "upstream_request_id": upstream_req_id,
        "probability_sample": {
            "question_key": "ping",
            "p_true": p_true,
            "confidence": confidence,
        },
        "usage": dict(usage) if usage is not None else None,
        "last_health_ok": True,
        "last_checked_at": _fmt_dt(now),
        "error": None,
    }


async def fetch_provider_models(
    db: Session,
    provider_code: str,
    *,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    row = get_provider_by_code(db, provider_code)
    models_url = (row.models_list_url or "").strip()
    if not models_url:
        fallback_models = [row.default_model] if row.default_model else []
        return {
            "provider_code": row.provider_code,
            "models_list_url": None,
            "source": "configured_default",
            "channel_used": None,
            "latency_ms": 0,
            "models": fallback_models,
        }

    effective_network_mode = resolve_effective_network_mode(row)
    headers: dict[str, str] = {"Accept": "application/json"}
    try:
        api_key = resolve_provider_api_key(row, settings=cfg)
    except ValueError as exc:
        raise AppError(f"解析渠道密钥失败: {exc}", status_code=400) from exc

    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        dispatch = await request_with_policy(
            "GET",
            models_url,
            network_mode=effective_network_mode,
            custom_proxy_url=row.custom_proxy_url,
            proxy_candidates=cfg.proxy_candidates,
            timeout_seconds=float(row.timeout_seconds or 30),
            headers=headers,
            transport=transport,
        )
    except (httpx.TransportError, ValueError) as exc:
        raise AppError(f"拉取上游模型列表失败: {exc}", status_code=502, code=502) from exc

    resp = dispatch.response
    if resp.status_code < 200 or resp.status_code >= 300:
        raise AppError(
            f"上游模型列表接口返回 HTTP {resp.status_code}: {resp.text[:200]}",
            status_code=502,
            code=502,
        )

    try:
        raw_payload = resp.json()
    except ValueError as exc:
        raise AppError("上游模型列表接口返回非法 JSON", status_code=502, code=502) from exc

    models = normalize_models_payload(raw_payload)
    return {
        "provider_code": row.provider_code,
        "models_list_url": models_url,
        "source": "upstream",
        "channel_used": dispatch.channel_label,
        "latency_ms": dispatch.elapsed_ms,
        "models": models,
    }


__all__ = [
    "fetch_provider_models",
    "get_provider_by_code",
    "list_providers",
    "probe_provider_connectivity",
    "resolve_provider_api_key",
    "serialize_provider",
    "update_provider",
    "upsert_provider",
]
