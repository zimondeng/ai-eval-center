from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.errors import AppError
from app.config import Settings
from app.db.models import AdminUser


def test_authenticate_admin_user_verifies_bcrypt_and_rejects_disabled_or_deleted(
    db_session: Session,
    test_settings: Settings,
) -> None:
    from app.auth.service import authenticate_admin_user

    result = authenticate_admin_user(
        db_session,
        username="admin",
        password=test_settings.default_admin_password,
        settings=test_settings,
    )
    assert result["token_type"] == "Bearer"
    assert result["expires_in"] == test_settings.jwt_expire_minutes * 60
    assert isinstance(result["access_token"], str) and len(result["access_token"]) > 20
    assert result["user"]["username"] == "admin"
    assert result["user"]["role"] == "ADMIN"
    assert result["user"]["last_login_at"] is not None

    with pytest.raises(AppError) as bad_pw_exc:
        authenticate_admin_user(
            db_session,
            username="admin",
            password="wrong-password",
            settings=test_settings,
        )
    assert bad_pw_exc.value.status_code == 401

    admin_row = db_session.scalar(select(AdminUser).where(AdminUser.username == "admin"))
    assert admin_row is not None
    admin_row.is_enabled = 0
    db_session.commit()

    with pytest.raises(AppError) as disabled_exc:
        authenticate_admin_user(
            db_session,
            username="admin",
            password=test_settings.default_admin_password,
            settings=test_settings,
        )
    assert disabled_exc.value.status_code == 401

    admin_row.is_enabled = 1
    admin_row.is_deleted = 1
    db_session.commit()

    with pytest.raises(AppError) as deleted_exc:
        authenticate_admin_user(
            db_session,
            username="admin",
            password=test_settings.default_admin_password,
            settings=test_settings,
        )
    assert deleted_exc.value.status_code == 401


def test_generate_raw_api_key_format_prefix_and_sha256() -> None:
    from app.auth.service import generate_raw_api_key
    from app.common.crypto import sha256_hex

    raw1, prefix1, digest1 = generate_raw_api_key()
    raw2, prefix2, digest2 = generate_raw_api_key()

    assert raw1 != raw2
    assert raw1.startswith("evk_live_")
    assert len(raw1) > 32
    assert prefix1 == raw1[:13]
    assert len(prefix1) <= 20
    assert digest1 == sha256_hex(raw1)
    assert digest2 == sha256_hex(raw2)


def test_resolve_provider_api_key_for_none_bearer_and_env_ref(
    monkeypatch: pytest.MonkeyPatch,
    test_settings: Settings,
) -> None:
    from app.common.crypto import encrypt_secret
    from app.db.models import DecisionProvider
    from app.providers import resolve_provider_api_key

    ollama_p = DecisionProvider(
        provider_code="ollama_test",
        provider_name="Ollama",
        provider_kind="OLLAMA_LOCAL",
        endpoint_url="http://127.0.0.1:11434/v1/systemone",
        default_model="tev1:0.8b",
        auth_type="NONE",
        api_key_cipher=encrypt_secret("should-not-be-returned", master_key=test_settings.master_encryption_key),
    )
    assert resolve_provider_api_key(ollama_p, test_settings) is None

    bearer_p = DecisionProvider(
        provider_code="emrank_test",
        provider_name="EMRank",
        provider_kind="DECISION_RELAY",
        endpoint_url="https://emrank.tech/v1/decisions",
        default_model="typesafe/jev-1.14.0",
        auth_type="BEARER",
        api_key_cipher=encrypt_secret("sk-emrank-secret-9f2a", master_key=test_settings.master_encryption_key),
    )
    assert resolve_provider_api_key(bearer_p, test_settings) == "sk-emrank-secret-9f2a"

    env_p = DecisionProvider(
        provider_code="typesafe_env_test",
        provider_name="TypeSafe",
        provider_kind="TYPESAFE_OFFICIAL",
        endpoint_url="https://api.typesafe.ai/v1/systemone",
        default_model="jev-1.14.0",
        auth_type="ENV_REF",
        api_key_env_name="TYPESAFE_UNIT_TEST_KEY",
    )
    monkeypatch.delenv("TYPESAFE_UNIT_TEST_KEY", raising=False)
    assert resolve_provider_api_key(env_p, test_settings) is None

    monkeypatch.setenv("TYPESAFE_UNIT_TEST_KEY", "ts-live-env-key-8811")
    assert resolve_provider_api_key(env_p, test_settings) == "ts-live-env-key-8811"


def test_normalized_confidence_and_noul_parser_across_three_provider_formats() -> None:
    from app.providers.client import compute_normalized_confidence, parse_noul_answer

    # 4-way distribution [0.03, 0.05, 0.12, 0.80]: (4 * 0.80 - 1) / (4 - 1) = 2.2 / 3 = 0.733333
    assert compute_normalized_confidence([0.03, 0.05, 0.12, 0.80]) == pytest.approx(0.733333, abs=1e-6)
    # Uniform 4-way [0.25, 0.25, 0.25, 0.25] -> 0.0
    assert compute_normalized_confidence([0.25, 0.25, 0.25, 0.25]) == 0.0
    # Upstream confidence takes precedence when provided
    assert compute_normalized_confidence([0.05, 0.14, 0.81], upstream_confidence=0.88) == 0.88

    # Official TypeSafe / Ollama /v1/systemone noul object {"type": "noul", "noul": 0.982}
    p1, c1 = parse_noul_answer({"type": "noul", "noul": 0.982})
    assert p1 == pytest.approx(0.982)
    assert c1 == pytest.approx(0.964)

    # EMRank / relay variant with explicit confidence or probabilities map
    p2, c2 = parse_noul_answer(
        {"probabilities": {"true": 0.85, "false": 0.15}, "confidence": 0.72}
    )
    assert p2 == pytest.approx(0.85)
    assert c2 == pytest.approx(0.72)

    with pytest.raises(ValueError):
        parse_noul_answer({"type": "noul", "noul": 1.5})


async def test_probe_provider_connectivity_forced_direct_vs_proxy_fallback_local_server(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import json as json_lib

    from app.config import Settings
    from app.db.models import DecisionProvider
    from app.providers.service import probe_provider_connectivity

    received_requests: list[tuple[str, dict[str, str], dict]] = []

    async def handle_s1(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        raw_head = await reader.readuntil(b"\r\n\r\n")
        head_text = raw_head.decode("utf-8", errors="replace")
        lines = head_text.split("\r\n")
        req_path = lines[0].split(" ")[1]
        hdrs: dict[str, str] = {}
        content_len = 0
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                hdrs[k.strip().lower()] = v.strip()
                if k.strip().lower() == "content-length":
                    content_len = int(v.strip())
        raw_body = await reader.readexactly(content_len) if content_len > 0 else b"{}"
        received_requests.append((req_path, hdrs, json_lib.loads(raw_body.decode("utf-8"))))

        resp_payload = json_lib.dumps(
            {
                "model": "tev1:0.8b",
                "answers": {"ping": {"type": "noul", "noul": 0.95}},
                "usage": {"input_tokens": 24, "output_tokens": 2},
            }
        ).encode("utf-8")
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"X-Request-ID: req_upstream_local_01\r\n"
            + f"Content-Length: {len(resp_payload)}\r\nConnection: close\r\n\r\n".encode("ascii")
            + resp_payload
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle_s1, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    try:
        # Poison ambient proxy env and configure unreachable first proxy candidate (port 1)
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("EMRANK_API_KEY", "sk-emrank-live-probe")

        probe_cfg = Settings(
            _env_file=None,
            proxy_candidates=["http://127.0.0.1:1"],
        )

        # 1. OLLAMA_LOCAL must force DIRECT (attempts == 1) even if network_mode column says PROXY_FALLBACK
        ollama_row = db_session.scalar(
            select(DecisionProvider).where(DecisionProvider.provider_code == "ollama_local_tev1")
        )
        assert ollama_row is not None
        ollama_row.endpoint_url = f"http://127.0.0.1:{port}/v1/systemone"
        ollama_row.network_mode = "PROXY_FALLBACK"
        ollama_row.timeout_seconds = 2
        db_session.commit()

        ollama_res = await probe_provider_connectivity(
            db_session,
            "ollama_local_tev1",
            settings=probe_cfg,
        )
        assert ollama_res["ok"] is True
        assert ollama_res["effective_network_mode"] == "DIRECT"
        assert ollama_res["channel_used"] == "DIRECT"
        assert ollama_res["attempts"] == 1
        assert ollama_res["probability_sample"]["p_true"] == pytest.approx(0.95)
        assert ollama_res["probability_sample"]["confidence"] == pytest.approx(0.90)

        path_1, hdrs_1, body_1 = received_requests[-1]
        assert path_1 == "/v1/systemone"
        assert "authorization" not in hdrs_1
        assert body_1["state"] == "探活测试"
        assert body_1["questions"]["ping"] == {
            "type": "noul",
            "instructions": "该文本是否为中文？",
        }
        assert body_1["keep_alive"] == "15m"

        # 2. DECISION_RELAY with PROXY_FALLBACK tries dead proxy candidate (port 1) then falls back to DIRECT (attempts == 2)
        relay_row = db_session.scalar(
            select(DecisionProvider).where(DecisionProvider.provider_code == "emrank_decisions_relay")
        )
        assert relay_row is not None
        relay_row.endpoint_url = f"http://127.0.0.1:{port}/v1/decisions"
        relay_row.timeout_seconds = 1
        db_session.commit()

        relay_res = await probe_provider_connectivity(
            db_session,
            "emrank_decisions_relay",
            settings=probe_cfg,
        )
        assert relay_res["ok"] is True
        assert relay_res["effective_network_mode"] == "PROXY_FALLBACK"
        assert relay_res["attempts"] == 2
        assert relay_res["channel_used"] == "DIRECT"
        assert relay_res["upstream_request_id"] == "req_upstream_local_01"

        path_2, hdrs_2, body_2 = received_requests[-1]
        assert path_2 == "/v1/decisions"
        assert hdrs_2.get("authorization") == "Bearer sk-emrank-live-probe"
        assert "keep_alive" not in body_2
    finally:
        server.close()
        await server.wait_closed()


def test_normalize_models_payload_openai_emrank_and_ollama_shapes() -> None:
    from app.providers.client import normalize_models_payload

    emrank_payload = {
        "object": "list",
        "data": [
            {"id": "typesafe/jev-1.14.0", "object": "model"},
            {"id": "typesafe/jev-1.13.0", "object": "model"},
            {"id": "typesafe/jev-1.14.0", "object": "model"},
        ],
    }
    assert normalize_models_payload(emrank_payload) == [
        "typesafe/jev-1.14.0",
        "typesafe/jev-1.13.0",
    ]

    ollama_payload = {
        "models": [
            {"name": "tev1:0.8b", "size": 580000000},
            {"name": "tev1:4b", "size": 2600000000},
        ]
    }
    assert normalize_models_payload(ollama_payload) == ["tev1:0.8b", "tev1:4b"]




