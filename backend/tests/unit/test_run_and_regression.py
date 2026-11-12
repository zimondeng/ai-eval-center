from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.api.adapters import get_sut_http_transport
from app.api.providers import get_provider_http_transport
from app.common.crypto import sha256_hex
from app.db.base import utcnow_naive
from app.db.models import ApiKey


def _seed_curated_e2e_dataset(
    client_sync_cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return client_sync_cases


async def _create_dataset_with_cases(
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    *,
    dataset_code: str,
    eval_level: str = "E2E",
    cases: list[dict[str, Any]],
) -> tuple[int, dict[str, dict[str, Any]]]:
    ds_resp = await client.post(
        "/api/v1/datasets",
        headers=admin_headers,
        json={
            "dataset_code": dataset_code,
            "version_tag": "v1.0",
            "dataset_name": f"测试集-{dataset_code}",
            "eval_level": eval_level,
        },
    )
    assert ds_resp.status_code == 200
    ds_id = ds_resp.json()["data"]["id"]

    by_code: dict[str, dict[str, Any]] = {}
    for c in cases:
        create_resp = await client.post(
            f"/api/v1/datasets/{ds_id}/cases",
            headers=admin_headers,
            json=c,
        )
        assert create_resp.status_code == 200
        row = create_resp.json()["data"]
        if c.get("curate_approve", True):
            cur_resp = await client.put(
                f"/api/v1/cases/{row['id']}/curate",
                headers=admin_headers,
                json={
                    "review_status": "APPROVED",
                    "pii_masked": 1,
                    "expected_output": c.get("expected_output"),
                    "expected_structured_json": c.get("expected_structured_json"),
                },
            )
            assert cur_resp.status_code == 200
            row = cur_resp.json()["data"]
        by_code[row["case_code"]] = row
    return ds_id, by_code


async def test_create_and_execute_run_snapshots_metrics_and_error_isolation(
    app: FastAPI,
    client: httpx.AsyncClient,
    db_session: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVAL_JAVA_BEARER_TOKEN", "sut-bearer-secret")

    # Unauthenticated requests to /api/v1/runs* MUST be rejected with 401
    unauth_resp = await client.get("/api/v1/runs")
    assert unauth_resp.status_code == 401
    assert unauth_resp.json()["code"] == 401

    # Provision CI API Key (evk_*) to verify /api/v1/runs supports X-API-Key
    raw_ci_key = "evk_live_runner_ci_trigger_key_001"
    db_session.add(
        ApiKey(
            key_name="ci-runner-key",
            key_prefix=raw_ci_key[:13],
            key_sha256=sha256_hex(raw_ci_key),
            status="ACTIVE",
            expires_at=utcnow_naive() + timedelta(days=7),
        )
    )
    db_session.commit()
    ci_headers = {"X-API-Key": raw_ci_key}

    ds_id, cases_by_code = await _create_dataset_with_cases(
        client,
        admin_headers,
        dataset_code="run_exec_ds",
        cases=[
            {
                "case_code": "dev_pass_1",
                "dataset_split": "DEV",
                "input_text": "常州住房公积金咨询电话是多少？",
                "expected_output": "常州公积金咨询热线为 12329，线上可通过常州住房公积金公众号办理。",
                "expected_structured_json": {"expected_agent_ids": ["cz_gjj_agent"]},
                "sut_input_json": {"selected_region": "天宁区"},
                "pii_masked": 1,
            },
            {
                "case_code": "dev_fail_2",
                "dataset_split": "DEV",
                "input_text": "提取商业贷款公积金需要什么材料？",
                "expected_output": "需身份证、一类银行卡、借款合同及还款明细，热线 12329。",
                "expected_structured_json": {"expected_agent_ids": ["cz_gjj_agent"]},
                "sut_input_json": {"selected_region": "钟楼区"},
                "pii_masked": 1,
            },
            {
                "case_code": "dev_sut_err_3",
                "dataset_split": "DEV",
                "input_text": "触发SUT网关超时或500异常",
                "expected_output": "不应崩溃整批",
                "expected_structured_json": {"expected_agent_ids": ["cz_gjj_agent"]},
                "pii_masked": 1,
            },
            {
                "case_code": "dev_judge_err_4",
                "dataset_split": "DEV",
                "input_text": "触发判别模型502异常",
                "expected_output": "不应崩溃整批",
                "expected_structured_json": {"expected_agent_ids": ["cz_gjj_agent"]},
                "pii_masked": 1,
            },
            {
                "case_code": "dev_unapproved_5",
                "dataset_split": "DEV",
                "input_text": "未审核通过用例不应被执行",
                "pii_masked": 1,
                "curate_approve": False,
            },
            {
                "case_code": "holdout_ignored_6",
                "dataset_split": "HOLDOUT",
                "input_text": "HOLDOUT用例在DEV过滤下不应被执行",
                "expected_output": "留出集答案",
                "expected_structured_json": {"expected_agent_ids": ["cz_gjj_agent"]},
                "pii_masked": 1,
            },
        ],
    )

    def sut_handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode("utf-8"))
        q = body["query"]
        if "触发SUT网关" in q:
            return httpx.Response(503, text="upstream gateway unavailable")
        if "咨询电话" in q:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "answer": "常州公积金咨询热线为 12329，也可在常州住房公积金微信公众号办理。",
                        "references": [{"content": "常州住房公积金热线 12329，支持公众号线上办。"}],
                        "finalAgentId": "cz_gjj_agent",
                        "selectedBranch": "department",
                        "finalRouteType": "department",
                        "requestId": "sut_req_1",
                        "events": [{"step": "routed"}],
                    }
                },
            )
        if "商业贷款" in q:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "answer": "不清楚，随便打 99999 问问吧。",
                        "references": [{"content": "需身份证、一类银行卡、借款合同及还款明细，热线 12329。"}],
                        "finalAgentId": "wrong_agent",
                        "selectedBranch": "fallback",
                        "finalRouteType": "fallback",
                        "requestId": "sut_req_2",
                        "events": [],
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "answer": "普通正常返回用于触发Judge异常",
                    "references": [{"content": "参考材料"}],
                    "finalAgentId": "cz_gjj_agent",
                    "selectedBranch": "department",
                    "finalRouteType": "department",
                    "requestId": "sut_req_4",
                    "events": [],
                }
            },
        )

    def judge_handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode("utf-8"))
        state = body["state"]
        if "触发判别模型502异常" in state.get("input", ""):
            return httpx.Response(502, text="judge gateway bad gateway")

        is_good = "咨询电话" in state.get("input", "")
        answers: dict[str, Any] = {}
        for q_key, q_spec in body["questions"].items():
            q_type = q_spec["type"]
            if q_type == "noul":
                answers[q_key] = {"noul": 0.95 if is_good else 0.15, "confidence": 0.90 if is_good else 0.42}
            elif q_type == "score":
                k = len(q_spec["criteria"])
                if is_good:
                    probs = [0.0] * (k - 1) + [1.0]
                else:
                    probs = [0.9] + [0.1 / (k - 1)] * (k - 1)
                answers[q_key] = {"probabilities": probs}
            elif q_type == "choice":
                opts = list(q_spec["criteria"].keys())
                if is_good:
                    probs_map = {o: (0.90 if i == len(opts) - 2 else 0.10 / (len(opts) - 1)) for i, o in enumerate(opts)}
                else:
                    probs_map = {o: (0.85 if i == 0 else 0.15 / (len(opts) - 1)) for i, o in enumerate(opts)}
                answers[q_key] = {"probabilities": probs_map}
        return httpx.Response(
            200,
            headers={"X-Request-ID": "judge_req_abc"},
            json={
                "answers": answers,
                "usage": {"input_tokens": 500, "output_tokens": 20},
            },
        )

    app.dependency_overrides[get_sut_http_transport] = lambda: httpx.MockTransport(sut_handler)
    app.dependency_overrides[get_provider_http_transport] = lambda: httpx.MockTransport(judge_handler)
    try:
        create_run_resp = await client.post(
            "/api/v1/runs",
            headers=ci_headers,
            json={
                "run_name": "DEV分集首轮冒烟跑批",
                "suite_code": "suite_gov_e2e_golden_v1",
                "dataset_id": ds_id,
                "dataset_split_filter": "DEV",
                "provider_code": "emrank_decisions_relay",
                "model_override": "typesafe/jev-1.14.0",
                "sut_fingerprint": {
                    "git_sha": "846e4b9b",
                    "prompt_version": "v1",
                    "config_overrides": {},
                },
                "run_sync": True,
            },
        )
        assert create_run_resp.status_code == 200
        run_data = create_run_resp.json()["data"]
        run_no = run_data["run_no"]
        assert run_no.startswith("RUN_")
        assert run_data["status"] == "COMPLETED"

        # Immutable provider & SUT snapshot checks
        assert run_data["pinned_provider_kind"] == "DECISION_RELAY"
        assert run_data["pinned_endpoint_url"] == "https://emrank.tech/v1/decisions"
        assert run_data["pinned_model_name"] == "typesafe/jev-1.14.0"
        assert run_data["sut_fingerprint_json"]["git_sha"] == "846e4b9b"

        # 4 APPROVED DEV cases selected (unapproved + HOLDOUT excluded)
        assert run_data["total_cases"] == 4
        assert run_data["completed_cases"] == 4
        assert run_data["passed_cases"] == 1
        assert run_data["failed_cases"] == 1
        assert run_data["error_cases"] == 2
        assert run_data["uncertain_cases"] == 1  # dev_fail_2 had confidence 0.42 < floor 0.50
        assert run_data["pass_rate"] == pytest.approx(0.5)

        # Each of the 2 successful cases has 2 distinct state_fields groups in suite_gov_e2e_golden_v1 -> 2 calls/case
        assert run_data["total_decision_calls"] >= 4
        assert run_data["total_input_tokens"] >= 2000
        # emrank cost_per_1m_input_tokens = 0.20 -> >= 2000 * 0.20 / 1e6 = 0.0004
        assert run_data["total_eval_cost"] > 0.0
        assert run_data["p50_sut_latency_ms"] is not None
        assert run_data["p95_sut_latency_ms"] is not None

        # Dimension summary includes 10-bin histogram counts and unbatched_question_count
        dim_sum = run_data["dimension_summary_json"]
        assert "rag_faithfulness_s1" in dim_sum
        rag_stat = dim_sum["rag_faithfulness_s1"]
        assert len(rag_stat["histogram_counts"]) == 10
        assert sum(rag_stat["histogram_counts"]) == 2
        assert rag_stat["unbatched_question_count"] == 4  # 2 questions * 2 evaluated cases

        # GET /api/v1/runs list with filters & enrichment
        list_resp = await client.get(
            "/api/v1/runs",
            params={"suite_code": "suite_gov_e2e_golden_v1", "dataset_split_filter": "DEV"},
            headers=ci_headers,
        )
        assert list_resp.status_code == 200
        list_data = list_resp.json()["data"]
        assert list_data["total"] == 1
        item0 = list_data["items"][0]
        assert item0["run_no"] == run_no
        assert item0["suite_code"] == "suite_gov_e2e_golden_v1"
        assert item0["dataset_name"] == "测试集-run_exec_ds"
        assert item0["adapter_code"] == "gov_e2e_routing_blocking"

        # GET /api/v1/runs/{run_no} detail report
        detail_resp = await client.get(f"/api/v1/runs/{run_no}", headers=admin_headers)
        assert detail_resp.status_code == 200
        detail = detail_resp.json()["data"]
        assert detail["pass_policy"] == "ALL_BLOCKERS_AND_WEIGHTED"
        assert detail["suite_pass_threshold"] == pytest.approx(0.75)
        assert detail["confidence_review_floor"] == pytest.approx(0.50)
        assert detail["unbatched_question_count"] >= 10
    finally:
        app.dependency_overrides.pop(get_sut_http_transport, None)
        app.dependency_overrides.pop(get_provider_http_transport, None)


async def test_semantic_regression_cases_filters_compare_cross_model_warning_and_cancel(
    app: FastAPI,
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    monkeypatch.setenv("EVAL_JAVA_BEARER_TOKEN", "sut-bearer-secret")

    # Unauthenticated requests to /api/v1/runs* MUST be rejected with 401
    unauth_resp = await client.get("/api/v1/runs")
    assert unauth_resp.status_code == 401
    assert unauth_resp.json()["code"] == 401

    ds_id, cases_by_code = await _create_dataset_with_cases(
        client,
        admin_headers,
        dataset_code="reg_compare_ds",
        cases=[
            {
                "case_code": "c_same",
                "dataset_split": "DEV",
                "input_text": "公积金线上提取怎么办理？",
                "expected_output": "可通过常州住房公积金微信公众号线上办理，咨询电话 12329。",
                "expected_structured_json": {"expected_agent_ids": ["cz_gjj_agent"]},
                "pii_masked": 1,
            },
            {
                "case_code": "c_specificity",
                "dataset_split": "DEV",
                "input_text": "退休提取公积金要多久到账？",
                "expected_output": "符合条件即时办结，资金一般 3 个工作日内到账，热线 12329。",
                "expected_structured_json": {"expected_agent_ids": ["cz_gjj_agent"]},
                "pii_masked": 1,
            },
            {
                "case_code": "c_action",
                "dataset_split": "DEV",
                "input_text": "异地购房提取公积金去哪办？",
                "expected_output": "可通过全国住房公积金小程序跨省通办，或线下窗口办理，热线 12329。",
                "expected_structured_json": {"expected_agent_ids": ["cz_gjj_agent"]},
                "pii_masked": 1,
            },
            {
                "case_code": "c_fact",
                "dataset_split": "DEV",
                "input_text": "常州公积金最高贷款额度是多少？",
                "expected_output": "双缴存职工最高 120 万元，单缴存职工最高 90 万元，热线 12329。",
                "expected_structured_json": {"expected_agent_ids": ["cz_gjj_agent"]},
                "pii_masked": 1,
            },
        ],
    )

    current_mode = {"version": "baseline"}

    def sut_handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode("utf-8"))
        q = body["query"]
        ver = current_mode["version"]
        if "线上提取" in q:
            ans = (
                "关注常州住房公积金微信公众号即可线上办理，电话 12329。"
                if ver == "baseline"
                else "您可通过常州住房公积金公众号在线申办，咨询热线 12329。"
            )
        elif "退休提取" in q:
            ans = (
                "线上办结后一般 3 个工作日内到账，热线 12329。"
                if ver == "baseline"
                else "线上办结后资金到账较快，详情咨询 12329。"
            )
        elif "异地购房" in q:
            ans = (
                "可通过全国住房公积金小程序线上跨省通办，热线 12329。"
                if ver == "baseline"
                else "必须本人带全套原件去常州线下柜台申请，电话 12329。"
            )
        else:
            ans = (
                "双职工最高可贷 120 万元，单职工最高 90 万元，热线 12329。"
                if ver == "baseline"
                else "双职工最高只能贷 50 万元，电话 12329。"
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "answer": ans,
                    "references": [
                        {
                            "content": (
                                "常州住房公积金政策：双职工最高 120 万元、单职工 90 万元；"
                                "异地购房支持全国住房公积金小程序跨省通办；3 个工作日内到账；热线 12329。"
                            )
                        }
                    ],
                    "finalAgentId": "cz_gjj_agent",
                    "selectedBranch": "department",
                    "finalRouteType": "department",
                    "requestId": f"req_{ver}",
                    "events": [{"ver": ver}],
                }
            },
        )

    def judge_handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode("utf-8"))
        state = body["state"]
        questions = body["questions"]

        # Semantic regression call
        if "semantic_regression_v1__diff" in questions:
            inp = state.get("input", "")
            if "最高贷款额度" in inp:
                probs = {"SAME": 0.02, "FACT_DIFFERS": 0.90, "ACTION_DIFFERS": 0.05, "SPECIFICITY_DIFFERS": 0.03}
            elif "异地购房" in inp:
                probs = {"SAME": 0.05, "FACT_DIFFERS": 0.10, "ACTION_DIFFERS": 0.80, "SPECIFICITY_DIFFERS": 0.05}
            elif "退休提取" in inp:
                probs = {"SAME": 0.10, "FACT_DIFFERS": 0.05, "ACTION_DIFFERS": 0.05, "SPECIFICITY_DIFFERS": 0.80}
            else:
                probs = {"SAME": 0.92, "FACT_DIFFERS": 0.02, "ACTION_DIFFERS": 0.03, "SPECIFICITY_DIFFERS": 0.03}
            return httpx.Response(
                200,
                headers={"X-Request-ID": "reg_req_01"},
                json={"answers": {"semantic_regression_v1__diff": {"probabilities": probs}}, "usage": {"input_tokens": 180}},
            )

        actual = state.get("actual_output", "")
        degraded = ("50 万元" in actual) or ("必须本人带全套原件" in actual)
        low_conf = "到账较快" in actual
        answers: dict[str, Any] = {}
        for q_key, q_spec in questions.items():
            qt = q_spec["type"]
            if qt == "noul":
                p = 0.20 if degraded else (0.55 if low_conf else 0.96)
                c = 0.10 if low_conf else 0.92
                answers[q_key] = {"noul": p, "confidence": c}
            elif qt == "score":
                k = len(q_spec["criteria"])
                if degraded:
                    probs_arr = [0.85] + [0.15 / (k - 1)] * (k - 1)
                elif low_conf:
                    probs_arr = [1.0 / k] * k
                else:
                    probs_arr = [0.02 / (k - 1)] * (k - 1) + [0.98]
                answers[q_key] = {"probabilities": probs_arr}
            elif qt == "choice":
                opts = list(q_spec["criteria"].keys())
                if degraded:
                    pm = {o: (0.85 if i == 0 else 0.15 / (len(opts) - 1)) for i, o in enumerate(opts)}
                else:
                    pm = {o: (0.90 if i == len(opts) - 2 else 0.10 / (len(opts) - 1)) for i, o in enumerate(opts)}
                answers[q_key] = {"probabilities": pm}
        return httpx.Response(
            200,
            headers={"X-Request-ID": "dim_req_99"},
            json={"answers": answers, "usage": {"input_tokens": 320}},
        )

    app.dependency_overrides[get_sut_http_transport] = lambda: httpx.MockTransport(sut_handler)
    app.dependency_overrides[get_provider_http_transport] = lambda: httpx.MockTransport(judge_handler)
    try:
        # 1. Baseline run with ollama_local_tev1 (model tev1:0.8b) via async default (run_sync=False)
        base_create = await client.post(
            "/api/v1/runs",
            headers=admin_headers,
            json={
                "run_name": "基线批次-tev1",
                "suite_code": "suite_gov_e2e_golden_v1",
                "dataset_id": ds_id,
                "dataset_split_filter": "DEV",
                "provider_code": "ollama_local_tev1",
                "sut_fingerprint": {"git_sha": "base_001"},
            },
        )
        assert base_create.status_code == 200
        base_no = base_create.json()["data"]["run_no"]
        base_id = base_create.json()["data"]["id"]

        # Wait for async background task to finish
        for _ in range(50):
            poll = (await client.get(f"/api/v1/runs/{base_no}", headers=admin_headers)).json()["data"]
            if poll["status"] == "COMPLETED":
                break
            await asyncio.sleep(0.02)
        assert poll["status"] == "COMPLETED"
        assert poll["pass_rate"] == pytest.approx(1.0)

        # 2. Candidate run A (same model tev1:0.8b -> cross_model_warning MUST be False!)
        current_mode["version"] = "candidate"
        cand_same_model = (
            await client.post(
                "/api/v1/runs",
                headers=admin_headers,
                json={
                    "run_name": "候选批次-同模型tev1",
                    "suite_code": "suite_gov_e2e_golden_v1",
                    "dataset_id": ds_id,
                    "dataset_split_filter": "DEV",
                    "provider_code": "ollama_local_tev1",
                    "baseline_run_id": base_id,
                    "sut_fingerprint": {"git_sha": "cand_002"},
                    "run_sync": True,
                },
            )
        ).json()["data"]
        cand_same_no = cand_same_model["run_no"]
        assert cand_same_model["regression_summary_json"] == {
            "SAME": 1,
            "FACT_DIFFERS": 1,
            "ACTION_DIFFERS": 1,
            "SPECIFICITY_DIFFERS": 1,
            "total_compared": 4,
        }

        cmp_same = await client.get(
            f"/api/v1/runs/{cand_same_no}/compare/{base_no}",
            headers=admin_headers,
        )
        assert cmp_same.status_code == 200
        cmp_same_data = cmp_same.json()["data"]
        assert cmp_same_data["cross_model_warning"] is False
        assert [d["regression_tag"] for d in cmp_same_data["case_diffs"]] == [
            "FACT_DIFFERS",
            "ACTION_DIFFERS",
            "SPECIFICITY_DIFFERS",
            "SAME",
        ]
        assert cmp_same_data["kpi_deltas"]["delta_pass_rate"] < 0.0

        # 3. Candidate run B (different model typesafe/jev-1.14.0 -> cross_model_warning MUST be True!)
        cand_diff_model = (
            await client.post(
                "/api/v1/runs",
                headers=admin_headers,
                json={
                    "run_name": "候选批次-跨模型jev",
                    "suite_code": "suite_gov_e2e_golden_v1",
                    "dataset_id": ds_id,
                    "dataset_split_filter": "DEV",
                    "provider_code": "emrank_decisions_relay",
                    "model_override": "typesafe/jev-1.14.0",
                    "baseline_run_no": base_no,
                    "sut_fingerprint": {"git_sha": "cand_003"},
                    "run_sync": True,
                },
            )
        ).json()["data"]
        cmp_diff = (
            await client.get(
                f"/api/v1/runs/{cand_diff_model['run_no']}/compare/{base_no}",
                headers=admin_headers,
            )
        ).json()["data"]
        assert cmp_diff["cross_model_warning"] is True

        # 4. GET /api/v1/runs/{run_no}/cases filtering & full ledger attribution
        fact_cases_resp = await client.get(
            f"/api/v1/runs/{cand_same_no}/cases",
            params={"passed": 0, "regression_tag": "FACT_DIFFERS"},
            headers=admin_headers,
        )
        assert fact_cases_resp.status_code == 200
        fact_items = fact_cases_resp.json()["data"]["items"]
        assert len(fact_items) == 1
        fc = fact_items[0]
        assert fc["case_code"] == "c_fact"
        assert fc["overall_passed"] is False
        assert fc["regression_tag"] == "FACT_DIFFERS"
        assert len(fc["ledgers"]) == 4
        s1_ledgers = [l for l in fc["ledgers"] if l["evaluator_kind"] == "SYSTEM_ONE"]
        assert s1_ledgers[0]["upstream_request_id"] == "dim_req_99"
        assert "questions" in s1_ledgers[0]["raw_answers_json"]

        # Filter by needs_human_review=1 and case_ids comma-separated
        uncertain_resp = await client.get(
            f"/api/v1/runs/{cand_same_no}/cases",
            params={"needs_human_review": 1},
            headers=admin_headers,
        )
        assert [c["case_code"] for c in uncertain_resp.json()["data"]["items"]] == ["c_specificity"]

        target_ids = f"{cases_by_code['c_same']['id']},{cases_by_code['c_fact']['id']}"
        by_ids_resp = await client.get(
            f"/api/v1/runs/{cand_same_no}/cases",
            params={"case_ids": target_ids},
            headers=admin_headers,
        )
        assert {c["case_code"] for c in by_ids_resp.json()["data"]["items"]} == {"c_same", "c_fact"}

        # 5. Cancel an in-flight async run
        slow_gate = asyncio.Event()

        async def slow_sut_handler(req: httpx.Request) -> httpx.Response:
            await slow_gate.wait()
            return sut_handler(req)

        app.dependency_overrides[get_sut_http_transport] = lambda: httpx.MockTransport(slow_sut_handler)
        cancel_target = (
            await client.post(
                "/api/v1/runs",
                headers=admin_headers,
                json={
                    "run_name": "待取消慢速批次",
                    "suite_code": "suite_gov_e2e_golden_v1",
                    "dataset_id": ds_id,
                    "dataset_split_filter": "DEV",
                    "sut_fingerprint": {"git_sha": "slow"},
                },
            )
        ).json()["data"]
        cancel_resp = await client.post(
            f"/api/v1/runs/{cancel_target['run_no']}/cancel",
            headers=admin_headers,
        )
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["data"]["status"] == "CANCELLED"
        slow_gate.set()
        await asyncio.sleep(0.05)
        after_cancel = (
            await client.get(f"/api/v1/runs/{cancel_target['run_no']}", headers=admin_headers)
        ).json()["data"]
        assert after_cancel["status"] == "CANCELLED"
    finally:
        app.dependency_overrides.pop(get_sut_http_transport, None)
        app.dependency_overrides.pop(get_provider_http_transport, None)


async def test_recalculate_100_percent_parity_with_fresh_run_and_500_cases_under_100ms(
    app: FastAPI,
    client: httpx.AsyncClient,
    db_session: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time
    from app.db.models import EvalCase, EvalRun, EvalRunCase, ProbabilityLedger, Suite
    from sqlalchemy import select

    monkeypatch.setenv("EVAL_JAVA_BEARER_TOKEN", "sut-bearer-secret")

    # Unauthenticated requests to /api/v1/runs* MUST be rejected with 401
    unauth_resp = await client.get("/api/v1/runs")
    assert unauth_resp.status_code == 401
    assert unauth_resp.json()["code"] == 401

    # Part A: 100% numerical parity between /recalculate and a fresh run under updated suite thresholds
    parity_cases = []
    for idx in range(8):
        parity_cases.append(
            {
                "case_code": f"parity_{idx}",
                "dataset_split": "DEV",
                "input_text": f"校准一致性测试问题 #{idx}",
                "expected_output": "标准政策答复，热线 12329。",
                "expected_structured_json": {"expected_agent_ids": ["cz_gjj_agent"]},
                "pii_masked": 1,
            }
        )
    ds_id, _ = await _create_dataset_with_cases(
        client,
        admin_headers,
        dataset_code="recalc_parity_ds",
        cases=parity_cases,
    )

    def parity_sut_handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode("utf-8"))
        q = body["query"]
        idx = int(q.split("#")[1])
        agent_id = "cz_gjj_agent" if idx != 0 else "other_agent"
        ans = f"答复 #{idx}，热线 12329。"
        return httpx.Response(
            200,
            json={
                "data": {
                    "answer": ans,
                    "references": [{"content": "标准政策答复，热线 12329。"}],
                    "finalAgentId": agent_id,
                    "selectedBranch": "department",
                    "finalRouteType": "department",
                    "requestId": f"p_{idx}",
                    "events": [],
                }
            },
        )

    def parity_judge_handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode("utf-8"))
        idx = int(body["state"]["input"].split("#")[1])
        # Deterministic gradient of scores across cases 0..7: 0.52, 0.58, 0.64, 0.70, 0.76, 0.82, 0.88, 0.94
        target_p = round(0.52 + 0.06 * idx, 4)
        answers: dict[str, Any] = {}
        for q_key, q_spec in body["questions"].items():
            qt = q_spec["type"]
            if qt == "noul":
                answers[q_key] = {"noul": target_p}
            elif qt == "score":
                k = len(q_spec["criteria"])
                probs = [round((1.0 - target_p) / (k - 1), 6)] * (k - 1) + [target_p]
                answers[q_key] = {"probabilities": probs}
            elif qt == "choice":
                opts = list(q_spec["criteria"].keys())
                # Case 3 triggers N/A (last option score=None has prob 0.80 >= 0.5)
                if idx == 3:
                    pm = {o: (0.80 if i == len(opts) - 1 else 0.20 / (len(opts) - 1)) for i, o in enumerate(opts)}
                else:
                    pm = {
                        opts[0]: round(1.0 - target_p, 6),
                        opts[len(opts) - 2]: target_p,
                    }
                    for o in opts:
                        pm.setdefault(o, 0.0)
                answers[q_key] = {"probabilities": pm}
        return httpx.Response(200, json={"answers": answers, "usage": {"input_tokens": 200}})

    app.dependency_overrides[get_sut_http_transport] = lambda: httpx.MockTransport(parity_sut_handler)
    app.dependency_overrides[get_provider_http_transport] = lambda: httpx.MockTransport(parity_judge_handler)
    try:
        run_a = (
            await client.post(
                "/api/v1/runs",
                headers=admin_headers,
                json={
                    "run_name": "Parity初始批次A",
                    "suite_code": "suite_gov_e2e_golden_v1",
                    "dataset_id": ds_id,
                    "dataset_split_filter": "DEV",
                    "sut_fingerprint": {"git_sha": "parity_a"},
                    "run_sync": True,
                },
            )
        ).json()["data"]
        run_a_no = run_a["run_no"]

        # Forbid ANY external HTTP call during /recalculate!
        def forbidden_transport(req: httpx.Request) -> httpx.Response:
            raise AssertionError(f"Recalculate MUST make 0 external HTTP calls! Got {req.url}")

        app.dependency_overrides[get_sut_http_transport] = lambda: httpx.MockTransport(forbidden_transport)
        app.dependency_overrides[get_provider_http_transport] = lambda: httpx.MockTransport(forbidden_transport)

        new_suite_threshold = 0.80
        dim_overrides = {
            "route_top1_match": {"threshold": 1.0, "weight": 2.0, "is_blocker": True},
            "rag_faithfulness_s1": {"threshold": 0.82, "weight": 3.0, "is_blocker": True},
            "policy_and_action_accuracy_s1": {"threshold": 0.68, "weight": 1.5, "is_blocker": False},
        }
        recalc_resp = await client.post(
            f"/api/v1/runs/{run_a_no}/recalculate",
            headers=admin_headers,
            json={
                "suite_pass_threshold": new_suite_threshold,
                "dimension_overrides": dim_overrides,
            },
        )
        assert recalc_resp.status_code == 200
        recalc_data = recalc_resp.json()["data"]

        # Now create a suite with those EXACT thresholds/weights/is_blocker and run fresh Run B
        app.dependency_overrides[get_sut_http_transport] = lambda: httpx.MockTransport(parity_sut_handler)
        app.dependency_overrides[get_provider_http_transport] = lambda: httpx.MockTransport(parity_judge_handler)

        suite_create = await client.post(
            "/api/v1/suites",
            headers=admin_headers,
            json={
                "suite_code": "suite_parity_updated_v1",
                "suite_name": "Parity新门禁验证套件",
                "eval_level": "E2E",
                "pass_policy": "ALL_BLOCKERS_AND_WEIGHTED",
                "suite_pass_threshold": new_suite_threshold,
                "confidence_review_floor": 0.50,
                "enable_semantic_regression": True,
                "dimensions_json": [
                    {
                        "dimension_code": "route_top1_match",
                        "version": 1,
                        "weight": 2.0,
                        "threshold": 1.0,
                        "is_blocker": True,
                    },
                    {
                        "dimension_code": "numeric_fact_grounding",
                        "version": 1,
                        "weight": 1.5,
                        "threshold": 1.0,
                        "is_blocker": True,
                    },
                    {
                        "dimension_code": "rag_faithfulness_s1",
                        "version": 1,
                        "weight": 3.0,
                        "threshold": 0.82,
                        "is_blocker": True,
                    },
                    {
                        "dimension_code": "policy_and_action_accuracy_s1",
                        "version": 1,
                        "weight": 1.5,
                        "threshold": 0.68,
                        "is_blocker": False,
                    },
                ],
            },
        )
        assert suite_create.status_code == 200

        run_b = (
            await client.post(
                "/api/v1/runs",
                headers=admin_headers,
                json={
                    "run_name": "Parity重跑验证批次B",
                    "suite_code": "suite_parity_updated_v1",
                    "dataset_id": ds_id,
                    "dataset_split_filter": "DEV",
                    "sut_adapter_code": "gov_e2e_routing_blocking",
                    "provider_code": "ollama_local_tev1",
                    "sut_fingerprint": {"git_sha": "parity_b"},
                    "run_sync": True,
                },
            )
        ).json()["data"]

        # 100% numerical parity assertions!
        assert recalc_data["recalculated_pass_rate"] == run_b["pass_rate"]
        assert recalc_data["recalculated_weighted_avg_score"] == run_b["weighted_avg_score"]
        assert recalc_data["passed_cases"] == run_b["passed_cases"]
        assert recalc_data["failed_cases"] == run_b["failed_cases"]
        assert recalc_data["delta_pass_rate"] == round(run_b["pass_rate"] - run_a["pass_rate"], 4)
        for dim_code, dim_summary in run_b["dimension_summary_json"].items():
            assert recalc_data["dimension_pass_rates"][dim_code] == dim_summary["pass_rate"]

        run_a_cases = (
            await client.get(f"/api/v1/runs/{run_a_no}/cases", headers=admin_headers)
        ).json()["data"]["items"]
        run_b_cases = (
            await client.get(f"/api/v1/runs/{run_b['run_no']}/cases", headers=admin_headers)
        ).json()["data"]["items"]
        a_pass_by_cid = {c["case_id"]: c["overall_passed"] for c in run_a_cases}
        b_pass_by_cid = {c["case_id"]: c["overall_passed"] for c in run_b_cases}
        expected_flipped = sorted(
            cid for cid in a_pass_by_cid if a_pass_by_cid[cid] != b_pass_by_cid[cid]
        )
        assert sorted(recalc_data["flipped_case_ids"]) == expected_flipped
        assert len(expected_flipped) > 0

        # Part B: 500-case pure-memory recalculation benchmark (< 100ms, 0 external calls)
        app.dependency_overrides[get_sut_http_transport] = lambda: httpx.MockTransport(forbidden_transport)
        app.dependency_overrides[get_provider_http_transport] = lambda: httpx.MockTransport(forbidden_transport)

        suite_row = db_session.scalar(
            select(Suite).where(Suite.suite_code == "suite_gov_e2e_golden_v1")
        )
        assert suite_row is not None

        bench_run = EvalRun(
            run_no="RUN_20261112_BENCH500",
            run_name="500条性能基准批次",
            suite_id=suite_row.id,
            dataset_id=ds_id,
            dataset_split_filter="DEV",
            sut_adapter_id=suite_row.default_adapter_id or 1,
            sut_fingerprint_json={"bench": True},
            provider_id=suite_row.default_provider_id or 1,
            pinned_provider_kind="OLLAMA_LOCAL",
            pinned_endpoint_url="http://127.0.0.1:11434/v1/systemone",
            pinned_model_name="tev1:0.8b",
            status="COMPLETED",
            total_cases=500,
            completed_cases=500,
            passed_cases=350,
            failed_cases=150,
            pass_rate=0.7000,
            weighted_avg_score=0.7850,
        )
        db_session.add(bench_run)
        db_session.flush()

        # Seed 500 EvalCase + 500 EvalRunCase + 2,000 ProbabilityLedger rows
        case_objs = [
            EvalCase(
                dataset_id=ds_id,
                case_code=f"bench500_{i:04d}",
                dataset_split="DEV",
                input_text=f"基准问题 {i}",
                pii_masked=1,
                review_status="APPROVED",
            )
            for i in range(500)
        ]
        db_session.add_all(case_objs)
        db_session.flush()

        rc_objs = []
        for i, c_obj in enumerate(case_objs):
            s = round(0.40 + (i / 500.0) * 0.58, 4)
            rc_objs.append(
                EvalRunCase(
                    run_id=bench_run.id,
                    case_id=c_obj.id,
                    exec_status="SUCCESS",
                    actual_output="基准输出",
                    overall_score=s,
                    overall_passed=1 if s >= 0.75 else 0,
                    min_confidence=0.85,
                    needs_human_review=0,
                )
            )
        db_session.add_all(rc_objs)
        db_session.flush()

        ledger_objs = []
        for i, rc_obj in enumerate(rc_objs):
            s = round(0.40 + (i / 500.0) * 0.58, 5)
            for d_idx, b_spec in enumerate(suite_row.dimensions_json):
                ledger_objs.append(
                    ProbabilityLedger(
                        run_id=bench_run.id,
                        run_case_id=rc_obj.id,
                        dimension_id=d_idx + 1,
                        dimension_code=b_spec["dimension_code"],
                        dimension_version=1,
                        evaluator_kind="SYSTEM_ONE",
                        raw_dimension_score=s,
                        applied_threshold=float(b_spec["threshold"]),
                        applied_weight=float(b_spec["weight"]),
                        is_blocker=1 if b_spec["is_blocker"] else 0,
                        passed=1 if s >= float(b_spec["threshold"]) else 0,
                        min_question_confidence=0.85,
                        deterministic_reason="bench",
                        raw_answers_json={"questions": [{"key": "q1", "type": "noul", "weight": 1.0, "applicable": True, "normalized_score": s}]},
                    )
                )
        db_session.add_all(ledger_objs)
        db_session.commit()

        # Warm-up once, then measure timed call
        await client.post(
            "/api/v1/runs/RUN_20261112_BENCH500/recalculate",
            headers=admin_headers,
            json={"suite_pass_threshold": 0.75},
        )

        t_start = time.perf_counter()
        bench_resp = await client.post(
            "/api/v1/runs/RUN_20261112_BENCH500/recalculate",
            headers=admin_headers,
            json={
                "suite_pass_threshold": 0.80,
                "dimension_overrides": {
                    "rag_faithfulness_s1": {"threshold": 0.82, "weight": 2.5, "is_blocker": True},
                    "policy_and_action_accuracy_s1": {"threshold": 0.78, "weight": 2.0, "is_blocker": True},
                },
            },
        )
        wall_ms = (time.perf_counter() - t_start) * 1000.0
        assert bench_resp.status_code == 200
        bench_out = bench_resp.json()["data"]
        assert bench_out["passed_cases"] + bench_out["failed_cases"] == 500
        assert bench_out["compute_elapsed_ms"] < 100
        assert wall_ms < 100.0
    finally:
        app.dependency_overrides.pop(get_sut_http_transport, None)
        app.dependency_overrides.pop(get_provider_http_transport, None)


async def test_recommend_thresholds_rejects_holdout_400_and_optimizes_youden_j_and_f1_on_dev(
    app: FastAPI,
    client: httpx.AsyncClient,
    db_session: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db.models import EvalCase

    monkeypatch.setenv("EVAL_JAVA_BEARER_TOKEN", "sut-bearer-secret")

    # Unauthenticated requests to /api/v1/runs* MUST be rejected with 401
    unauth_resp = await client.get("/api/v1/runs")
    assert unauth_resp.status_code == 401
    assert unauth_resp.json()["code"] == 401

    # Build 6 DEV cases (3 human-labeled bad around score ~0.42..0.52, 3 human-labeled good around ~0.82..0.92)
    # plus 2 HOLDOUT cases
    cases_payload = []
    dev_specs = [
        ("dev_neg_1", False, 0.40),
        ("dev_neg_2", False, 0.46),
        ("dev_neg_3", False, 0.52),
        ("dev_pos_4", True, 0.82),
        ("dev_pos_5", True, 0.88),
        ("dev_pos_6", True, 0.94),
    ]
    for code, human_ok, target_score in dev_specs:
        cases_payload.append(
            {
                "case_code": code,
                "dataset_split": "DEV",
                "input_text": f"阈值寻优样本|{target_score}",
                "expected_output": "标准答复 12329",
                "expected_structured_json": {
                    "expected_agent_ids": ["cz_gjj_agent"],
                    "expected_pass": human_ok,
                },
                "pii_masked": 1,
            }
        )
    for h_idx in range(2):
        cases_payload.append(
            {
                "case_code": f"holdout_{h_idx}",
                "dataset_split": "HOLDOUT",
                "input_text": f"留出集样本|{0.85 + 0.05 * h_idx}",
                "expected_output": "标准答复 12329",
                "expected_structured_json": {
                    "expected_agent_ids": ["cz_gjj_agent"],
                    "expected_pass": True,
                },
                "pii_masked": 1,
            }
        )

    ds_id, created_map = await _create_dataset_with_cases(
        client,
        admin_headers,
        dataset_code="thresh_opt_ds",
        cases=cases_payload,
    )

    def opt_sut_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "answer": "办理指引，热线 12329。",
                    "references": [{"content": "标准答复 12329"}],
                    "finalAgentId": "cz_gjj_agent",
                    "selectedBranch": "department",
                    "finalRouteType": "department",
                    "requestId": "opt_req",
                    "events": [],
                }
            },
        )

    def opt_judge_handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode("utf-8"))
        score_val = float(body["state"]["input"].split("|")[1])
        answers: dict[str, Any] = {}
        for q_key, q_spec in body["questions"].items():
            qt = q_spec["type"]
            if qt == "noul":
                answers[q_key] = {"noul": score_val}
            elif qt == "score":
                k = len(q_spec["criteria"])
                answers[q_key] = {
                    "probabilities": [round(1.0 - score_val, 6)] + [0.0] * (k - 2) + [score_val]
                }
            elif qt == "choice":
                opts = list(q_spec["criteria"].keys())
                pm = {opts[0]: round(1.0 - score_val, 6), opts[len(opts) - 2]: score_val}
                for o in opts:
                    pm.setdefault(o, 0.0)
                answers[q_key] = {"probabilities": pm}
        return httpx.Response(200, json={"answers": answers, "usage": {"input_tokens": 100}})

    app.dependency_overrides[get_sut_http_transport] = lambda: httpx.MockTransport(opt_sut_handler)
    app.dependency_overrides[get_provider_http_transport] = lambda: httpx.MockTransport(opt_judge_handler)
    try:
        # 1. HOLDOUT run MUST be strictly rejected with 400 on /recommend-thresholds!
        holdout_run = (
            await client.post(
                "/api/v1/runs",
                headers=admin_headers,
                json={
                    "run_name": "HOLDOUT留出集跑批",
                    "suite_code": "suite_gov_e2e_golden_v1",
                    "dataset_id": ds_id,
                    "dataset_split_filter": "HOLDOUT",
                    "sut_fingerprint": {"git_sha": "holdout_1"},
                    "run_sync": True,
                },
            )
        ).json()["data"]

        holdout_rec_resp = await client.post(
            f"/api/v1/runs/{holdout_run['run_no']}/recommend-thresholds",
            headers=admin_headers,
        )
        assert holdout_rec_resp.status_code == 400
        assert holdout_rec_resp.json()["code"] == 400
        assert "DEV" in holdout_rec_resp.json()["message"]

        # 2. ALL split run MUST also be rejected with 400!
        all_run = (
            await client.post(
                "/api/v1/runs",
                headers=admin_headers,
                json={
                    "run_name": "ALL混合集跑批",
                    "suite_code": "suite_gov_e2e_golden_v1",
                    "dataset_id": ds_id,
                    "dataset_split_filter": "ALL",
                    "sut_fingerprint": {"git_sha": "all_1"},
                    "run_sync": True,
                },
            )
        ).json()["data"]
        all_rec_resp = await client.post(
            f"/api/v1/runs/{all_run['run_no']}/recommend-thresholds",
            headers=admin_headers,
        )
        assert all_rec_resp.status_code == 400

        # 3. DEV run optimizes thresholds cleanly in (0.52, 0.82], achieving perfect J=1.0 and F1=1.0!
        dev_run = (
            await client.post(
                "/api/v1/runs",
                headers=admin_headers,
                json={
                    "run_name": "DEV调优集跑批",
                    "suite_code": "suite_gov_e2e_golden_v1",
                    "dataset_id": ds_id,
                    "dataset_split_filter": "DEV",
                    "sut_fingerprint": {"git_sha": "dev_1"},
                    "run_sync": True,
                },
            )
        ).json()["data"]

        dev_rec_resp = await client.post(
            f"/api/v1/runs/{dev_run['run_no']}/recommend-thresholds",
            headers=admin_headers,
            json={"metric": "youden_j"},
        )
        assert dev_rec_resp.status_code == 200
        rec_data = dev_rec_resp.json()["data"]
        assert rec_data["label_source"] == "ground_truth"
        assert 0.53 <= rec_data["suite_pass_threshold"] <= 0.82
        rag_rec_t = rec_data["dimension_overrides"]["rag_faithfulness_s1"]["threshold"]
        assert 0.53 <= rag_rec_t <= 0.82
        assert rec_data["best_youden_j"] == pytest.approx(1.0)
        assert rec_data["best_f1"] == pytest.approx(1.0)
        assert rec_data["preview"]["passed_cases"] == 3
        assert rec_data["preview"]["failed_cases"] == 3

        # F1 metric mode with explicit caller-supplied labels also works
        f1_rec_resp = await client.post(
            f"/api/v1/runs/{dev_run['run_no']}/recommend-thresholds",
            headers=admin_headers,
            json={
                "metric": "f1",
                "labels": {
                    created_map["dev_neg_1"]["case_code"]: 0,
                    created_map["dev_neg_2"]["case_code"]: 0,
                    created_map["dev_neg_3"]["case_code"]: 1,  # treat 0.52 as positive too!
                    created_map["dev_pos_4"]["case_code"]: 1,
                    created_map["dev_pos_5"]["case_code"]: 1,
                    created_map["dev_pos_6"]["case_code"]: 1,
                },
            },
        )
        assert f1_rec_resp.status_code == 200
        f1_data = f1_rec_resp.json()["data"]
        # Separating <= 0.46 (neg) from >= 0.52 (pos): threshold must lie in (0.46, 0.65]
        assert 0.47 <= f1_data["dimension_overrides"]["rag_faithfulness_s1"]["threshold"] <= 0.52
        assert f1_data["best_f1"] == pytest.approx(1.0)

        # Distribution fallback when explicit ground-truth binary labels are absent
        unlabeled_ds_id, _ = await _create_dataset_with_cases(
            client,
            admin_headers,
            dataset_code="thresh_unlabeled_dev_ds",
            cases=[
                {
                    "case_code": f"unlbl_{i}",
                    "dataset_split": "DEV",
                    "input_text": f"无显式标签样本|{score_v}",
                    "expected_output": "标准答复 12329",
                    "expected_structured_json": {"expected_agent_ids": ["cz_gjj_agent"]},
                    "pii_masked": 1,
                }
                for i, score_v in enumerate([0.38, 0.44, 0.86, 0.92])
            ],
        )
        unlabeled_run = (
            await client.post(
                "/api/v1/runs",
                headers=admin_headers,
                json={
                    "run_name": "无标签DEV分布寻优",
                    "suite_code": "suite_gov_e2e_golden_v1",
                    "dataset_id": unlabeled_ds_id,
                    "dataset_split_filter": "DEV",
                    "sut_fingerprint": {"git_sha": "unlbl_1"},
                    "run_sync": True,
                },
            )
        ).json()["data"]
        fallback_rec = (
            await client.post(
                f"/api/v1/runs/{unlabeled_run['run_no']}/recommend-thresholds",
                headers=admin_headers,
            )
        ).json()["data"]
        assert fallback_rec["label_source"] == "distribution_fallback"
        assert 0.45 <= fallback_rec["dimension_overrides"]["rag_faithfulness_s1"]["threshold"] <= 0.86

        # 4. Guardrail against post-run split contamination: if a case in DEV run is moved to HOLDOUT -> 400!
        tampered_case = db_session.get(EvalCase, created_map["dev_pos_6"]["id"])
        assert tampered_case is not None
        tampered_case.dataset_split = "HOLDOUT"
        db_session.commit()

        contaminated_resp = await client.post(
            f"/api/v1/runs/{dev_run['run_no']}/recommend-thresholds",
            headers=admin_headers,
        )
        assert contaminated_resp.status_code == 400
    finally:
        app.dependency_overrides.pop(get_sut_http_transport, None)
        app.dependency_overrides.pop(get_provider_http_transport, None)



