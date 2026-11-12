from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session


class _EchoPayload(BaseModel):
    count: int

from app.common.crypto import verify_password
from app.config import Settings
from app.db.models import AdminUser, DecisionProvider, Dimension, Suite, SutAdapter
from app.db.seed import seed_defaults


def test_seed_defaults_populates_all_v1_entities_idempotently(
    db_session: Session,
    test_settings: Settings,
) -> None:
    admin = db_session.scalar(select(AdminUser).where(AdminUser.username == "admin"))
    assert admin is not None
    assert admin.role == "ADMIN"
    assert admin.is_enabled == 1
    assert verify_password(test_settings.default_admin_password, admin.password_hash) is True
    original_admin_uuid = admin.uuid

    providers = {
        p.provider_code: p for p in db_session.scalars(select(DecisionProvider)).all()
    }
    assert set(providers.keys()) == {
        "ollama_local_tev1",
        "emrank_decisions_relay",
        "typesafe_official",
    }
    assert providers["ollama_local_tev1"].network_mode == "DIRECT"
    assert providers["ollama_local_tev1"].is_default == 1
    assert providers["ollama_local_tev1"].extra_options_json == {"keep_alive": "15m"}
    assert providers["emrank_decisions_relay"].endpoint_url == "https://emrank.tech/v1/decisions"
    assert providers["emrank_decisions_relay"].network_mode == "PROXY_FALLBACK"
    assert providers["typesafe_official"].default_model == "jev-1.14.0"

    adapters = {a.adapter_code: a for a in db_session.scalars(select(SutAdapter)).all()}
    assert set(adapters.keys()) == {
        "gov_e2e_routing_blocking",
        "gov_e2e_normal_chat",
        "gov_comp_routing_harness",
    }
    assert adapters["gov_e2e_routing_blocking"].eval_level == "E2E"
    assert adapters["gov_comp_routing_harness"].adapter_type == "COMPONENT_HARNESS"
    assert (
        adapters["gov_comp_routing_harness"].record_endpoint_url
        == "http://127.0.0.1:8000/internal/eval-harness/record"
    )

    dims = {(d.dimension_code, d.version): d for d in db_session.scalars(select(Dimension)).all()}
    assert set(dims.keys()) == {
        ("route_top1_match", 1),
        ("numeric_fact_grounding", 1),
        ("rag_faithfulness_s1", 1),
        ("policy_and_action_accuracy_s1", 1),
    }
    faithfulness_qs = dims[("rag_faithfulness_s1", 1)].questions_json
    assert faithfulness_qs is not None and len(faithfulness_qs) == 2
    assert faithfulness_qs[1]["option_scores"]["nothing_missing"] is None

    policy_qs = dims[("policy_and_action_accuracy_s1", 1)].questions_json
    assert policy_qs is not None and len(policy_qs) == 3
    assert policy_qs[2]["option_scores"]["not_applicable"] is None

    suites = {s.suite_code: s for s in db_session.scalars(select(Suite)).all()}
    assert set(suites.keys()) == {
        "suite_gov_e2e_golden_v1",
        "suite_gov_comp_routing_v1",
    }
    assert suites["suite_gov_e2e_golden_v1"].eval_level == "E2E"
    assert suites["suite_gov_e2e_golden_v1"].default_adapter_id == adapters["gov_e2e_routing_blocking"].id
    assert suites["suite_gov_e2e_golden_v1"].default_provider_id == providers["ollama_local_tev1"].id
    assert suites["suite_gov_comp_routing_v1"].eval_level == "COMPONENT"
    assert suites["suite_gov_comp_routing_v1"].default_adapter_id == adapters["gov_comp_routing_harness"].id

    # Re-run seed_defaults twice to verify strict idempotency
    seed_defaults(db_session, settings=test_settings)
    seed_defaults(db_session, settings=test_settings)

    assert len(db_session.scalars(select(AdminUser)).all()) == 1
    assert len(db_session.scalars(select(DecisionProvider)).all()) == 3
    assert len(db_session.scalars(select(SutAdapter)).all()) == 3
    assert len(db_session.scalars(select(Dimension)).all()) == 4
    assert len(db_session.scalars(select(Suite)).all()) == 2
    admin_after = db_session.scalar(select(AdminUser).where(AdminUser.username == "admin"))
    assert admin_after is not None and admin_after.uuid == original_admin_uuid


async def test_health_endpoint_and_error_envelopes(
    client: "httpx.AsyncClient",
    app: "FastAPI",
) -> None:
    from app.common.errors import AppError

    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    assert body["message"] == "ok"
    assert body["request_id"].startswith("req_")
    assert resp.headers.get("X-Request-ID") == body["request_id"]
    assert body["data"]["status"] == "healthy"
    assert body["data"]["db_ok"] is True

    # Unknown API path returns 404 JSON envelope
    not_found = await client.get("/api/v1/unknown-endpoint")
    assert not_found.status_code == 404
    nf_body = not_found.json()
    assert nf_body["code"] == 404
    assert nf_body["request_id"].startswith("req_")
    assert nf_body["data"] is None

    # AppError & validation error return unified envelopes
    probe_router = APIRouter()

    @probe_router.post("/probe-error")
    async def _probe_error(payload: _EchoPayload) -> dict:
        if payload.count < 0:
            raise AppError("count 不能为负数", status_code=400, code=40010, data={"field": "count"})
        return {"count": payload.count}

    app.include_router(probe_router, prefix="/api/v1")

    bad_biz = await client.post("/api/v1/probe-error", json={"count": -1})
    assert bad_biz.status_code == 400
    assert bad_biz.json()["code"] == 40010
    assert bad_biz.json()["message"] == "count 不能为负数"
    assert bad_biz.json()["data"] == {"field": "count"}

    bad_val = await client.post("/api/v1/probe-error", json={"count": "not-an-int"})
    assert bad_val.status_code == 422
    assert bad_val.json()["code"] == 422
    assert "errors" in bad_val.json()["data"]


async def test_auth_dependencies_console_jwt_and_api_key(
    client: "httpx.AsyncClient",
    app: "FastAPI",
    db_session: Session,
    admin_headers: dict[str, str],
) -> None:
    from datetime import timedelta
    from fastapi import APIRouter, Depends
    from app.common.auth_deps import (
        CurrentPrincipal,
        require_console_or_api_key,
        require_console_user,
    )
    from app.common.crypto import sha256_hex
    from app.common.envelope import ok_response
    from app.db.base import utcnow_naive
    from app.db.models import ApiKey

    auth_probe = APIRouter()

    @auth_probe.get("/protected-console")
    async def _protected_console(
        principal: CurrentPrincipal = Depends(require_console_user),
    ) -> dict:
        return ok_response(principal.model_dump())

    @auth_probe.get("/protected-hybrid")
    async def _protected_hybrid(
        principal: CurrentPrincipal = Depends(require_console_or_api_key),
    ) -> dict:
        return ok_response(principal.model_dump())

    app.include_router(auth_probe, prefix="/api/v1")

    # Missing credentials -> 401 envelope
    unauth = await client.get("/api/v1/protected-console")
    assert unauth.status_code == 401
    assert unauth.json()["code"] == 401

    # Valid admin JWT -> 200 on both console and hybrid routes
    ok_console = await client.get("/api/v1/protected-console", headers=admin_headers)
    assert ok_console.status_code == 200
    assert ok_console.json()["data"]["principal_type"] == "ADMIN_USER"
    assert ok_console.json()["data"]["identifier"] == "admin"

    # Seed an ACTIVE, REVOKED, and EXPIRED API key in ev_api_key
    raw_active = "evk_live_9a2c_valid_secret_token_001"
    raw_revoked = "evk_live_dead_revoked_secret_token_002"
    raw_expired = "evk_live_old0_expired_secret_token_003"

    db_session.add_all(
        [
            ApiKey(
                key_name="ci-nightly",
                key_prefix="evk_live_9a2c",
                key_sha256=sha256_hex(raw_active),
                status="ACTIVE",
                expires_at=utcnow_naive() + timedelta(days=7),
            ),
            ApiKey(
                key_name="ci-revoked",
                key_prefix="evk_live_dead",
                key_sha256=sha256_hex(raw_revoked),
                status="REVOKED",
            ),
            ApiKey(
                key_name="ci-expired",
                key_prefix="evk_live_old0",
                key_sha256=sha256_hex(raw_expired),
                status="ACTIVE",
                expires_at=utcnow_naive() - timedelta(minutes=5),
            ),
        ]
    )
    db_session.commit()

    # Console-only endpoint rejects X-API-Key
    api_on_console = await client.get(
        "/api/v1/protected-console", headers={"X-API-Key": raw_active}
    )
    assert api_on_console.status_code == 401

    # Hybrid endpoint accepts active X-API-Key and updates last_used_at
    ok_hybrid = await client.get(
        "/api/v1/protected-hybrid", headers={"X-API-Key": raw_active}
    )
    assert ok_hybrid.status_code == 200
    assert ok_hybrid.json()["data"]["principal_type"] == "API_KEY"
    assert ok_hybrid.json()["data"]["identifier"] == "evk_live_9a2c"

    db_session.expire_all()
    active_row = db_session.scalar(
        select(ApiKey).where(ApiKey.key_prefix == "evk_live_9a2c")
    )
    assert active_row is not None and active_row.last_used_at is not None

    # Revoked and expired API keys are rejected with 401
    rev_resp = await client.get(
        "/api/v1/protected-hybrid", headers={"X-API-Key": raw_revoked}
    )
    assert rev_resp.status_code == 401

    exp_resp = await client.get(
        "/api/v1/protected-hybrid", headers={"X-API-Key": raw_expired}
    )
    assert exp_resp.status_code == 401


async def test_spa_static_fallback_does_not_shadow_api_routes(
    tmp_path: "Path",
    db_engine: "Engine",
) -> None:
    import httpx
    from app.db.session import build_session_factory, get_db
    from app.main import create_app

    dist_dir = tmp_path / "web_dist"
    assets_dir = dist_dir / "assets"
    assets_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text(
        "<!doctype html><html><body>AI Eval Console SPA</body></html>",
        encoding="utf-8",
    )
    (assets_dir / "app.js").write_text("console.log('eval-spa');", encoding="utf-8")

    spa_settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=f"sqlite:///{(tmp_path / 'spa_test.db').as_posix()}",
        web_dist_dir=str(dist_dir),
        auto_init_db=False,
    )
    spa_app = create_app(settings=spa_settings)
    factory = build_session_factory(db_engine)

    def _override_db():
        s = factory()
        try:
            yield s
        finally:
            s.close()

    spa_app.dependency_overrides[get_db] = _override_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=spa_app), base_url="http://testserver"
    ) as ac:
        root_resp = await ac.get("/")
        assert root_resp.status_code == 200
        assert "AI Eval Console SPA" in root_resp.text

        deep_route = await ac.get("/reports/RUN_20261112_0001")
        assert deep_route.status_code == 200
        assert "AI Eval Console SPA" in deep_route.text

        asset_resp = await ac.get("/assets/app.js")
        assert asset_resp.status_code == 200
        assert "eval-spa" in asset_resp.text

        api_health = await ac.get("/api/v1/health")
        assert api_health.status_code == 200
        assert api_health.json()["code"] == 200

        api_404 = await ac.get("/api/v1/missing-route")
        assert api_404.status_code == 404
        assert api_404.json()["code"] == 404


async def test_lifespan_auto_initializes_schema_and_seeds_fresh_db(tmp_path: Path) -> None:
    from app.db.models import DecisionProvider
    from app.db.session import build_session_factory, get_engine
    from app.main import create_app

    fresh_db = tmp_path / "fresh_startup.db"
    startup_settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{fresh_db.as_posix()}",
    )
    assert startup_settings.auto_init_db is True

    fresh_app = create_app(settings=startup_settings)
    try:
        async with fresh_app.router.lifespan_context(fresh_app):
            factory = build_session_factory(get_engine(startup_settings))
            with factory() as s:
                providers = s.scalars(select(DecisionProvider)).all()
                assert len(providers) == 3
    finally:
        get_engine(startup_settings).dispose()


