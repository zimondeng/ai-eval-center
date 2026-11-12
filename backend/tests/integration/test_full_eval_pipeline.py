from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.api.adapters import get_sut_http_transport
from app.api.datasets import get_desensitize_http_transport
from app.api.providers import get_provider_http_transport
from app.common.crypto import sha256_hex
from app.db.base import utcnow_naive
from app.db.models import ApiKey


async def test_full_eval_pipeline_e2e_and_component_closed_loops(
    app: FastAPI,
    client: httpx.AsyncClient,
    db_session: Session,
    admin_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """完整端到端闭环验证 (docs/01 §7 & §9):
    采集脱敏 -> 快照录制与真值校准 -> E2E(SSE)/Component 基线跑批 ->
    候选版本语义回归(含 WAF 403 剥代码块自愈 + 9题单次合批) ->
    跨模型标尺告警对比 -> 零成本重算与 DEV 专属阈值寻优。
    """
    monkeypatch.setenv("EVAL_JAVA_BEARER_TOKEN", "java-e2e-token-2026")
    monkeypatch.setenv("ROUTING_EVAL_HARNESS_TOKEN", "py-harness-token-2026")
    monkeypatch.setenv("EMRANK_API_KEY", "sk-emrank-relay-test-key")

    # Provision CI API Key (evk_*)
    raw_ci_key = "evk_live_full_pipeline_ci_bot_888"
    db_session.add(
        ApiKey(
            key_name="pipeline-ci-bot",
            key_prefix=raw_ci_key[:13],
            key_sha256=sha256_hex(raw_ci_key),
            status="ACTIVE",
            expires_at=utcnow_naive() + timedelta(days=7),
        )
    )
    db_session.commit()
    ci_headers = {"X-API-Key": raw_ci_key}

    # =========================================================================
    # PHASE 1: Online Corpus Sampling + Desensitization Center + Curation
    # =========================================================================
    src_db_path = tmp_path / "gov_online_readonly.db"
    conn = sqlite3.connect(src_db_path)
    conn.execute(
        """
        CREATE TABLE gov_routing_conversation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            input_text TEXT NOT NULL,
            answer_text TEXT,
            selected_region TEXT,
            mining_signal TEXT NOT NULL,
            create_time TEXT NOT NULL
        )
        """
    )
    raw_phone = "13988887777"
    raw_id_card = "320402199105056677"
    conn.executemany(
        """
        INSERT INTO gov_routing_conversation (
            request_id, input_text, answer_text, selected_region, mining_signal, create_time
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "online_req_1",
                f"本人手机{raw_phone}，常州双职工住房公积金最高能贷多少万？",
                "双缴存职工最高贷款额度 120 万元，咨询热线 12329。",
                "天宁区",
                "USER_DISLIKE",
                "2026-11-05T10:00:00",
            ),
            (
                "online_req_2",
                f"身份证{raw_id_card}异地购房提取常州公积金怎么办理？",
                "可通过全国住房公积金小程序线上跨省通办，热线 12329。",
                "武进区",
                "CITY_BACKUP",
                "2026-11-06T11:00:00",
            ),
            (
                "online_req_3",
                "常州住房公积金官方服务热线是多少？",
                "常州市住房公积金服务热线为 12329。",
                "钟楼区",
                "STRATIFIED_SAMPLE",
                "2026-11-07T14:00:00",
            ),
        ],
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("GOV_ONLINE_READONLY_DSN", f"sqlite:///{src_db_path.as_posix()}")

    state_tracker: dict[str, Any] = {
        "sut_version": "v1_baseline",
        "system_one_calls": [],
        "waf_403_triggered": False,
        "harness_replay_overrides": [],
    }

    def unified_mock_handler(req: httpx.Request) -> httpx.Response:
        url_str = str(req.url)
        path = req.url.path

        # 1) Data Desensitization Center API
        if path == "/api/v1/mask/batch-inline":
            body = json.loads(req.content.decode("utf-8"))
            masked_items = []
            for item in body["items"]:
                txt = item["text"]
                counts: dict[str, int] = {}
                if raw_phone in txt:
                    counts["PHONE"] = 1
                if raw_id_card in txt:
                    counts["ID_CARD"] = 1
                masked = txt.replace(raw_phone, "[PHONE]").replace(raw_id_card, "[ID_CARD]")
                masked_items.append(
                    {"id": item["id"], "masked_text": masked, "tag_counts": counts}
                )
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "policy_snapshot": "gov_eval_corpus:v2",
                        "items": masked_items,
                    },
                },
            )

        # 2) E2E SSE Streaming SUT (/ai/invoke-sse)
        if path == "/ai/invoke-sse":
            body = json.loads(req.content.decode("utf-8"))
            q = body.get("query", "")
            ver = state_tracker["sut_version"]
            if "最高能贷" in q:
                if ver == "v1_baseline":
                    part1, part2 = "常州双缴存职工最高可贷 120 万元，", "咨询热线 12329。"
                else:
                    # Candidate introduces factual error + fenced shell snippet that trips WAF once!
                    part1 = "最高只能贷 45 万元。\n```bash\ncurl -s http://internal/calc\n```\n"
                    part2 = "详情电询 12329。"
            elif "异地购房" in q:
                if ver == "v1_baseline":
                    part1, part2 = "可通过全国住房公积金小程序跨省通办，", "咨询电话 12329。"
                else:
                    part1, part2 = "只能本人前往常州线下网点柜台提交纸质申请，", "咨询电话 12329。"
            else:
                part1, part2 = "常州市住房公积金服务热线为 12329，", "欢迎随时拨打咨询。"

            sse_lines = [
                f"data: {json.dumps({'event': 'delta', 'answer': part1}, ensure_ascii=False)}\n\n",
                f"data: {json.dumps({'event': 'delta', 'answer': part2}, ensure_ascii=False)}\n\n",
                "data: "
                + json.dumps(
                    {
                        "event": "done",
                        "references": [
                            {
                                "content": (
                                    "常州住房公积金政策：双职工最高可贷 120 万元；"
                                    "异地购房支持全国住房公积金小程序线上跨省通办；官方热线 12329。"
                                )
                            }
                        ],
                        "finalAgentId": "cz_gjj_agent",
                        "routeType": "department",
                    },
                    ensure_ascii=False,
                )
                + "\n\n",
                "data: [DONE]\n\n",
            ]
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content="".join(sse_lines).encode("utf-8"),
            )

        # 3) Python Component Harness Record & Replay
        if path == "/internal/eval-harness/record":
            assert req.headers.get("x-eval-harness-token") == "py-harness-token-2026"
            body = json.loads(req.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "sut_context": {
                            "agents": [
                                {"agent_id": "cz_gjj_agent", "name": "公积金智能体"},
                                {"agent_id": "cz_yb_agent", "name": "医保智能体"},
                            ],
                            "candidate_responses": [
                                {"agent_id": "cz_gjj_agent", "answer": "公积金热线 12329"}
                            ],
                            "region": body.get("selected_region"),
                        },
                        "reference_output": "公积金热线 12329",
                    },
                },
            )

        if path == "/internal/eval-harness/replay":
            assert req.headers.get("x-eval-harness-token") == "py-harness-token-2026"
            body = json.loads(req.content.decode("utf-8"))
            overrides = body.get("overrides") or {}
            state_tracker["harness_replay_overrides"].append(overrides)
            q = body.get("query", "")
            is_weak_case = "边缘模糊" in q
            chosen_agent = "cz_gjj_agent"
            ans = (
                "疑似可走普通窗口，不太确定。"
                if is_weak_case
                else "已命中常州公积金智能体，官方热线 12329。"
            )
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "actual_output": ans,
                        "references": ["常州公积金智能体知识库：热线 12329。"],
                        "structured_decision": {
                            "route_type": "department",
                            "actual_agent_id": chosen_agent,
                            "selected_agent_ids": [chosen_agent],
                            "usable": not is_weak_case,
                        },
                        "trace": {"elapsed_ms": 14, "cache_bypassed": True},
                    },
                },
            )

        # 4) System One Decision Endpoints (Ollama /v1/systemone & EMRank /v1/decisions)
        if path in ("/v1/systemone", "/v1/decisions"):
            body = json.loads(req.content.decode("utf-8"))
            state = body["state"]
            questions = body["questions"]

            # Simulate EMRank edge WAF HTML 403 when fenced bash block is present
            actual_txt = str(state.get("actual_output") or state.get("current_output") or "")
            if path == "/v1/decisions" and "```bash" in actual_txt:
                state_tracker["waf_403_triggered"] = True
                return httpx.Response(
                    403,
                    headers={"Content-Type": "text/html"},
                    text="<html><body><h1>403 Forbidden - Edge WAF</h1></body></html>",
                )

            state_tracker["system_one_calls"].append(
                {"url": url_str, "model": body["model"], "num_questions": len(questions)}
            )

            # Semantic regression 4-way classification
            if "semantic_regression_v1__diff" in questions:
                inp = state.get("input", "")
                if "最高能贷" in inp:
                    probs = {"SAME": 0.03, "FACT_DIFFERS": 0.89, "ACTION_DIFFERS": 0.05, "SPECIFICITY_DIFFERS": 0.03}
                elif "异地购房" in inp:
                    probs = {"SAME": 0.04, "FACT_DIFFERS": 0.08, "ACTION_DIFFERS": 0.83, "SPECIFICITY_DIFFERS": 0.05}
                else:
                    probs = {"SAME": 0.94, "FACT_DIFFERS": 0.02, "ACTION_DIFFERS": 0.02, "SPECIFICITY_DIFFERS": 0.02}
                return httpx.Response(
                    200,
                    headers={"X-Request-ID": "emrank_reg_req_01"},
                    json={
                        "answers": {"semantic_regression_v1__diff": {"probabilities": probs}},
                        "usage": {"input_tokens": 220, "output_tokens": 12},
                    },
                )

            bad = ("45 万元" in actual_txt) or ("纸质申请" in actual_txt) or ("不太确定" in actual_txt)
            p_good = 0.35 if bad else 0.92
            answers: dict[str, Any] = {}
            for q_key, q_spec in questions.items():
                qt = q_spec["type"]
                if qt == "noul":
                    answers[q_key] = {"noul": p_good}
                elif qt == "score":
                    k = len(q_spec["criteria"])
                    probs_arr = [round(1.0 - p_good, 6)] + [0.0] * (k - 2) + [p_good]
                    answers[q_key] = {"probabilities": probs_arr}
                elif qt == "choice":
                    opts = list(q_spec["criteria"].keys())
                    pm = {opts[0]: round(1.0 - p_good, 6), opts[len(opts) - 2]: p_good}
                    for o in opts:
                        pm.setdefault(o, 0.0)
                    answers[q_key] = {"probabilities": pm}
            return httpx.Response(
                200,
                headers={"X-Request-ID": "s1_batch_req_77"},
                json={
                    "answers": answers,
                    "usage": {"input_tokens": 400, "output_tokens": 25},
                },
            )

        raise AssertionError(f"Unhandled mock URL: {url_str}")

    mock_transport = httpx.MockTransport(unified_mock_handler)
    app.dependency_overrides[get_desensitize_http_transport] = lambda: mock_transport
    app.dependency_overrides[get_sut_http_transport] = lambda: mock_transport
    app.dependency_overrides[get_provider_http_transport] = lambda: mock_transport

    try:
        # 1.1 Create E2E dataset & ingest from MySQL/SQLite through desensitization center
        e2e_ds_id = (
            await client.post(
                "/api/v1/datasets",
                headers=admin_headers,
                json={
                    "dataset_code": "gov_e2e_closed_loop",
                    "version_tag": "v1.0",
                    "dataset_name": "政务客服全链路闭环验收测试集",
                    "eval_level": "E2E",
                },
            )
        ).json()["data"]["id"]

        ingest_resp = await client.post(
            f"/api/v1/datasets/{e2e_ds_id}/ingest-from-mysql",
            headers=ci_headers,
            json={
                "source_dsn_env": "GOV_ONLINE_READONLY_DSN",
                "preset_extractor": "gov_routing_conversation",
                "filter": {
                    "start_time": "2026-11-01T00:00:00",
                    "end_time": "2026-11-10T00:00:00",
                    "mining_signals": ["USER_DISLIKE", "CITY_BACKUP", "STRATIFIED_SAMPLE"],
                    "limit": 50,
                },
                "desensitize_policy_code": "gov_eval_corpus",
                "dev_ratio": 1.0,
            },
        )
        assert ingest_resp.status_code == 200
        assert ingest_resp.json()["data"]["ingested_count"] == 3

        # Verify PII stripped and curate all 3 cases to APPROVED in DEV
        raw_cases = (
            await client.get(f"/api/v1/datasets/{e2e_ds_id}/cases", headers=admin_headers)
        ).json()["data"]["items"]
        serialized_dump = json.dumps(raw_cases, ensure_ascii=False)
        assert raw_phone not in serialized_dump
        assert raw_id_card not in serialized_dump

        for c in raw_cases:
            cur_resp = await client.put(
                f"/api/v1/cases/{c['id']}/curate",
                headers=admin_headers,
                json={
                    "dataset_split": "DEV",
                    "review_status": "APPROVED",
                    "expected_structured_json": {"expected_agent_ids": ["cz_gjj_agent"]},
                },
            )
            assert cur_resp.status_code == 200

        # =====================================================================
        # PHASE 2: 3 Shared-State Dimensions (9 Atomic Questions -> 1 Batch Call)
        #          + SSE Streaming Adapter (TTFT capture)
        # =====================================================================
        sse_adapter_resp = await client.post(
            "/api/v1/adapters",
            headers=admin_headers,
            json={
                "adapter_code": "gov_e2e_routing_sse",
                "adapter_name": "政务智能路由 SSE 流式入口",
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
                    "chunk_text_path": "$.answer",
                    "actual_references": "$.references[*].content",
                    "actual_structured": {"actual_agent_id": "$.finalAgentId"},
                },
            },
        )
        assert sse_adapter_resp.status_code == 200

        # Create two more 3/4-question dimensions sharing ["input", "actual_output", "references"]
        # Alongside rag_faithfulness_s1 (2 questions): 2 + 3 + 4 = 9 questions -> 1 HTTP call/case!
        dim_rel_resp = await client.post(
            "/api/v1/dimensions",
            headers=admin_headers,
            json={
                "dimension_code": "answer_relevancy_3q",
                "dimension_name": "回答相关性 (3题)",
                "category": "RELEVANCY",
                "target_turn_mode": "SINGLE_TURN",
                "evaluator_kind": "SYSTEM_ONE",
                "state_fields_json": ["input", "actual_output", "references"],
                "questions_json": [
                    {"key": "direct_ans", "type": "noul", "weight": 1.0, "instructions": "actual_output 直接回应了 input 的核心诉求。"},
                    {"key": "no_offtopic", "type": "noul", "weight": 1.0, "instructions": "actual_output 未偏离 input 所涉业务主题。"},
                    {"key": "ref_used", "type": "noul", "weight": 1.0, "instructions": "actual_output 合理利用了 references 中的有效信息。"},
                ],
                "default_threshold": 0.75,
                "default_weight": 1.5,
                "status": "ACTIVE",
            },
        )
        assert dim_rel_resp.status_code == 200

        dim_clarity_resp = await client.post(
            "/api/v1/dimensions",
            headers=admin_headers,
            json={
                "dimension_code": "service_clarity_4q",
                "dimension_name": "政务表达清晰度 (4题)",
                "category": "ACCURACY",
                "target_turn_mode": "SINGLE_TURN",
                "evaluator_kind": "SYSTEM_ONE",
                "state_fields_json": ["references", "actual_output", "input"],
                "questions_json": [
                    {"key": "clear_struct", "type": "noul", "weight": 1.0, "instructions": "actual_output 对 input 的答复条理清晰。"},
                    {"key": "polite_tone", "type": "noul", "weight": 1.0, "instructions": "actual_output 语气规范客观。"},
                    {"key": "concise_expr", "type": "noul", "weight": 1.0, "instructions": "actual_output 无冗余重复套话。"},
                    {"key": "consistent_ref", "type": "noul", "weight": 1.0, "instructions": "actual_output 表述与 references 术语保持一致。"},
                ],
                "default_threshold": 0.75,
                "default_weight": 1.0,
                "status": "ACTIVE",
            },
        )
        assert dim_clarity_resp.status_code == 200

        suite_9q_resp = await client.post(
            "/api/v1/suites",
            headers=admin_headers,
            json={
                "suite_code": "suite_e2e_9q_merged",
                "suite_name": "9题同上下文单次合批验收套件",
                "eval_level": "E2E",
                "pass_policy": "ALL_BLOCKERS_AND_WEIGHTED",
                "suite_pass_threshold": 0.75,
                "confidence_review_floor": 0.50,
                "enable_semantic_regression": True,
                "dimensions_json": [
                    {"dimension_code": "route_top1_match", "version": 1, "weight": 1.0, "threshold": 1.0, "is_blocker": True},
                    {"dimension_code": "rag_faithfulness_s1", "version": 1, "weight": 2.0, "threshold": 0.75, "is_blocker": True},
                    {"dimension_code": "answer_relevancy_3q", "version": 1, "weight": 1.5, "threshold": 0.75, "is_blocker": False},
                    {"dimension_code": "service_clarity_4q", "version": 1, "weight": 1.0, "threshold": 0.75, "is_blocker": False},
                ],
            },
        )
        assert suite_9q_resp.status_code == 200

        # =====================================================================
        # PHASE 3: Baseline Run (Ollama tev1:0.8b) + Candidate Run (EMRank Jev)
        # =====================================================================
        state_tracker["system_one_calls"].clear()
        base_run = (
            await client.post(
                "/api/v1/runs",
                headers=ci_headers,
                json={
                    "run_name": "E2E基线跑批-本地tev1",
                    "suite_code": "suite_e2e_9q_merged",
                    "dataset_id": e2e_ds_id,
                    "dataset_split_filter": "DEV",
                    "sut_adapter_code": "gov_e2e_routing_sse",
                    "provider_code": "ollama_local_tev1",
                    "sut_fingerprint": {"git_sha": "v1_base"},
                    "run_sync": True,
                },
            )
        ).json()["data"]

        # Criterion §9.2: 3 cases * 9 System One questions = 27 unbatched questions merged into strictly 3 HTTP calls!
        assert base_run["status"] == "COMPLETED"
        assert base_run["total_cases"] == 3
        assert base_run["pass_rate"] == pytest.approx(1.0)
        assert base_run["p50_ttft_ms"] is not None
        assert base_run["unbatched_question_count"] == 27
        assert base_run["total_decision_calls"] == 3
        assert len(state_tracker["system_one_calls"]) == 3
        assert all(c["num_questions"] == 9 for c in state_tracker["system_one_calls"])

        # Candidate run on EMRank with semantic regression + WAF 403 fenced-code auto-heal
        state_tracker["sut_version"] = "v2_candidate"
        cand_run = (
            await client.post(
                "/api/v1/runs",
                headers=ci_headers,
                json={
                    "run_name": "E2E候选跑批-EMRank跨模型回归",
                    "suite_code": "suite_e2e_9q_merged",
                    "dataset_id": e2e_ds_id,
                    "dataset_split_filter": "DEV",
                    "sut_adapter_code": "gov_e2e_routing_sse",
                    "provider_code": "emrank_decisions_relay",
                    "model_override": "typesafe/jev-1.14.0",
                    "baseline_run_id": base_run["id"],
                    "sut_fingerprint": {"git_sha": "v2_cand"},
                    "run_sync": True,
                },
            )
        ).json()["data"]

        assert state_tracker["waf_403_triggered"] is True
        assert cand_run["status"] == "COMPLETED"
        assert cand_run["passed_cases"] == 1
        assert cand_run["failed_cases"] == 2
        assert cand_run["regression_summary_json"] == {
            "SAME": 1,
            "FACT_DIFFERS": 1,
            "ACTION_DIFFERS": 1,
            "SPECIFICITY_DIFFERS": 0,
            "total_compared": 3,
        }

        # Verify WAF fallback flag recorded on ProbabilityLedger in /cases
        cand_cases = (
            await client.get(
                f"/api/v1/runs/{cand_run['run_no']}/cases",
                params={"regression_tag": "FACT_DIFFERS"},
                headers=admin_headers,
            )
        ).json()["data"]["items"]
        assert len(cand_cases) == 1
        assert any(l["waf_fallback_used"] is True for l in cand_cases[0]["ledgers"])

        # Dual-run comparison: cross_model_warning MUST be True & FACT_DIFFERS ordered first
        cmp_data = (
            await client.get(
                f"/api/v1/runs/{cand_run['run_no']}/compare/{base_run['run_no']}",
                headers=admin_headers,
            )
        ).json()["data"]
        assert cmp_data["cross_model_warning"] is True
        assert [d["regression_tag"] for d in cmp_data["case_diffs"]] == [
            "FACT_DIFFERS",
            "ACTION_DIFFERS",
            "SAME",
        ]
        assert cmp_data["kpi_deltas"]["delta_pass_rate"] < 0.0

        # =====================================================================
        # PHASE 4: Zero-Cost In-Memory Recalculate (0 External HTTP Calls)
        # =====================================================================
        def zero_external_calls_guard(req: httpx.Request) -> httpx.Response:
            raise AssertionError(f"Zero external calls permitted! Unexpected {req.url}")

        guard_transport = httpx.MockTransport(zero_external_calls_guard)
        app.dependency_overrides[get_sut_http_transport] = lambda: guard_transport
        app.dependency_overrides[get_provider_http_transport] = lambda: guard_transport

        recalc_resp = await client.post(
            f"/api/v1/runs/{cand_run['run_no']}/recalculate",
            headers=admin_headers,
            json={
                "suite_pass_threshold": 0.30,
                "dimension_overrides": {
                    "rag_faithfulness_s1": {"threshold": 0.30, "is_blocker": False},
                },
            },
        )
        assert recalc_resp.status_code == 200
        recalc = recalc_resp.json()["data"]
        assert recalc["recalculated_pass_rate"] == pytest.approx(1.0)
        assert len(recalc["flipped_case_ids"]) == 2
        assert recalc["compute_elapsed_ms"] < 100

        flipped_csv = ",".join(str(cid) for cid in recalc["flipped_case_ids"])
        flipped_cases = (
            await client.get(
                f"/api/v1/runs/{cand_run['run_no']}/cases",
                params={"case_ids": flipped_csv},
                headers=admin_headers,
            )
        ).json()["data"]
        assert flipped_cases["total"] == 2

        # =====================================================================
        # PHASE 5: Component Harness Record -> Replay -> DEV Optimize vs HOLDOUT Guard
        # =====================================================================
        app.dependency_overrides[get_sut_http_transport] = lambda: mock_transport
        app.dependency_overrides[get_provider_http_transport] = lambda: mock_transport

        comp_ds_id = (
            await client.post(
                "/api/v1/datasets",
                headers=admin_headers,
                json={
                    "dataset_code": "gov_comp_closed_loop",
                    "version_tag": "v1.0",
                    "dataset_name": "政务路由组件隔离回放调优集",
                    "eval_level": "COMPONENT",
                },
            )
        ).json()["data"]["id"]

        imp_resp = await client.post(
            f"/api/v1/datasets/{comp_ds_id}/import",
            headers=admin_headers,
            json={
                "cases": [
                    {
                        "case_code": "comp_dev_good",
                        "dataset_split": "DEV",
                        "input_text": "常州住房公积金提取问题",
                        "sut_input_json": {"component": "SupervisorRouter", "selected_region": "天宁区"},
                        "expected_structured_json": {"expected_agent_ids": ["cz_gjj_agent"], "expected_pass": True},
                        "pii_masked": 1,
                    },
                    {
                        "case_code": "comp_dev_weak",
                        "dataset_split": "DEV",
                        "input_text": "边缘模糊疑难跨部门咨询",
                        "sut_input_json": {"component": "SupervisorRouter", "selected_region": "钟楼区"},
                        "expected_structured_json": {"expected_agent_ids": ["cz_gjj_agent"], "expected_pass": False},
                        "pii_masked": 1,
                    },
                    {
                        "case_code": "comp_holdout_verify",
                        "dataset_split": "HOLDOUT",
                        "input_text": "常州住房公积金贷款额度咨询",
                        "sut_input_json": {"component": "SupervisorRouter", "selected_region": "武进区"},
                        "expected_structured_json": {"expected_agent_ids": ["cz_gjj_agent"], "expected_pass": True},
                        "pii_masked": 1,
                    },
                ]
            },
        )
        assert imp_resp.status_code == 200

        # Record frozen component snapshots
        rec_snap_resp = await client.post(
            f"/api/v1/datasets/{comp_ds_id}/record-component-snapshots",
            headers=admin_headers,
            json={"adapter_code": "gov_comp_routing_harness"},
        )
        assert rec_snap_resp.status_code == 200
        assert rec_snap_resp.json()["data"]["recorded_count"] == 3

        # Approve all 3 component cases
        comp_cases = (
            await client.get(f"/api/v1/datasets/{comp_ds_id}/cases", headers=admin_headers)
        ).json()["data"]["items"]
        for cc in comp_cases:
            assert cc["has_snapshot"] is True
            await client.put(
                f"/api/v1/cases/{cc['id']}/curate",
                headers=admin_headers,
                json={"review_status": "APPROVED"},
            )

        # Replay on DEV with config_overrides
        comp_dev_run = (
            await client.post(
                "/api/v1/runs",
                headers=admin_headers,
                json={
                    "run_name": "Component回放调优-DEV",
                    "suite_code": "suite_gov_comp_routing_v1",
                    "dataset_id": comp_ds_id,
                    "dataset_split_filter": "DEV",
                    "sut_fingerprint": {
                        "git_sha": "comp_v2",
                        "config_overrides": {"confidence_threshold": 0.72},
                    },
                    "run_sync": True,
                },
            )
        ).json()["data"]
        assert comp_dev_run["status"] == "COMPLETED"
        assert {"confidence_threshold": 0.72} in state_tracker["harness_replay_overrides"]

        # Recommend thresholds on DEV -> 200 OK
        opt_resp = await client.post(
            f"/api/v1/runs/{comp_dev_run['run_no']}/recommend-thresholds",
            headers=admin_headers,
            json={"metric": "youden_j"},
        )
        assert opt_resp.status_code == 200
        opt_data = opt_resp.json()["data"]
        assert opt_data["best_youden_j"] == pytest.approx(1.0)

        # Replay on HOLDOUT -> verify recommend-thresholds is strictly blocked with 400!
        comp_holdout_run = (
            await client.post(
                "/api/v1/runs",
                headers=admin_headers,
                json={
                    "run_name": "Component独立验真-HOLDOUT",
                    "suite_code": "suite_gov_comp_routing_v1",
                    "dataset_id": comp_ds_id,
                    "dataset_split_filter": "HOLDOUT",
                    "sut_fingerprint": {
                        "git_sha": "comp_v2",
                        "config_overrides": {"confidence_threshold": 0.72},
                    },
                    "run_sync": True,
                },
            )
        ).json()["data"]
        assert comp_holdout_run["pass_rate"] == pytest.approx(1.0)

        holdout_opt_attempt = await client.post(
            f"/api/v1/runs/{comp_holdout_run['run_no']}/recommend-thresholds",
            headers=admin_headers,
        )
        assert holdout_opt_attempt.status_code == 400
    finally:
        app.dependency_overrides.pop(get_desensitize_http_transport, None)
        app.dependency_overrides.pop(get_sut_http_transport, None)
        app.dependency_overrides.pop(get_provider_http_transport, None)
