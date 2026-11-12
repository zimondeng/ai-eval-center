from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.common.crypto import sha256_hex
from app.db.base import utcnow_naive
from app.db.models import ApiKey


async def test_adapters_crud_filtering_auth_and_jsonpath_validation(
    client: httpx.AsyncClient,
    db_session: Session,
    admin_headers: dict[str, str],
) -> None:
    # 1. Require console JWT (rejects unauthenticated and CI API-key-only requests)
    unauth = await client.get("/api/v1/adapters")
    assert unauth.status_code == 401
    assert unauth.json()["code"] == 401

    raw_ci_key = "evk_live_adap_ci_only_token_001"
    db_session.add(
        ApiKey(
            key_name="ci-runner",
            key_prefix="evk_live_adap",
            key_sha256=sha256_hex(raw_ci_key),
            status="ACTIVE",
            expires_at=utcnow_naive() + timedelta(days=1),
        )
    )
    db_session.commit()

    api_key_attempt = await client.get(
        "/api/v1/adapters", headers={"X-API-Key": raw_ci_key}
    )
    assert api_key_attempt.status_code == 401

    # 2. List seeded adapters and filter by eval_level / adapter_type / status
    list_resp = await client.get("/api/v1/adapters", headers=admin_headers)
    assert list_resp.status_code == 200
    all_items = list_resp.json()["data"]
    assert [item["adapter_code"] for item in all_items] == [
        "gov_e2e_routing_blocking",
        "gov_e2e_normal_chat",
        "gov_comp_routing_harness",
    ]

    comp_resp = await client.get(
        "/api/v1/adapters",
        params={"eval_level": "COMPONENT", "adapter_type": "COMPONENT_HARNESS"},
        headers=admin_headers,
    )
    assert comp_resp.status_code == 200
    assert [item["adapter_code"] for item in comp_resp.json()["data"]] == [
        "gov_comp_routing_harness"
    ]

    # 3. Get single adapter & 404 on unknown code
    detail_resp = await client.get(
        "/api/v1/adapters/gov_comp_routing_harness", headers=admin_headers
    )
    assert detail_resp.status_code == 200
    assert (
        detail_resp.json()["data"]["record_endpoint_url"]
        == "http://127.0.0.1:8000/internal/eval-harness/record"
    )

    missing_resp = await client.get(
        "/api/v1/adapters/nonexistent_adapter", headers=admin_headers
    )
    assert missing_resp.status_code == 404
    assert missing_resp.json()["code"] == 404

    # 4. Reject invalid JSONPath expression on create
    bad_jsonpath = await client.post(
        "/api/v1/adapters",
        headers=admin_headers,
        json={
            "adapter_code": "broken_adapter",
            "adapter_name": "Broken JSONPath Adapter",
            "eval_level": "E2E",
            "adapter_type": "HTTP_JSON",
            "endpoint_url": "http://127.0.0.1:8080/ai/invoke",
            "request_mapping_json": {"field_map": {"query": "$..[unclosed"}},
            "response_mapping_json": {"actual_output": "$.data.answer"},
        },
    )
    assert bad_jsonpath.status_code == 400
    assert bad_jsonpath.json()["code"] == 400

    # 5. Create new HTTP_SSE adapter via POST /adapters, then upsert by same adapter_code
    create_resp = await client.post(
        "/api/v1/adapters",
        headers=admin_headers,
        json={
            "adapter_code": "gov_e2e_routing_sse",
            "adapter_name": "政务智能客服-流式路由入口",
            "eval_level": "E2E",
            "adapter_type": "HTTP_SSE",
            "endpoint_url": "http://127.0.0.1:8080/ai/invoke-sse",
            "auth_config_json": {
                "header": "Authorization",
                "prefix": "Bearer ",
                "env_name": "EVAL_JAVA_BEARER_TOKEN",
            },
            "request_mapping_json": {
                "body_template": {"responseMode": "streaming"},
                "field_map": {"query": "$.input_text"},
            },
            "response_mapping_json": {
                "delta_path": "$.delta",
                "actual_references": "$.data.references[*].content",
                "actual_structured": {"actual_agent_id": "$.data.finalAgentId"},
                "sut_trace": "$.data.events",
            },
            "timeout_seconds": 45,
            "max_concurrency": 6,
            "description": "流式SSE与TTFT验证入口",
        },
    )
    assert create_resp.status_code == 200
    created_data = create_resp.json()["data"]
    assert created_data["adapter_code"] == "gov_e2e_routing_sse"
    assert created_data["adapter_type"] == "HTTP_SSE"
    assert created_data["timeout_seconds"] == 45
    assert created_data["created_by"] == "admin"
    original_id = created_data["id"]

    upsert_resp = await client.post(
        "/api/v1/adapters",
        headers=admin_headers,
        json={
            "adapter_code": "gov_e2e_routing_sse",
            "adapter_name": "政务智能客服-流式路由入口(v2)",
            "eval_level": "E2E",
            "adapter_type": "HTTP_SSE",
            "endpoint_url": "http://127.0.0.1:8080/ai/invoke-sse-v2",
            "request_mapping_json": {
                "body_template": {"responseMode": "streaming"},
                "field_map": {"query": "$.input_text"},
            },
            "response_mapping_json": {"delta_path": "$.delta"},
            "timeout_seconds": 75,
        },
    )
    assert upsert_resp.status_code == 200
    assert upsert_resp.json()["data"]["id"] == original_id
    assert (
        upsert_resp.json()["data"]["endpoint_url"]
        == "http://127.0.0.1:8080/ai/invoke-sse-v2"
    )
    assert upsert_resp.json()["data"]["timeout_seconds"] == 75

    # 6. Update via PUT /adapters/{adapter_code} + filter by DISABLED status
    put_resp = await client.put(
        "/api/v1/adapters/gov_e2e_routing_sse",
        headers=admin_headers,
        json={
            "status": "DISABLED",
            "max_concurrency": 10,
            "description": "暂时停用维护",
        },
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["data"]["status"] == "DISABLED"
    assert put_resp.json()["data"]["max_concurrency"] == 10
    assert put_resp.json()["data"]["description"] == "暂时停用维护"

    disabled_list = await client.get(
        "/api/v1/adapters", params={"status": "DISABLED"}, headers=admin_headers
    )
    assert disabled_list.status_code == 200
    assert [item["adapter_code"] for item in disabled_list.json()["data"]] == [
        "gov_e2e_routing_sse"
    ]

    put_missing = await client.put(
        "/api/v1/adapters/unknown_code",
        headers=admin_headers,
        json={"status": "DISABLED"},
    )
    assert put_missing.status_code == 404


async def test_adapter_live_test_endpoint_across_http_json_sse_and_component_harness(
    app: FastAPI,
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.adapters import get_sut_http_transport

    raw_secret = "secret-java-bearer-9999"
    monkeypatch.setenv("EVAL_JAVA_BEARER_TOKEN", raw_secret)
    monkeypatch.setenv("ROUTING_EVAL_HARNESS_TOKEN", "harness-live-tok-8888")

    seen_auth_headers: dict[str, str] = {}

    def sut_handler(request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] = json.loads(request.content.decode("utf-8"))
        if request.url.path == "/ai/invoke":
            seen_auth_headers["invoke"] = request.headers.get("authorization", "")
            if body.get("query") == "TRIGGER_500":
                return httpx.Response(500, text="Upstream Java NPE")
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "answer": "商业贷款提取可通过常州公积金公众号办理。",
                        "references": [{"content": "商贷提取政策2026"}],
                        "finalAgentId": "cz_gjj_agent",
                        "selectedBranch": "department",
                        "finalRouteType": "AUTO_DEPT",
                        "requestId": "req_java_77",
                        "events": [{"stage": "done"}],
                    },
                },
            )
        if request.url.path == "/internal/eval-harness/replay":
            seen_auth_headers["harness"] = request.headers.get("x-eval-harness-token", "")
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "actual_output": "命中公积金智能体",
                        "references": ["目录快照片段A"],
                        "structured_decision": {
                            "route_type": "department",
                            "actual_agent_id": "cz_gjj_agent",
                            "usable": True,
                        },
                        "trace": {"elapsed_ms": 9, "cache_bypassed": True},
                    },
                },
            )
        if request.url.path == "/ai/sse-probe":
            sse_bytes = (
                'data: {"delta": "流式首段"}\n\n'
                'data: {"delta": "-流式末段"}\n\n'
                "data: [DONE]\n\n"
            ).encode("utf-8")
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=sse_bytes,
            )
        return httpx.Response(404)

    mock_sut_transport = httpx.MockTransport(sut_handler)
    app.dependency_overrides[get_sut_http_transport] = lambda: mock_sut_transport

    try:
        # 1. Seeded gov_e2e_routing_blocking test preview & secret masking
        e2e_test = await client.post(
            "/api/v1/adapters/gov_e2e_routing_blocking/test",
            headers=admin_headers,
            json={
                "input_text": "商贷提取怎么办理？",
                "sut_input_json": {"selected_region": "新北区"},
            },
        )
        assert e2e_test.status_code == 200
        e2e_data = e2e_test.json()["data"]
        assert seen_auth_headers["invoke"] == f"Bearer {raw_secret}"
        assert raw_secret not in json.dumps(e2e_data)
        assert e2e_data["request_preview"]["url"] == "http://127.0.0.1:8080/ai/invoke"
        assert e2e_data["request_preview"]["headers"]["Authorization"].startswith(
            "Bearer "
        )
        assert e2e_data["request_preview"]["body"] == {
            "responseMode": "blocking",
            "routeMode": "auto",
            "query": "商贷提取怎么办理？",
            "selectedRegion": "新北区",
        }
        assert e2e_data["actual_output"] == "商业贷款提取可通过常州公积金公众号办理。"
        assert e2e_data["actual_references"] == ["商贷提取政策2026"]
        assert e2e_data["actual_structured"] == {
            "actual_agent_id": "cz_gjj_agent",
            "selected_branch": "department",
            "final_route_type": "AUTO_DEPT",
            "request_id": "req_java_77",
        }
        assert e2e_data["sut_trace"] == [{"stage": "done"}]
        assert isinstance(e2e_data["sut_latency_ms"], int)
        assert e2e_data["ttft_ms"] is None

        # 2. Seeded gov_comp_routing_harness test preview & replay mapping
        comp_test = await client.post(
            "/api/v1/adapters/gov_comp_routing_harness/test",
            headers=admin_headers,
            json={
                "input_text": "公积金贷款额度怎么算？",
                "sut_input_json": {"component": "FastRouter"},
                "sut_context_json": {"agents": [{"agent_id": "cz_gjj_agent"}]},
                "run_overrides": {"confidence_threshold": 0.8},
            },
        )
        assert comp_test.status_code == 200
        comp_data = comp_test.json()["data"]
        assert seen_auth_headers["harness"] == "harness-live-tok-8888"
        assert comp_data["request_preview"]["body"] == {
            "component": "FastRouter",
            "query": "公积金贷款额度怎么算？",
            "turns": [],
            "sut_context": {"agents": [{"agent_id": "cz_gjj_agent"}]},
            "overrides": {"confidence_threshold": 0.8},
        }
        assert comp_data["actual_output"] == "命中公积金智能体"
        assert comp_data["actual_references"] == ["目录快照片段A"]
        assert comp_data["actual_structured"] == {
            "route_type": "department",
            "actual_agent_id": "cz_gjj_agent",
            "usable": True,
        }
        assert comp_data["sut_trace"] == {"elapsed_ms": 9, "cache_bypassed": True}

        # 3. Create and test an HTTP_SSE adapter
        await client.post(
            "/api/v1/adapters",
            headers=admin_headers,
            json={
                "adapter_code": "probe_sse_adapter",
                "adapter_name": "SSE 联调适配器",
                "eval_level": "E2E",
                "adapter_type": "HTTP_SSE",
                "endpoint_url": "http://127.0.0.1:8080/ai/sse-probe",
                "request_mapping_json": {"field_map": {"query": "$.input_text"}},
                "response_mapping_json": {"delta_path": "$.delta"},
            },
        )
        sse_test = await client.post(
            "/api/v1/adapters/probe_sse_adapter/test",
            headers=admin_headers,
            json={"input_text": "测试SSE流"},
        )
        assert sse_test.status_code == 200
        sse_data = sse_test.json()["data"]
        assert sse_data["actual_output"] == "流式首段-流式末段"
        assert sse_data["ttft_ms"] is not None and sse_data["ttft_ms"] >= 0

        # 4. Upstream 500 surfaces 502 envelope with request_preview; missing adapter -> 404
        err_test = await client.post(
            "/api/v1/adapters/gov_e2e_routing_blocking/test",
            headers=admin_headers,
            json={"input_text": "TRIGGER_500"},
        )
        assert err_test.status_code == 502
        assert err_test.json()["code"] == 502
        assert err_test.json()["data"]["request_preview"]["body"]["query"] == "TRIGGER_500"

        missing_test = await client.post(
            "/api/v1/adapters/no_such_adapter/test",
            headers=admin_headers,
            json={"input_text": "hello"},
        )
        assert missing_test.status_code == 404
    finally:
        app.dependency_overrides.pop(get_sut_http_transport, None)

