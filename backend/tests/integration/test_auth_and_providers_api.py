from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import AdminUser


async def test_login_and_auth_me_lifecycle(
    client: httpx.AsyncClient,
    db_session: Session,
    test_settings: Settings,
) -> None:
    unauth_me = await client.get("/api/v1/auth/me")
    assert unauth_me.status_code == 401
    assert unauth_me.json()["code"] == 401

    bad_login = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "incorrect-password"},
    )
    assert bad_login.status_code == 401
    assert bad_login.json()["code"] == 401

    ok_login = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": test_settings.default_admin_password},
    )
    assert ok_login.status_code == 200
    body = ok_login.json()
    assert body["code"] == 200
    data = body["data"]
    assert data["token_type"] == "Bearer"
    assert data["expires_in"] == test_settings.jwt_expire_minutes * 60
    assert data["user"]["username"] == "admin"
    assert data["user"]["display_name"] == test_settings.default_admin_display_name
    assert data["user"]["last_login_at"] is not None

    token = data["access_token"]
    me_resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_resp.status_code == 200
    me_data = me_resp.json()["data"]
    assert me_data["username"] == "admin"
    assert me_data["role"] == "ADMIN"
    assert me_data["principal_type"] == "ADMIN_USER"

    # Disable admin user -> login and /auth/me both reject with 401
    admin_row = db_session.scalar(select(AdminUser).where(AdminUser.username == "admin"))
    assert admin_row is not None
    admin_row.is_enabled = 0
    db_session.commit()

    disabled_login = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": test_settings.default_admin_password},
    )
    assert disabled_login.status_code == 401

    disabled_me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert disabled_me.status_code == 401


async def test_api_keys_create_list_revoke_and_hybrid_auth_rejection(
    client: httpx.AsyncClient,
    app,
    db_session: Session,
    admin_headers: dict[str, str],
) -> None:
    from fastapi import APIRouter, Depends

    from app.common.auth_deps import CurrentPrincipal, require_console_or_api_key
    from app.common.crypto import sha256_hex
    from app.common.envelope import ok_response
    from app.db.models import ApiKey

    ci_router = APIRouter()

    @ci_router.post("/runs-ci-probe")
    def _ci_probe(
        principal: CurrentPrincipal = Depends(require_console_or_api_key),
    ) -> dict:
        return ok_response(principal.model_dump())

    app.include_router(ci_router, prefix="/api/v1")

    # Unauthenticated calls to /api-keys must fail with 401
    unauth_list = await client.get("/api/v1/api-keys")
    assert unauth_list.status_code == 401

    # Create first API key via console JWT
    create_resp = await client.post(
        "/api/v1/api-keys",
        headers=admin_headers,
        json={"key_name": "github-actions-nightly", "rate_limit_rpm": 120, "expires_in_days": 30},
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["data"]
    raw_key = created["api_key_plain"]
    key_id = created["id"]

    assert raw_key.startswith("evk_live_")
    assert created["key_prefix"] == raw_key[:13]
    assert created["status"] == "ACTIVE"
    assert created["rate_limit_rpm"] == 120
    assert created["created_by"] == "admin"
    assert "key_sha256" not in created

    # Verify DB stores ONLY prefix and SHA-256, never plaintext
    db_row = db_session.scalar(select(ApiKey).where(ApiKey.id == key_id))
    assert db_row is not None
    assert db_row.key_prefix == raw_key[:13]
    assert db_row.key_sha256 == sha256_hex(raw_key)
    assert raw_key not in (db_row.key_prefix, db_row.key_sha256, db_row.key_name)

    # List API keys: must NOT return api_key_plain or key_sha256
    list_resp = await client.get("/api/v1/api-keys", headers=admin_headers)
    assert list_resp.status_code == 200
    items = list_resp.json()["data"]
    assert len(items) == 1
    assert items[0]["id"] == key_id
    assert items[0]["key_prefix"] == raw_key[:13]
    assert "api_key_plain" not in items[0]
    assert "key_sha256" not in items[0]

    # X-API-Key cannot manage /api-keys (console-only), but CAN trigger hybrid CI endpoint
    key_on_console = await client.get("/api/v1/api-keys", headers={"X-API-Key": raw_key})
    assert key_on_console.status_code == 401

    ci_ok = await client.post("/api/v1/runs-ci-probe", headers={"X-API-Key": raw_key})
    assert ci_ok.status_code == 200
    assert ci_ok.json()["data"]["principal_type"] == "API_KEY"
    assert ci_ok.json()["data"]["identifier"] == raw_key[:13]

    # Revoke via POST /api-keys/{key_id}/revoke -> immediately rejected with 401
    revoke_resp = await client.post(f"/api/v1/api-keys/{key_id}/revoke", headers=admin_headers)
    assert revoke_resp.status_code == 200
    assert revoke_resp.json()["data"]["status"] == "REVOKED"

    ci_after_revoke = await client.post("/api/v1/runs-ci-probe", headers={"X-API-Key": raw_key})
    assert ci_after_revoke.status_code == 401

    # Also verify DELETE /api-keys/{key_id} revokes an active key and 404s on missing id
    create2 = await client.post(
        "/api/v1/api-keys",
        headers=admin_headers,
        json={"key_name": "temp-runner"},
    )
    raw_key2 = create2.json()["data"]["api_key_plain"]
    key_id2 = create2.json()["data"]["id"]

    del_resp = await client.delete(f"/api/v1/api-keys/{key_id2}", headers=admin_headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["data"]["status"] == "REVOKED"

    ci_after_del = await client.post("/api/v1/runs-ci-probe", headers={"X-API-Key": raw_key2})
    assert ci_after_del.status_code == 401

    missing_rev = await client.post("/api/v1/api-keys/999999/revoke", headers=admin_headers)
    assert missing_rev.status_code == 404


async def test_providers_upsert_update_secret_masking_ollama_defaults_and_exclusive_default(
    client: httpx.AsyncClient,
    db_session: Session,
    admin_headers: dict[str, str],
    test_settings: Settings,
) -> None:
    from app.common.crypto import decrypt_secret
    from app.db.models import DecisionProvider

    # Unauthenticated access rejected with 401
    unauth = await client.get("/api/v1/providers")
    assert unauth.status_code == 401

    # Initial seeded providers (3 rows)
    init_resp = await client.get("/api/v1/providers", headers=admin_headers)
    assert init_resp.status_code == 200
    init_items = init_resp.json()["data"]
    assert len(init_items) == 3
    for item in init_items:
        assert "api_key_cipher" not in item
        assert "api_key_plain" not in item

    # Invalid provider_kind rejected with 422
    bad_kind = await client.post(
        "/api/v1/providers",
        headers=admin_headers,
        json={
            "provider_code": "bad_kind_p",
            "provider_name": "Invalid Kind",
            "provider_kind": "OPENAI_CHAT",
            "endpoint_url": "https://api.openai.com/v1/chat/completions",
            "default_model": "gpt-4o",
        },
    )
    assert bad_kind.status_code == 422

    # Upsert EMRank DECISION_RELAY with BEARER secret & is_default=True
    emrank_resp = await client.post(
        "/api/v1/providers",
        headers=admin_headers,
        json={
            "provider_code": "emrank_decisions_prod",
            "provider_name": "EMRank 决策中转站",
            "provider_kind": "DECISION_RELAY",
            "endpoint_url": "https://emrank.tech/v1/decisions",
            "models_list_url": "https://emrank.tech/v1/models",
            "default_model": "typesafe/jev-1.14.0",
            "auth_type": "BEARER",
            "api_key_plain": "sk-emrank9876543219f2a",
            "network_mode": "PROXY_FALLBACK",
            "max_questions_per_call": 64,
            "max_options_per_question": 24,
            "waf_strip_code_retry": True,
            "cost_per_1m_input_tokens": 0.20,
            "is_default": True,
        },
    )
    assert emrank_resp.status_code == 200
    emrank_data = emrank_resp.json()["data"]
    assert emrank_data["provider_code"] == "emrank_decisions_prod"
    assert emrank_data["api_key_masked"] == "sk-****9f2a"
    assert "api_key_cipher" not in emrank_data
    assert "api_key_plain" not in emrank_data
    assert emrank_data["is_default"] is True

    # Verify DB encrypted cipher and exclusive is_default=1
    db_session.expire_all()
    emrank_row = db_session.scalar(
        select(DecisionProvider).where(DecisionProvider.provider_code == "emrank_decisions_prod")
    )
    assert emrank_row is not None
    assert emrank_row.api_key_cipher is not None
    assert "sk-emrank9876543219f2a" not in emrank_row.api_key_cipher
    assert (
        decrypt_secret(emrank_row.api_key_cipher, master_key=test_settings.master_encryption_key)
        == "sk-emrank9876543219f2a"
    )

    default_codes = [
        p.provider_code
        for p in db_session.scalars(
            select(DecisionProvider).where(
                DecisionProvider.is_default == 1,
                DecisionProvider.is_deleted == 0,
            )
        ).all()
    ]
    assert default_codes == ["emrank_decisions_prod"]

    # Create OLLAMA_LOCAL without specifying network_mode or auth_type -> defaults to DIRECT and NONE
    ollama_resp = await client.post(
        "/api/v1/providers",
        headers=admin_headers,
        json={
            "provider_code": "ollama_tev1_4b",
            "provider_name": "本地 Ollama tev1:4b",
            "provider_kind": "OLLAMA_LOCAL",
            "endpoint_url": "http://127.0.0.1:11434/v1/systemone",
            "models_list_url": "http://127.0.0.1:11434/v1/models",
            "default_model": "tev1:4b",
            "extra_options_json": {"keep_alive": "30m"},
            "is_default": True,
        },
    )
    assert ollama_resp.status_code == 200
    ollama_data = ollama_resp.json()["data"]
    assert ollama_data["network_mode"] == "DIRECT"
    assert ollama_data["auth_type"] == "NONE"
    assert ollama_data["waf_strip_code_retry"] is False
    assert ollama_data["is_default"] is True

    # Exclusive default switched from emrank_decisions_prod to ollama_tev1_4b
    list_after = (await client.get("/api/v1/providers", headers=admin_headers)).json()["data"]
    defaults_after = [item["provider_code"] for item in list_after if item["is_default"]]
    assert defaults_after == ["ollama_tev1_4b"]

    # PUT /providers/{provider_code}: update status & name without wiping existing BEARER key
    put_resp = await client.put(
        "/api/v1/providers/emrank_decisions_prod",
        headers=admin_headers,
        json={
            "provider_name": "EMRank 生产中转站(已停用)",
            "status": "DISABLED",
        },
    )
    assert put_resp.status_code == 200
    put_data = put_resp.json()["data"]
    assert put_data["provider_name"] == "EMRank 生产中转站(已停用)"
    assert put_data["status"] == "DISABLED"
    assert put_data["api_key_masked"] == "sk-****9f2a"

    # Re-POST upsert without resending api_key_plain preserves existing encrypted BEARER secret
    re_upsert = await client.post(
        "/api/v1/providers",
        headers=admin_headers,
        json={
            "provider_code": "emrank_decisions_prod",
            "provider_name": "EMRank 生产中转站(已恢复)",
            "provider_kind": "DECISION_RELAY",
            "endpoint_url": "https://emrank.tech/v1/decisions",
            "models_list_url": "https://emrank.tech/v1/models",
            "default_model": "typesafe/jev-1.14.0",
            "status": "ACTIVE",
        },
    )
    assert re_upsert.status_code == 200
    assert re_upsert.json()["data"]["status"] == "ACTIVE"
    assert re_upsert.json()["data"]["api_key_masked"] == "sk-****9f2a"

    missing_put = await client.put(
        "/api/v1/providers/nonexistent_provider",
        headers=admin_headers,
        json={"status": "DISABLED"},
    )
    assert missing_put.status_code == 404


async def test_provider_connectivity_probe_all_three_kinds_and_health_persistence(
    client: httpx.AsyncClient,
    app,
    db_session: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json as json_lib

    from app.api.providers import get_provider_http_transport
    from app.db.models import DecisionProvider

    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-official-secret-001")
    monkeypatch.setenv("EMRANK_API_KEY", "sk-emrank-relay-002")

    captured: dict[str, tuple[httpx.Request, dict]] = {}
    fail_emrank = False

    def mock_handler(request: httpx.Request) -> httpx.Response:
        body = json_lib.loads(request.content.decode("utf-8"))
        host_and_path = f"{request.url.host}{request.url.path}"
        captured[host_and_path] = (request, body)

        if request.url.host == "api.typesafe.ai":
            return httpx.Response(
                200,
                headers={"X-Request-ID": "ts_req_9001"},
                json={
                    "model": "jev-1.14.0",
                    "answers": {"ping": {"type": "noul", "noul": 0.991}},
                    "usage": {"input_tokens": 18, "output_tokens": 2},
                },
            )
        if request.url.host == "emrank.tech":
            if fail_emrank:
                return httpx.Response(
                    401,
                    headers={"X-Request-ID": "emr_req_unauth"},
                    json={"error": {"message": "Invalid API key"}},
                )
            return httpx.Response(
                200,
                headers={"X-Request-ID": "emr_req_7788"},
                json={
                    "model": "typesafe/jev-1.14.0",
                    "answers": {"ping": {"type": "noul", "noul": 0.974}},
                    "usage": {"input_tokens": 19, "output_tokens": 2},
                },
            )
        if request.url.host == "127.0.0.1" and request.url.path == "/v1/systemone":
            return httpx.Response(
                200,
                json={
                    "model": "tev1:0.8b",
                    "answers": {"ping": {"type": "noul", "noul": 0.942}},
                    "usage": {"input_tokens": 21, "output_tokens": 2},
                },
            )
        return httpx.Response(404, json={"error": "unexpected url"})

    app.dependency_overrides[get_provider_http_transport] = lambda: httpx.MockTransport(mock_handler)
    try:
        # 1. TypeSafe Official (/v1/systemone)
        ts_resp = await client.post(
            "/api/v1/providers/typesafe_official/test", headers=admin_headers
        )
        assert ts_resp.status_code == 200
        ts_data = ts_resp.json()["data"]
        assert ts_data["ok"] is True
        assert ts_data["upstream_request_id"] == "ts_req_9001"
        assert ts_data["probability_sample"]["p_true"] == pytest.approx(0.991)
        ts_req, ts_body = captured["api.typesafe.ai/v1/systemone"]
        assert ts_req.headers.get("Authorization") == "Bearer ts-official-secret-001"
        assert ts_body["model"] == "jev-1.14.0"
        assert ts_body["state"] == "探活测试"
        assert "keep_alive" not in ts_body

        # 2. EMRank Relay (/v1/decisions)
        emr_resp = await client.post(
            "/api/v1/providers/emrank_decisions_relay/test", headers=admin_headers
        )
        assert emr_resp.status_code == 200
        emr_data = emr_resp.json()["data"]
        assert emr_data["ok"] is True
        assert emr_data["upstream_request_id"] == "emr_req_7788"
        assert emr_data["probability_sample"]["p_true"] == pytest.approx(0.974)
        emr_req, emr_body = captured["emrank.tech/v1/decisions"]
        assert emr_req.headers.get("Authorization") == "Bearer sk-emrank-relay-002"
        assert emr_body["model"] == "typesafe/jev-1.14.0"

        # 3. Local Ollama tev1:0.8b (/v1/systemone) -> merges extra_options_json & omits Authorization
        ol_resp = await client.post(
            "/api/v1/providers/ollama_local_tev1/test", headers=admin_headers
        )
        assert ol_resp.status_code == 200
        ol_data = ol_resp.json()["data"]
        assert ol_data["ok"] is True
        assert ol_data["effective_network_mode"] == "DIRECT"
        assert ol_data["probability_sample"]["p_true"] == pytest.approx(0.942)
        ol_req, ol_body = captured["127.0.0.1/v1/systemone"]
        assert "Authorization" not in ol_req.headers
        assert ol_body["model"] == "tev1:0.8b"
        assert ol_body["keep_alive"] == "15m"

        # Verify DB persisted last_health_ok == 1 and last_checked_at != None
        db_session.expire_all()
        ol_row = db_session.scalar(
            select(DecisionProvider).where(DecisionProvider.provider_code == "ollama_local_tev1")
        )
        assert ol_row is not None
        assert ol_row.last_health_ok == 1
        assert ol_row.last_checked_at is not None

        # 4. Simulate EMRank upstream 401 failure -> updates last_health_ok = 0
        fail_emrank = True
        fail_resp = await client.post(
            "/api/v1/providers/emrank_decisions_relay/test", headers=admin_headers
        )
        assert fail_resp.status_code == 200
        fail_data = fail_resp.json()["data"]
        assert fail_data["ok"] is False
        assert fail_data["status_code"] == 401
        assert fail_data["last_health_ok"] is False
        assert fail_data["error"] is not None

        db_session.expire_all()
        emr_row = db_session.scalar(
            select(DecisionProvider).where(
                DecisionProvider.provider_code == "emrank_decisions_relay"
            )
        )
        assert emr_row is not None
        assert emr_row.last_health_ok == 0
        assert emr_row.last_checked_at is not None
    finally:
        app.dependency_overrides.pop(get_provider_http_transport, None)


async def test_provider_models_fetch_and_normalization_across_providers(
    client: httpx.AsyncClient,
    app,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.providers import get_provider_http_transport

    monkeypatch.setenv("EMRANK_API_KEY", "sk-emrank-models-key")
    seen_headers: dict[str, dict[str, str]] = {}

    def models_handler(request: httpx.Request) -> httpx.Response:
        key = f"{request.url.host}{request.url.path}"
        seen_headers[key] = dict(request.headers)
        if request.url.host == "emrank.tech" and request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {"id": "typesafe/jev-1.14.0", "object": "model"},
                        {"id": "typesafe/jev-1.13.0", "object": "model"},
                    ],
                },
            )
        if request.url.host == "127.0.0.1" and request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "tev1:0.8b"},
                        {"name": "tev1:4b"},
                    ]
                },
            )
        return httpx.Response(500, text="unexpected upstream error")

    app.dependency_overrides[get_provider_http_transport] = lambda: httpx.MockTransport(
        models_handler
    )
    try:
        # EMRank /v1/models (OpenAI data[].id shape + Bearer header)
        emr_models = await client.get(
            "/api/v1/providers/emrank_decisions_relay/models", headers=admin_headers
        )
        assert emr_models.status_code == 200
        assert emr_models.json()["data"]["models"] == [
            "typesafe/jev-1.14.0",
            "typesafe/jev-1.13.0",
        ]
        assert (
            seen_headers["emrank.tech/v1/models"].get("authorization")
            == "Bearer sk-emrank-models-key"
        )

        # Ollama /v1/models (Ollama models[].name shape, no Authorization header)
        ol_models = await client.get(
            "/api/v1/providers/ollama_local_tev1/models", headers=admin_headers
        )
        assert ol_models.status_code == 200
        assert ol_models.json()["data"]["models"] == ["tev1:0.8b", "tev1:4b"]
        assert "authorization" not in seen_headers["127.0.0.1/v1/models"]

        # TypeSafe Official has models_list_url = None -> returns configured default_model cleanly
        ts_models = await client.get(
            "/api/v1/providers/typesafe_official/models", headers=admin_headers
        )
        assert ts_models.status_code == 200
        assert ts_models.json()["data"]["models"] == ["jev-1.14.0"]

        # Unknown provider -> 404
        missing_models = await client.get(
            "/api/v1/providers/unknown_provider/models", headers=admin_headers
        )
        assert missing_models.status_code == 404
    finally:
        app.dependency_overrides.pop(get_provider_http_transport, None)




