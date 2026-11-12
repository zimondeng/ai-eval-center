from __future__ import annotations

import pytest


def test_settings_defaults_and_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import Settings

    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DESENSITIZE_BASE_URL", raising=False)

    cfg = Settings(_env_file=None)
    assert cfg.port == 18200
    assert "kd_eval" in cfg.database_url
    assert cfg.jwt_expire_minutes > 0
    assert len(cfg.jwt_secret) >= 32
    assert len(cfg.master_encryption_key) >= 16
    assert cfg.default_admin_username == "admin"
    assert cfg.default_admin_password != ""
    assert cfg.desensitize_base_url == "http://127.0.0.1:18100"
    assert cfg.proxy_candidates == ["http://127.0.0.1:10808", "http://127.0.0.1:7897"]

    monkeypatch.setenv("PORT", "19200")
    monkeypatch.setenv("DESENSITIZE_BASE_URL", "http://127.0.0.1:28100")
    overridden = Settings(_env_file=None)
    assert overridden.port == 19200
    assert overridden.desensitize_base_url == "http://127.0.0.1:28100"


def test_aes256_gcm_encrypt_decrypt_and_tamper_rejection() -> None:
    from app.common.crypto import decrypt_secret, encrypt_secret

    master = "unit-test-master-key-material-01"
    plaintext = "sk-live-emrank-secret-9f2a"

    c1 = encrypt_secret(plaintext, master_key=master)
    c2 = encrypt_secret(plaintext, master_key=master)
    assert c1 != c2
    assert plaintext not in c1
    assert decrypt_secret(c1, master_key=master) == plaintext
    assert decrypt_secret(c2, master_key=master) == plaintext

    with pytest.raises(ValueError):
        decrypt_secret(c1, master_key="wrong-master-key-material-999999")

    tampered = c1[:-2] + ("AA" if not c1.endswith("AA") else "BB")
    with pytest.raises(ValueError):
        decrypt_secret(tampered, master_key=master)


def test_mask_secret_sha256_and_password_hash() -> None:
    from app.common.crypto import hash_password, mask_secret, sha256_hex, verify_password

    assert mask_secret("sk-123456789f2a") == "sk-****9f2a"
    assert mask_secret("abcd") == "****"
    assert mask_secret("abcdefgh") == "a****gh"
    assert mask_secret(None) is None

    assert (
        sha256_hex("abc")
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )

    pw_hash = hash_password("S3cureAdmin!2026")
    assert pw_hash != "S3cureAdmin!2026"
    assert verify_password("S3cureAdmin!2026", pw_hash) is True
    assert verify_password("wrong-password", pw_hash) is False


async def test_http_client_direct_and_proxy_fallback_against_local_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import httpx
    from app.common.http_client import request_with_policy, resolve_proxy_chain

    assert resolve_proxy_chain("DIRECT") == [None]
    assert resolve_proxy_chain("CUSTOM_PROXY", custom_proxy_url="http://127.0.0.1:7897") == [
        "http://127.0.0.1:7897"
    ]
    assert resolve_proxy_chain(
        "PROXY_FALLBACK", proxy_candidates=["http://127.0.0.1:10808", "http://127.0.0.1:7897"]
    ) == ["http://127.0.0.1:10808", "http://127.0.0.1:7897", None]

    with pytest.raises(ValueError):
        resolve_proxy_chain("CUSTOM_PROXY", custom_proxy_url="")

    async def handle_http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        body = b'{"pong":true}'
        resp_bytes = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii")
            + body
        )
        writer.write(resp_bytes)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle_http, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    target_url = f"http://127.0.0.1:{port}/ping"

    try:
        # Poison ambient proxy env vars to prove DIRECT enforces trust_env=False
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")

        direct_res = await request_with_policy(
            "GET",
            target_url,
            network_mode="DIRECT",
            timeout_seconds=3.0,
        )
        assert direct_res.response.status_code == 200
        assert direct_res.response.json() == {"pong": True}
        assert direct_res.used_proxy is None
        assert direct_res.channel_label == "DIRECT"
        assert direct_res.attempts == 1

        # PROXY_FALLBACK retries across two unreachable proxy ports then succeeds on direct
        fallback_res = await request_with_policy(
            "GET",
            target_url,
            network_mode="PROXY_FALLBACK",
            proxy_candidates=["http://127.0.0.1:1", "http://127.0.0.1:2"],
            timeout_seconds=0.3,
        )
        assert fallback_res.response.status_code == 200
        assert fallback_res.response.json() == {"pong": True}
        assert fallback_res.used_proxy is None
        assert fallback_res.channel_label == "DIRECT"
        assert fallback_res.attempts == 3

        # CUSTOM_PROXY does NOT silently bypass a dead configured proxy
        with pytest.raises(httpx.TransportError):
            await request_with_policy(
                "GET",
                target_url,
                network_mode="CUSTOM_PROXY",
                custom_proxy_url="http://127.0.0.1:1",
                timeout_seconds=0.3,
            )
    finally:
        server.close()
        await server.wait_closed()


async def test_http_client_pools_reuse_and_proxy_fallback_cache_invalidation() -> None:
    import asyncio
    from app.common.http_client import (
        aclose_http_client_pools,
        get_pooled_async_client,
        request_with_policy,
        reset_http_client_pools,
    )

    reset_http_client_pools()

    async def handle_origin(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        body = b'{"origin":true}'
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii")
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def handle_http_proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        body = b'{"via_proxy":true}'
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii")
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    origin_srv = await asyncio.start_server(handle_origin, "127.0.0.1", 0)
    origin_port = origin_srv.sockets[0].getsockname()[1]
    origin_url = f"http://127.0.0.1:{origin_port}/check"

    proxy_srv = await asyncio.start_server(handle_http_proxy, "127.0.0.1", 0)
    proxy_port = proxy_srv.sockets[0].getsockname()[1]
    working_proxy = f"http://127.0.0.1:{proxy_port}"

    try:
        c1 = get_pooled_async_client(None, False)
        c2 = get_pooled_async_client(None, False)
        c3 = get_pooled_async_client(None, True)
        assert c1 is c2
        assert c1 is not c3

        candidates = ["http://127.0.0.1:1", working_proxy]

        # 1st call: port 1 fails -> working_proxy succeeds (attempts == 2) and gets cached
        r1 = await request_with_policy(
            "GET",
            origin_url,
            network_mode="PROXY_FALLBACK",
            proxy_candidates=candidates,
            timeout_seconds=0.5,
        )
        assert r1.used_proxy == working_proxy
        assert r1.attempts == 2
        assert r1.response.json() == {"via_proxy": True}

        # 2nd call: hits cached working_proxy directly (attempts == 1, skips dead port 1)
        r2 = await request_with_policy(
            "GET",
            origin_url,
            network_mode="PROXY_FALLBACK",
            proxy_candidates=candidates,
            timeout_seconds=0.5,
        )
        assert r2.used_proxy == working_proxy
        assert r2.attempts == 1

        # Shut down working_proxy -> 3rd call hits TransportError on cached proxy,
        # invalidates cache, retries remaining chain, and succeeds via DIRECT!
        proxy_srv.close()
        await proxy_srv.wait_closed()

        r3 = await request_with_policy(
            "GET",
            origin_url,
            network_mode="PROXY_FALLBACK",
            proxy_candidates=candidates,
            timeout_seconds=0.5,
        )
        assert r3.used_proxy is None
        assert r3.channel_label == "DIRECT"
        assert r3.response.json() == {"origin": True}

        # 4th call now uses newly cached DIRECT in 1 attempt
        r4 = await request_with_policy(
            "GET",
            origin_url,
            network_mode="PROXY_FALLBACK",
            proxy_candidates=candidates,
            timeout_seconds=0.5,
        )
        assert r4.used_proxy is None
        assert r4.attempts == 1

        await aclose_http_client_pools()
    finally:
        origin_srv.close()
        await origin_srv.wait_closed()
        if proxy_srv.is_serving():
            proxy_srv.close()
            await proxy_srv.wait_closed()
        reset_http_client_pools()


async def test_http_client_mock_transport_request_and_sse_stream() -> None:
    import httpx
    from app.common.http_client import request_with_policy, stream_with_policy

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sse":
            sse_payload = b'data: {"delta":"hello"}\n\ndata: {"delta":" world"}\n\ndata: [DONE]\n\n'
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=sse_payload,
            )
        return httpx.Response(200, json={"echo_path": request.url.path})

    mock_transport = httpx.MockTransport(handler)

    res = await request_with_policy(
        "POST",
        "https://emrank.tech/v1/decisions",
        json={"model": "typesafe/jev-1.14.0"},
        transport=mock_transport,
    )
    assert res.response.status_code == 200
    assert res.response.json() == {"echo_path": "/v1/decisions"}
    assert res.channel_label == "MOCK_TRANSPORT"
    assert res.attempts == 1

    lines: list[str] = []
    async with stream_with_policy(
        "POST",
        "http://sut.local/sse",
        json={"query": "hi"},
        transport=mock_transport,
    ) as (resp, used_proxy):
        assert resp.status_code == 200
        assert used_proxy is None
        async for line in resp.aiter_lines():
            if line:
                lines.append(line)

    assert lines == [
        'data: {"delta":"hello"}',
        'data: {"delta":" world"}',
        "data: [DONE]",
    ]


def test_orm_models_match_01_schema_sql_and_dual_dialect_compatibility() -> None:
    import re
    from pathlib import Path
    from sqlalchemy import UniqueConstraint
    from sqlalchemy.dialects import mysql
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session
    from sqlalchemy.schema import CreateTable

    from app.db.base import Base
    from app.db.models import (
        AdminUser,
        ApiKey,
        Dataset,
        DecisionProvider,
        Dimension,
        EvalCase,
        EvalRun,
        EvalRunCase,
        ProbabilityLedger,
        Suite,
        SutAdapter,
    )
    from app.db.session import build_engine

    model_map = {
        "ev_admin_user": AdminUser,
        "ev_api_key": ApiKey,
        "ev_decision_provider": DecisionProvider,
        "ev_sut_adapter": SutAdapter,
        "ev_dimension": Dimension,
        "ev_suite": Suite,
        "ev_dataset": Dataset,
        "ev_case": EvalCase,
        "ev_run": EvalRun,
        "ev_run_case": EvalRunCase,
        "ev_probability_ledger": ProbabilityLedger,
    }
    assert set(Base.metadata.tables.keys()) == set(model_map.keys())
    for table_name, cls in model_map.items():
        assert cls.__tablename__ == table_name

    # Parse authoritative docs/sql/01_schema.sql and verify 100% column & UK parity
    sql_path = Path(__file__).resolve().parents[3] / "docs" / "sql" / "01_schema.sql"
    sql_text = sql_path.read_text(encoding="utf-8")
    table_blocks = re.findall(
        r"CREATE TABLE IF NOT EXISTS `(\w+)` \((.*?)\) ENGINE\s*=",
        sql_text,
        flags=re.DOTALL,
    )
    assert len(table_blocks) == 11

    forbidden_business_cols = {
        "selected_region",
        "java_request_id",
        "normal_chat_app_code",
        "target_agent_id",
        "route_mode",
    }

    for tbl_name, body in table_blocks:
        expected_cols: list[str] = []
        expected_uks: dict[str, tuple[str, ...]] = {}
        for raw_line in body.splitlines():
            line = raw_line.strip().rstrip(",")
            col_match = re.match(r"^`(\w+)`\s+", line)
            if col_match:
                expected_cols.append(col_match.group(1))
                continue
            uk_match = re.match(r"^UNIQUE KEY `(\w+)` \(([^)]+)\)", line)
            if uk_match:
                uk_name = uk_match.group(1)
                uk_cols = tuple(c.strip().strip("`") for c in uk_match.group(2).split(","))
                expected_uks[uk_name] = uk_cols

        sa_table = Base.metadata.tables[tbl_name]
        actual_cols = list(sa_table.columns.keys())
        assert actual_cols == expected_cols, f"Column mismatch in {tbl_name}"
        assert forbidden_business_cols.isdisjoint(actual_cols)

        actual_uks = {
            c.name: tuple(col.name for col in c.columns)
            for c in sa_table.constraints
            if isinstance(c, UniqueConstraint)
        }
        assert actual_uks == expected_uks, f"UniqueConstraint mismatch in {tbl_name}"

        mysql_ddl = str(CreateTable(sa_table).compile(dialect=mysql.dialect()))
        assert "UNSIGNED" in mysql_ddl and "AUTO_INCREMENT" in mysql_ddl
        assert "DATETIME(3)" in mysql_ddl

    case_mysql_ddl = str(
        CreateTable(Base.metadata.tables["ev_case"]).compile(dialect=mysql.dialect())
    )
    assert "MEDIUMTEXT" in case_mysql_ddl

    # Verify SQLite auto-increment PK + unique constraints enforcement
    engine = build_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ds = Dataset(
            dataset_code="ds_unit",
            version_tag="v1.0",
            dataset_name="Unit Dataset",
            eval_level="E2E",
        )
        session.add(ds)
        session.flush()
        assert ds.id is not None and ds.id >= 1

        c1 = EvalCase(
            dataset_id=ds.id,
            case_code="CASE_001",
            dataset_split="DEV",
            input_text="测试问题",
        )
        session.add(c1)
        session.commit()
        assert c1.id is not None and len(c1.uuid) == 32

        dup_case = EvalCase(
            dataset_id=ds.id,
            case_code="CASE_001",
            dataset_split="HOLDOUT",
            input_text="重复编码用例",
        )
        session.add(dup_case)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_register_api_routers_skips_missing_modules_and_missing_parent_packages() -> None:
    from fastapi import FastAPI
    from app.api.registry import ROUTE_MODULES, register_api_routers

    assert ROUTE_MODULES == (
        "app.api.health",
        "app.api.auth",
        "app.api.providers",
        "app.api.dimensions",
        "app.api.suites",
        "app.api.adapters",
        "app.api.datasets",
        "app.api.cases",
        "app.api.runs",
    )

    dummy_app = FastAPI()
    mounted = register_api_routers(
        dummy_app,
        prefix="/api/v1",
        module_names=["app.api.health", "app.api.nonexistent_route", "app.missing_pkg.routes"],
    )
    assert mounted == ["app.api.health"]
    assert "/api/v1/health" in dummy_app.openapi()["paths"]




