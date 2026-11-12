from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.db.models import SutAdapter
from app.db.seed import DEFAULT_ADAPTERS


def _seed_adapter_model(code: str) -> SutAdapter:
    spec = next(item for item in DEFAULT_ADAPTERS if item["adapter_code"] == code)
    return SutAdapter(**spec)


async def test_http_json_adapter_executes_seeded_e2e_templates_and_maps_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.adapters import SutExecutionInput, execute_sut_adapter

    monkeypatch.setenv("EVAL_JAVA_BEARER_TOKEN", "java-secret-jwt-9876")
    captured_requests: list[tuple[str, dict[str, str], dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured_requests.append((str(request.url), dict(request.headers), body))
        if request.url.path == "/ai/invoke":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "answer": "常州住房公积金咨询热线为12329，可线上刷脸办理。",
                        "references": [
                            {"docId": "d1", "content": "常州公积金热线：12329"},
                            {"docId": "d2", "content": "支持微信公众号线上零材料办理"},
                        ],
                        "finalAgentId": "cz_gjj_agent",
                        "selectedBranch": "department_agent",
                        "finalRouteType": "DEPARTMENT",
                        "requestId": "java_req_001",
                        "events": [
                            {"node": "FastRouter", "elapsedMs": 12},
                            {"node": "cz_gjj_agent", "elapsedMs": 340},
                        ],
                    },
                },
            )
        if request.url.path == "/ai/chat":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "answer": "您好，请问有什么可以帮您？",
                        "references": [{"content": "通用欢迎语知识库"}],
                        "sessionId": "sess_42",
                    },
                },
            )
        return httpx.Response(404, json={"message": "not found"})

    transport = httpx.MockTransport(handler)

    # 1. Seeded gov_e2e_routing_blocking: preserves template routeMode='auto' and omits None targetAgentId
    blocking_adapter = _seed_adapter_model("gov_e2e_routing_blocking")
    result = await execute_sut_adapter(
        blocking_adapter,
        SutExecutionInput(
            input_text="在常州怎么提取公积金还商贷？",
            sut_input_json={"selected_region": "天宁区"},
        ),
        transport=transport,
    )

    assert len(captured_requests) == 1
    req_url, req_headers, req_body = captured_requests[0]
    assert req_url == "http://127.0.0.1:8080/ai/invoke"
    assert req_headers["authorization"] == "Bearer java-secret-jwt-9876"
    assert req_body == {
        "responseMode": "blocking",
        "routeMode": "auto",
        "query": "在常州怎么提取公积金还商贷？",
        "selectedRegion": "天宁区",
    }
    assert "targetAgentId" not in req_body

    assert result.actual_output == "常州住房公积金咨询热线为12329，可线上刷脸办理。"
    assert result.actual_references == [
        "常州公积金热线：12329",
        "支持微信公众号线上零材料办理",
    ]
    assert result.actual_structured == {
        "actual_agent_id": "cz_gjj_agent",
        "selected_branch": "department_agent",
        "final_route_type": "DEPARTMENT",
        "request_id": "java_req_001",
    }
    assert result.sut_trace == [
        {"node": "FastRouter", "elapsedMs": 12},
        {"node": "cz_gjj_agent", "elapsedMs": 340},
    ]
    assert isinstance(result.sut_latency_ms, int) and result.sut_latency_ms >= 0
    assert result.ttft_ms is None

    # 2. Explicit sut_input_json overrides template default routeMode and populates optional targetAgentId
    await execute_sut_adapter(
        blocking_adapter,
        SutExecutionInput(
            input_text="指定智能体测试",
            sut_input_json={
                "selected_region": "武进区",
                "route_mode": "direct",
                "target_agent_id": "cz_shebao_agent",
            },
        ),
        transport=transport,
    )
    _, _, override_body = captured_requests[1]
    assert override_body == {
        "responseMode": "blocking",
        "routeMode": "direct",
        "query": "指定智能体测试",
        "selectedRegion": "武进区",
        "targetAgentId": "cz_shebao_agent",
    }

    # 3. Seeded gov_e2e_normal_chat + direct token auth config
    chat_adapter = _seed_adapter_model("gov_e2e_normal_chat")
    chat_adapter.auth_config_json = {
        "header": "X-Custom-Auth",
        "prefix": "Token ",
        "token": "direct-token-123",
    }
    chat_result = await execute_sut_adapter(
        chat_adapter,
        {"input_text": "你好", "sut_input_json": {"app_code": "gov_portal"}},
        transport=transport,
    )
    chat_url, chat_headers, chat_body = captured_requests[2]
    assert chat_url == "http://127.0.0.1:8080/ai/chat"
    assert chat_headers["x-custom-auth"] == "Token direct-token-123"
    assert chat_body == {"query": "你好", "appCode": "gov_portal"}
    assert chat_result.actual_output == "您好，请问有什么可以帮您？"
    assert chat_result.actual_references == ["通用欢迎语知识库"]
    assert chat_result.actual_structured == {}
    assert chat_result.sut_trace == {
        "answer": "您好，请问有什么可以帮您？",
        "references": [{"content": "通用欢迎语知识库"}],
        "sessionId": "sess_42",
    }


async def test_http_sse_adapter_streams_chunks_measures_ttft_and_extracts_final_metadata() -> None:
    import asyncio
    from collections.abc import AsyncIterator
    from app.adapters import SutExecutionInput, execute_sut_adapter

    class TimedSseStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            # Keep-alive comment and empty role chunk arrive immediately; must NOT set ttft_ms
            yield b": keep-alive\n\n"
            yield b'data: {"role": "assistant", "delta": ""}\n\n'
            await asyncio.sleep(0.045)
            yield 'data: {"delta": "您可携带身份证"}\n\n'.encode("utf-8")
            yield 'data: {"delta": "在市民中心办理。"}\n\n'.encode("utf-8")
            await asyncio.sleep(0.045)
            final_meta = {
                "data": {
                    "references": [{"content": "政务大厅办事指南第2条"}],
                    "finalAgentId": "cz_hall_agent",
                    "events": [{"step": "stream_end", "tokens": 14}],
                }
            }
            yield f"data: {json.dumps(final_meta, ensure_ascii=False)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ai/stream":
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                stream=TimedSseStream(),
            )
        if request.url.path == "/v1/chat/completions":
            openai_sse = (
                'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"公积金"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"热线12329"}}]}\n\n'
                "data: [DONE]\n\n"
            ).encode("utf-8")
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=openai_sse,
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    sse_adapter = SutAdapter(
        adapter_code="gov_e2e_routing_sse",
        adapter_name="政务智能客服流式入口",
        eval_level="E2E",
        adapter_type="HTTP_SSE",
        endpoint_url="http://127.0.0.1:8080/ai/stream",
        request_mapping_json={
            "body_template": {"responseMode": "streaming"},
            "field_map": {"query": "$.input_text"},
        },
        response_mapping_json={
            "actual_output": "$.data.answer",
            "actual_references": "$.data.references[*].content",
            "actual_structured": {"actual_agent_id": "$.data.finalAgentId"},
            "sut_trace": "$.data.events",
        },
    )

    res = await execute_sut_adapter(
        sse_adapter,
        SutExecutionInput(input_text="线下怎么办理？"),
        transport=transport,
    )

    assert res.actual_output == "您可携带身份证在市民中心办理。"
    assert res.actual_references == ["政务大厅办事指南第2条"]
    assert res.actual_structured == {"actual_agent_id": "cz_hall_agent"}
    assert res.sut_trace == [{"step": "stream_end", "tokens": 14}]
    assert res.ttft_ms is not None
    assert res.ttft_ms >= 30
    assert res.sut_latency_ms >= 75
    assert res.ttft_ms < res.sut_latency_ms

    # OpenAI-style explicit delta_path
    openai_sse_adapter = SutAdapter(
        adapter_code="openai_sse_chat",
        adapter_name="OpenAI Compatible SSE",
        eval_level="E2E",
        adapter_type="HTTP_SSE",
        endpoint_url="http://127.0.0.1:8080/v1/chat/completions",
        request_mapping_json={
            "body_template": {"stream": True},
            "field_map": {"prompt": "$.input_text"},
        },
        response_mapping_json={
            "delta_path": "$.choices[0].delta.content",
            "actual_structured": {},
        },
    )
    openai_res = await execute_sut_adapter(
        openai_sse_adapter,
        SutExecutionInput(input_text="热线电话是多少？"),
        transport=transport,
    )
    assert openai_res.actual_output == "公积金热线12329"
    assert openai_res.ttft_ms is not None and openai_res.ttft_ms >= 0


async def test_component_harness_adapter_records_snapshot_and_replays_isolated_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.adapters import (
        ComponentRecordInput,
        SutExecutionInput,
        execute_sut_adapter,
        record_component_snapshot,
    )

    monkeypatch.setenv("ROUTING_EVAL_HARNESS_TOKEN", "harness-token-2026")
    captured: list[tuple[str, dict[str, str], dict[str, Any]]] = []

    frozen_sut_context = {
        "agents": [
            {"agent_id": "cz_gjj_agent", "name": "公积金专员"},
            {"agent_id": "cz_shebao_agent", "name": "社保专员"},
        ],
        "candidate_responses": [
            {"agent_id": "cz_gjj_agent", "answer": "可通过常州公积金公众号刷脸提取。"}
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        captured.append((str(request.url), dict(request.headers), payload))
        if request.url.path == "/internal/eval-harness/record":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "sut_context": frozen_sut_context,
                        "reference_output": "线上可通过常州公积金公众号刷脸提取。",
                    },
                },
            )
        if request.url.path == "/internal/eval-harness/replay":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "actual_output": "可通过常州公积金公众号刷脸提取。",
                        "references": ["常州公积金商贷提取指南v2"],
                        "structured_decision": {
                            "route_type": "department",
                            "actual_agent_id": "cz_gjj_agent",
                            "selected_agent_ids": ["cz_gjj_agent"],
                            "usable": True,
                        },
                        "trace": {"elapsed_ms": 18, "cache_bypassed": True},
                    },
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    harness_adapter = _seed_adapter_model("gov_comp_routing_harness")

    # Step 1: Record component snapshot
    rec_result = await record_component_snapshot(
        harness_adapter,
        ComponentRecordInput(
            input_text="在常州提取公积金偿还商业贷款需要哪些材料？",
            conversation_turns_json=[{"role": "user", "content": "你好"}],
            sut_input_json={"selected_region": "天宁区"},
        ),
        transport=transport,
    )
    assert len(captured) == 1
    rec_url, rec_headers, rec_body = captured[0]
    assert rec_url == "http://127.0.0.1:8000/internal/eval-harness/record"
    assert rec_headers["x-eval-harness-token"] == "harness-token-2026"
    assert rec_body == {
        "query": "在常州提取公积金偿还商业贷款需要哪些材料？",
        "selected_region": "天宁区",
        "turns": [{"role": "user", "content": "你好"}],
    }
    assert rec_result.sut_context == frozen_sut_context
    assert rec_result.reference_output == "线上可通过常州公积金公众号刷脸提取。"
    assert rec_result.raw_response["code"] == 200

    # Step 2: Isolated replay with frozen sut_context and run_overrides
    replay_result = await execute_sut_adapter(
        harness_adapter,
        SutExecutionInput(
            input_text="在常州提取公积金偿还商业贷款需要哪些材料？",
            sut_input_json={"component": "SupervisorRouter"},
            sut_context_json=rec_result.sut_context,
        ),
        run_overrides={"supervisor_prompt": None, "confidence_threshold": 0.72},
        transport=transport,
    )
    assert len(captured) == 2
    rep_url, rep_headers, rep_body = captured[1]
    assert rep_url == "http://127.0.0.1:8000/internal/eval-harness/replay"
    assert rep_headers["x-eval-harness-token"] == "harness-token-2026"
    assert rep_body == {
        "component": "SupervisorRouter",
        "query": "在常州提取公积金偿还商业贷款需要哪些材料？",
        "turns": [],
        "sut_context": frozen_sut_context,
        "overrides": {"supervisor_prompt": None, "confidence_threshold": 0.72},
    }
    assert replay_result.actual_output == "可通过常州公积金公众号刷脸提取。"
    assert replay_result.actual_references == ["常州公积金商贷提取指南v2"]
    assert replay_result.actual_structured == {
        "route_type": "department",
        "actual_agent_id": "cz_gjj_agent",
        "selected_agent_ids": ["cz_gjj_agent"],
        "usable": True,
    }
    assert replay_result.sut_trace == {"elapsed_ms": 18, "cache_bypassed": True}
    assert replay_result.ttft_ms is None
    assert replay_result.sut_latency_ms >= 0


async def test_adapter_orm_eval_case_input_custom_record_mapping_network_policy_and_errors() -> None:
    from app.adapters import (
        SutAdapterConfig,
        SutExecutionError,
        execute_sut_adapter,
        record_component_snapshot,
        resolve_sut_network_policy,
    )
    from app.db.models import EvalCase

    eval_case = EvalCase(
        dataset_id=1,
        case_code="CASE_COMP_01",
        dataset_split="DEV",
        input_text="企业开办需要几个工作日？",
        conversation_turns_json=[{"role": "user", "content": "我想开公司"}],
        sut_input_json={"tenant_id": "t_changzhou", "component": "FastRouter"},
    )

    captured_bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/custom/record":
            captured_bodies.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(
                200,
                json={
                    "sut_context": {"candidates": ["biz_license_agent"]},
                    "answer": "企业开办全流程1个工作日办结。",
                },
            )
        return httpx.Response(503, text="Service Unavailable")

    transport = httpx.MockTransport(handler)

    custom_harness = SutAdapter(
        adapter_code="generic_comp_harness",
        adapter_name="Generic Domain-Agnostic Harness",
        eval_level="COMPONENT",
        adapter_type="COMPONENT_HARNESS",
        endpoint_url="https://harness.example.com/custom/replay",
        record_endpoint_url="https://harness.example.com/custom/record",
        request_mapping_json={
            "record_body_template": {"mode": "freeze"},
            "record_field_map": {
                "prompt": "$.input_text",
                "tenant": "$.sut_input_json.tenant_id",
                "history": "$.conversation_turns_json",
            },
        },
        response_mapping_json={"actual_output": "$.output"},
    )

    # Network policy: loopback -> DIRECT, external -> PROXY_FALLBACK, explicit override -> DIRECT
    local_cfg = SutAdapterConfig.from_adapter(_seed_adapter_model("gov_e2e_routing_blocking"))
    assert resolve_sut_network_policy(local_cfg.endpoint_url, local_cfg) == ("DIRECT", None)

    remote_cfg = SutAdapterConfig.from_adapter(custom_harness)
    assert resolve_sut_network_policy(remote_cfg.endpoint_url, remote_cfg) == (
        "PROXY_FALLBACK",
        None,
    )

    custom_harness.request_mapping_json["network_mode"] = "DIRECT"
    override_cfg = SutAdapterConfig.from_adapter(custom_harness)
    assert resolve_sut_network_policy(override_cfg.endpoint_url, override_cfg) == (
        "DIRECT",
        None,
    )

    # Custom record_field_map + EvalCase ORM instance + top-level sut_context response
    rec = await record_component_snapshot(custom_harness, eval_case, transport=transport)
    assert captured_bodies[0] == {
        "mode": "freeze",
        "prompt": "企业开办需要几个工作日？",
        "tenant": "t_changzhou",
        "history": [{"role": "user", "content": "我想开公司"}],
    }
    assert rec.sut_context == {"candidates": ["biz_license_agent"]}
    assert rec.reference_output == "企业开办全流程1个工作日办结。"

    # Upstream 503 raises SutExecutionError with status_code and request_preview
    with pytest.raises(SutExecutionError) as exc_info:
        await execute_sut_adapter(custom_harness, eval_case, transport=transport)
    assert exc_info.value.status_code == 503
    assert exc_info.value.request_preview is not None

    # Missing record_endpoint_url raises SutExecutionError
    custom_harness.record_endpoint_url = None
    with pytest.raises(SutExecutionError):
        await record_component_snapshot(custom_harness, eval_case, transport=transport)

    # Misconfigured CUSTOM_PROXY without custom_proxy_url raises SutExecutionError cleanly
    bad_proxy_adapter = _seed_adapter_model("gov_e2e_normal_chat")
    bad_proxy_adapter.request_mapping_json = {
        "network_mode": "CUSTOM_PROXY",
        "field_map": {"query": "$.input_text"},
    }
    with pytest.raises(SutExecutionError):
        await execute_sut_adapter(bad_proxy_adapter, eval_case, transport=None)




