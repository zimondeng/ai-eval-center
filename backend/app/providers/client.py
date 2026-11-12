from __future__ import annotations

import math
import os
from collections.abc import Mapping, Sequence
from typing import Any

from app.common.crypto import decrypt_secret
from app.config import Settings, get_settings
from app.db.models import DecisionProvider


def resolve_provider_api_key(
    provider: DecisionProvider,
    settings: Settings | None = None,
) -> str | None:
    cfg = settings or get_settings()
    auth_type = (provider.auth_type or "NONE").upper()
    if auth_type == "NONE":
        return None
    if auth_type == "BEARER":
        cipher = (provider.api_key_cipher or "").strip()
        if not cipher:
            return None
        decrypted = decrypt_secret(cipher, master_key=cfg.master_encryption_key).strip()
        return decrypted or None
    if auth_type == "ENV_REF":
        env_name = (provider.api_key_env_name or "").strip()
        if not env_name:
            return None
        env_val = os.getenv(env_name, "").strip()
        return env_val or None
    raise ValueError(f"不支持的渠道鉴权类型: {provider.auth_type}")


def resolve_effective_network_mode(provider: DecisionProvider) -> str:
    if provider.provider_kind == "OLLAMA_LOCAL" or provider.network_mode == "DIRECT":
        return "DIRECT"
    return provider.network_mode or "PROXY_FALLBACK"


def build_system_one_payload(
    model: str,
    state: Any,
    questions: Mapping[str, Any],
    *,
    provider_kind: str | None = None,
    extra_options_json: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "state": state,
        "questions": dict(questions),
    }
    if provider_kind == "OLLAMA_LOCAL" and isinstance(extra_options_json, Mapping):
        for key, val in extra_options_json.items():
            if key not in ("model", "state", "questions"):
                payload[key] = val
    return payload


def _is_real_number(val: Any) -> bool:
    return (
        isinstance(val, (int, float))
        and not isinstance(val, bool)
        and math.isfinite(float(val))
    )


def compute_normalized_confidence(
    probabilities: Mapping[str, float] | Sequence[float],
    upstream_confidence: float | None = None,
) -> float:
    if upstream_confidence is not None and _is_real_number(upstream_confidence):
        c_val = float(upstream_confidence)
        if -1e-6 <= c_val <= 1.0 + 1e-6:
            return round(max(0.0, min(1.0, c_val)), 6)

    probs = (
        [float(v) for v in probabilities.values()]
        if isinstance(probabilities, Mapping)
        else [float(v) for v in probabilities]
    )
    n = len(probs)
    if n == 0:
        return 0.0
    if n == 1:
        return 1.0
    p_max = max(probs)
    raw = (n * p_max - 1.0) / (n - 1.0)
    return round(max(0.0, min(1.0, raw)), 6)


def parse_noul_answer(raw_answer: Any) -> tuple[float, float]:
    p_true: float | None = None
    upstream_conf: float | None = None

    if _is_real_number(raw_answer):
        p_true = float(raw_answer)
    elif isinstance(raw_answer, Mapping):
        if _is_real_number(raw_answer.get("confidence")):
            upstream_conf = float(raw_answer["confidence"])
        for candidate_key in ("noul", "p_true", "probability", "prob", "true", "score", "value"):
            if candidate_key in raw_answer and _is_real_number(raw_answer[candidate_key]):
                p_true = float(raw_answer[candidate_key])
                break
        if p_true is None and isinstance(raw_answer.get("probabilities"), Mapping):
            probs_map = raw_answer["probabilities"]
            for true_key in ("true", "True", "1", "yes", "YES"):
                if true_key in probs_map and _is_real_number(probs_map[true_key]):
                    p_true = float(probs_map[true_key])
                    break

    if p_true is None or not (-1e-6 <= p_true <= 1.0 + 1e-6):
        raise ValueError(f"非法的 noul 概率响应: {raw_answer!r}")

    clamped_p = round(max(0.0, min(1.0, p_true)), 6)
    confidence = compute_normalized_confidence(
        [clamped_p, 1.0 - clamped_p],
        upstream_confidence=upstream_conf,
    )
    return clamped_p, confidence


def normalize_models_payload(payload: Any) -> list[str]:
    candidates: Sequence[Any] = ()
    if isinstance(payload, Mapping):
        if isinstance(payload.get("data"), Sequence) and not isinstance(
            payload.get("data"), (str, bytes)
        ):
            candidates = payload["data"]
        elif isinstance(payload.get("models"), Sequence) and not isinstance(
            payload.get("models"), (str, bytes)
        ):
            candidates = payload["models"]
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        candidates = payload

    seen: set[str] = set()
    normalized: list[str] = []
    for item in candidates:
        model_id: str | None = None
        if isinstance(item, str):
            model_id = item.strip()
        elif isinstance(item, Mapping):
            for key in ("id", "name", "model"):
                val = item.get(key)
                if isinstance(val, str) and val.strip():
                    model_id = val.strip()
                    break
        if model_id and model_id not in seen:
            seen.add(model_id)
            normalized.append(model_id)
    return normalized

