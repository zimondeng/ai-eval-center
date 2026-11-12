from __future__ import annotations

from datetime import timedelta

import httpx
from sqlalchemy.orm import Session

from app.common.crypto import sha256_hex
from app.db.base import utcnow_naive
from app.db.models import ApiKey, DecisionProvider, SutAdapter


async def test_dimensions_endpoints_require_console_jwt(
    client: httpx.AsyncClient,
    db_session: Session,
) -> None:
    raw_key = "evk_live_dim_test_api_key_0001"
    db_session.add(
        ApiKey(
            key_name="ci-key",
            key_prefix="evk_live_dim_",
            key_sha256=sha256_hex(raw_key),
            status="ACTIVE",
            expires_at=utcnow_naive() + timedelta(days=1),
        )
    )
    db_session.commit()

    for method, path, payload in [
        ("GET", "/api/v1/dimensions", None),
        ("GET", "/api/v1/dimensions/rag_faithfulness_s1", None),
        ("POST", "/api/v1/dimensions/lint", {"state_fields_json": [], "questions_json": []}),
        ("POST", "/api/v1/dimensions", {}),
    ]:
        no_auth = await client.request(method, path, json=payload)
        assert no_auth.status_code == 401
        assert no_auth.json()["code"] == 401

        apikey_only = await client.request(
            method, path, json=payload, headers={"X-API-Key": raw_key}
        )
        assert apikey_only.status_code == 401
        assert apikey_only.json()["code"] == 401


async def test_post_dimensions_lint_contract(
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
) -> None:
    resp = await client.post(
        "/api/v1/dimensions/lint",
        headers=admin_headers,
        json={
            "state_fields_json": ["input", "actual_output"],
            "questions_json": [
                {
                    "key": "core_conclusion",
                    "type": "score",
                    "instructions": "对比 expected_output，actual_output 是否准确？",
                    "criteria": [
                        "完全准确且无遗漏",
                        "部分准确",
                        "完全错误",
                    ],
                },
                {
                    "key": "channel_check",
                    "type": "noul",
                    "instructions": "是否提供了线上渠道？是否同时提供了线下窗口地址？",
                },
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    data = body["data"]
    assert data["passed"] is False

    codes_by_q = {(i["question_key"], i["code"]): i["severity"] for i in data["issues"]}
    assert codes_by_q[("core_conclusion", "LINT_SCORE_ORDER")] == "ERROR"
    assert codes_by_q[("core_conclusion", "LINT_STATE_FIELD_REF")] == "ERROR"
    assert codes_by_q[("channel_check", "LINT_COMPOUND_QUESTION")] == "WARN"


async def test_get_dimensions_filters_and_version_history_detail(
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
) -> None:
    all_resp = await client.get("/api/v1/dimensions", headers=admin_headers)
    assert all_resp.status_code == 200
    all_dims = all_resp.json()["data"]
    assert len(all_dims) == 4
    assert {d["dimension_code"] for d in all_dims} == {
        "route_top1_match",
        "numeric_fact_grounding",
        "rag_faithfulness_s1",
        "policy_and_action_accuracy_s1",
    }

    s1_grounding = await client.get(
        "/api/v1/dimensions",
        headers=admin_headers,
        params={
            "category": "GROUNDING",
            "evaluator_kind": "SYSTEM_ONE",
            "status": "ACTIVE",
            "latest_only": "true",
        },
    )
    assert s1_grounding.status_code == 200
    filtered = s1_grounding.json()["data"]
    assert [d["dimension_code"] for d in filtered] == ["rag_faithfulness_s1"]
    assert filtered[0]["version"] == 1
    assert filtered[0]["default_threshold"] == 0.75
    assert filtered[0]["lint_passed"] is True

    detail_resp = await client.get(
        "/api/v1/dimensions/rag_faithfulness_s1", headers=admin_headers
    )
    assert detail_resp.status_code == 200
    detail = detail_resp.json()["data"]
    assert detail["dimension_code"] == "rag_faithfulness_s1"
    assert detail["latest"]["version"] == 1
    assert len(detail["versions"]) == 1
    assert detail["versions"][0]["version"] == 1

    missing_resp = await client.get(
        "/api/v1/dimensions/non_existent_dimension", headers=admin_headers
    )
    assert missing_resp.status_code == 404
    assert missing_resp.json()["code"] == 404


async def test_post_dimensions_immutable_version_bump_and_linter_gate(
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
) -> None:
    # 1. Create new SYSTEM_ONE dimension -> v1 (with choice null option + noul + score)
    v1_payload = {
        "dimension_code": "answer_relevancy_custom",
        "dimension_name": "回答切题度 (v1)",
        "category": "RELEVANCY",
        "target_turn_mode": "SINGLE_TURN",
        "evaluator_kind": "SYSTEM_ONE",
        "state_fields_json": ["input", "actual_output"],
        "questions_json": [
            {
                "key": "direct_answer",
                "type": "score",
                "weight": 2.0,
                "instructions": "actual_output 是否直接回答了 input 的核心诉求？",
                "criteria": [
                    "答非所问，完全偏离 input 主题",
                    "部分切题，但遗漏了核心子问题",
                    "紧扣 input 诉求，直接清晰作答",
                ],
            },
            {
                "key": "clarification_handling",
                "type": "choice",
                "weight": 1.0,
                "instructions": "当 input 关键条件缺失时，actual_output 是否合理追问？",
                "criteria": {
                    "blind_answer": "忽略缺失前提强行下结论",
                    "proper_followup": "礼貌追问缺失的关键办理前提",
                    "no_need": "input 信息充分，无需追问",
                },
                "option_scores": {
                    "blind_answer": 0.0,
                    "proper_followup": 1.0,
                    "no_need": None,
                },
            },
        ],
        "default_threshold": 0.72,
        "strict_mode": False,
        "default_weight": 1.5,
        "status": "ACTIVE",
        "description": "v1 初始切题度定义",
    }
    create_v1 = await client.post(
        "/api/v1/dimensions", headers=admin_headers, json=v1_payload
    )
    assert create_v1.status_code == 200
    v1_data = create_v1.json()["data"]
    assert v1_data["version"] == 1
    assert v1_data["status"] == "ACTIVE"
    assert v1_data["lint_passed"] is True
    assert v1_data["lint_report_json"] == {"passed": True, "issues": []}
    assert v1_data["created_by"] == "admin"

    # 2. Update same dimension_code -> must create v2 and leave v1 untouched
    v2_payload = {
        **v1_payload,
        "dimension_name": "回答切题度与礼貌性 (v2)",
        "default_threshold": 0.85,
        "strict_mode": True,
        "questions_json": [
            {
                "key": "direct_answer",
                "type": "noul",
                "weight": 1.0,
                "instructions": "actual_output 是否直接回答了 input？同时是否保持礼貌？",
            }
        ],
        "description": "v2 调整题型(含复合问句WARN但允许ACTIVE)",
    }
    create_v2 = await client.post(
        "/api/v1/dimensions", headers=admin_headers, json=v2_payload
    )
    assert create_v2.status_code == 200
    v2_data = create_v2.json()["data"]
    assert v2_data["version"] == 2
    assert v2_data["id"] != v1_data["id"]
    assert v2_data["uuid"] != v1_data["uuid"]
    assert v2_data["lint_passed"] is True
    assert len(v2_data["lint_report_json"]["issues"]) == 1
    assert v2_data["lint_report_json"]["issues"][0]["code"] == "LINT_COMPOUND_QUESTION"
    assert v2_data["strict_mode"] is True

    # Verify v1 was NOT mutated via detail endpoint and list latest_only toggle
    history_resp = await client.get(
        "/api/v1/dimensions/answer_relevancy_custom", headers=admin_headers
    )
    assert history_resp.status_code == 200
    history = history_resp.json()["data"]
    assert history["latest"]["version"] == 2
    assert [v["version"] for v in history["versions"]] == [2, 1]
    persisted_v1 = history["versions"][1]
    assert persisted_v1["id"] == v1_data["id"]
    assert persisted_v1["uuid"] == v1_data["uuid"]
    assert persisted_v1["dimension_name"] == "回答切题度 (v1)"
    assert persisted_v1["default_threshold"] == 0.72
    assert persisted_v1["strict_mode"] is False
    assert len(persisted_v1["questions_json"]) == 2
    assert persisted_v1["description"] == "v1 初始切题度定义"

    latest_list = await client.get(
        "/api/v1/dimensions",
        headers=admin_headers,
        params={"category": "RELEVANCY", "latest_only": "true"},
    )
    assert [(d["dimension_code"], d["version"]) for d in latest_list.json()["data"]] == [
        ("answer_relevancy_custom", 2)
    ]

    full_list = await client.get(
        "/api/v1/dimensions",
        headers=admin_headers,
        params={"category": "RELEVANCY", "latest_only": "false"},
    )
    assert [(d["dimension_code"], d["version"]) for d in full_list.json()["data"]] == [
        ("answer_relevancy_custom", 2),
        ("answer_relevancy_custom", 1),
    ]

    # 3. Linter ERROR must block status == "ACTIVE" with 400, but allow status == "DRAFT"
    bad_rubric_base = {
        "dimension_code": "broken_rubric_dim",
        "dimension_name": "存在反向档位错误的维度",
        "category": "ACCURACY",
        "evaluator_kind": "SYSTEM_ONE",
        "state_fields_json": ["actual_output"],
        "questions_json": [
            {
                "key": "bad_score",
                "type": "score",
                "instructions": "对比 expected_output 判断 actual_output 质量。",
                "criteria": [
                    "完全准确无遗漏",
                    "完全错误",
                ],
            }
        ],
    }
    blocked_active = await client.post(
        "/api/v1/dimensions",
        headers=admin_headers,
        json={**bad_rubric_base, "status": "ACTIVE"},
    )
    assert blocked_active.status_code == 400
    assert blocked_active.json()["code"] == 400
    assert blocked_active.json()["data"]["passed"] is False

    allowed_draft = await client.post(
        "/api/v1/dimensions",
        headers=admin_headers,
        json={**bad_rubric_base, "status": "DRAFT"},
    )
    assert allowed_draft.status_code == 200
    draft_data = allowed_draft.json()["data"]
    assert draft_data["version"] == 1
    assert draft_data["status"] == "DRAFT"
    assert draft_data["lint_passed"] is False
    assert any(
        i["code"] == "LINT_SCORE_ORDER" for i in draft_data["lint_report_json"]["issues"]
    )
    assert any(
        i["code"] == "LINT_STATE_FIELD_REF"
        for i in draft_data["lint_report_json"]["issues"]
    )


async def test_post_deterministic_dimensions_validates_all_four_rule_types(
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
) -> None:
    valid_rules = [
        (
            "det_json_match",
            "ROUTING",
            {
                "rule_type": "json_exact_or_in",
                "actual_path": "$.route_type",
                "expected_path": "$.expected_route_types",
            },
        ),
        (
            "det_team_recall",
            "ROUTING",
            {
                "rule_type": "set_recall_precision",
                "actual_path": "$.selected_agent_ids",
                "expected_path": "$.expected_agent_ids",
                "metric": "recall",
            },
        ),
        (
            "det_num_grounding",
            "GROUNDING",
            {
                "rule_type": "numeric_fact_grounding",
                "check_types": ["phone", "amount", "percent", "workdays"],
                "grounding_sources": ["references", "expected_output"],
            },
        ),
        (
            "det_ttft_sla",
            "PERFORMANCE",
            {
                "rule_type": "latency_sla",
                "target_field": "ttft_ms",
                "max_ms": 1500,
            },
        ),
    ]

    for code, category, rule_json in valid_rules:
        resp = await client.post(
            "/api/v1/dimensions",
            headers=admin_headers,
            json={
                "dimension_code": code,
                "dimension_name": f"规则维度 {code}",
                "category": category,
                "evaluator_kind": "DETERMINISTIC",
                "deterministic_rule_json": rule_json,
                "default_threshold": 1.0,
                "default_weight": 1.0,
                "status": "ACTIVE",
            },
        )
        assert resp.status_code == 200, f"{code} failed: {resp.text}"
        assert resp.json()["data"]["version"] == 1
        assert resp.json()["data"]["deterministic_rule_json"]["rule_type"] == rule_json["rule_type"]

    for bad_rule in [
        None,
        {},
        {"rule_type": "regex_arbitrary_eval"},
        {"rule_type": "json_exact_or_in", "actual_path": ""},
        {"rule_type": "latency_sla", "target_field": "ttft_ms", "max_ms": -10},
    ]:
        bad_resp = await client.post(
            "/api/v1/dimensions",
            headers=admin_headers,
            json={
                "dimension_code": "bad_det_dim",
                "dimension_name": "非法确定性规则",
                "category": "ROUTING",
                "evaluator_kind": "DETERMINISTIC",
                "deterministic_rule_json": bad_rule,
                "status": "ACTIVE",
            },
        )
        assert bad_resp.status_code == 400


async def test_suites_crud_and_pinned_dimension_validation(
    client: httpx.AsyncClient,
    db_session: Session,
    admin_headers: dict[str, str],
) -> None:
    from sqlalchemy import select

    # 1. Console JWT required for all /suites endpoints
    for method, path, payload in [
        ("GET", "/api/v1/suites", None),
        ("GET", "/api/v1/suites/suite_gov_e2e_golden_v1", None),
        ("POST", "/api/v1/suites", {}),
        ("PUT", "/api/v1/suites/suite_gov_e2e_golden_v1", {}),
    ]:
        unauth = await client.request(method, path, json=payload)
        assert unauth.status_code == 401

    # 2. GET /suites and GET /suites/{suite_code} on seeded suites
    list_resp = await client.get("/api/v1/suites", headers=admin_headers)
    assert list_resp.status_code == 200
    suites = list_resp.json()["data"]
    assert {s["suite_code"] for s in suites} == {
        "suite_gov_e2e_golden_v1",
        "suite_gov_comp_routing_v1",
    }

    e2e_only = await client.get(
        "/api/v1/suites", headers=admin_headers, params={"eval_level": "E2E"}
    )
    assert [s["suite_code"] for s in e2e_only.json()["data"]] == [
        "suite_gov_e2e_golden_v1"
    ]

    detail_resp = await client.get(
        "/api/v1/suites/suite_gov_e2e_golden_v1", headers=admin_headers
    )
    assert detail_resp.status_code == 200
    e2e_suite = detail_resp.json()["data"]
    assert e2e_suite["pass_policy"] == "ALL_BLOCKERS_AND_WEIGHTED"
    assert e2e_suite["suite_pass_threshold"] == 0.75
    assert e2e_suite["confidence_review_floor"] == 0.50
    assert e2e_suite["enable_semantic_regression"] is True
    assert len(e2e_suite["dimensions_json"]) == 4

    missing_suite = await client.get(
        "/api/v1/suites/not_a_suite", headers=admin_headers
    )
    assert missing_suite.status_code == 404

    # 3. Prepare dimension versions: rag_faithfulness_s1 v2 (ACTIVE) and v3 (DRAFT),
    # plus a DEPRECATED dimension
    v2_dim = await client.post(
        "/api/v1/dimensions",
        headers=admin_headers,
        json={
            "dimension_code": "rag_faithfulness_s1",
            "dimension_name": "知识引用忠实度 (v2)",
            "category": "GROUNDING",
            "evaluator_kind": "SYSTEM_ONE",
            "state_fields_json": ["input", "actual_output", "references"],
            "questions_json": [
                {
                    "key": "strict_grounding",
                    "type": "noul",
                    "weight": 1.0,
                    "instructions": "actual_output 中每一项政策条件都在 references 中有明确依据。",
                }
            ],
            "default_threshold": 0.85,
            "default_weight": 2.0,
            "status": "ACTIVE",
        },
    )
    assert v2_dim.status_code == 200
    assert v2_dim.json()["data"]["version"] == 2

    v3_draft = await client.post(
        "/api/v1/dimensions",
        headers=admin_headers,
        json={
            "dimension_code": "rag_faithfulness_s1",
            "dimension_name": "知识引用忠实度 (v3 草稿)",
            "category": "GROUNDING",
            "evaluator_kind": "SYSTEM_ONE",
            "state_fields_json": ["input", "actual_output", "references"],
            "questions_json": [
                {
                    "key": "strict_grounding",
                    "type": "noul",
                    "weight": 1.0,
                    "instructions": "actual_output 无编造 references 之外的信息。",
                }
            ],
            "status": "DRAFT",
        },
    )
    assert v3_draft.status_code == 200
    assert v3_draft.json()["data"]["version"] == 3

    deprecated_dim = await client.post(
        "/api/v1/dimensions",
        headers=admin_headers,
        json={
            "dimension_code": "legacy_route_dim",
            "dimension_name": "已废弃路由维度",
            "category": "ROUTING",
            "evaluator_kind": "DETERMINISTIC",
            "deterministic_rule_json": {
                "rule_type": "json_exact_or_in",
                "actual_path": "$.actual_agent_id",
                "expected_path": "$.expected_agent_ids",
            },
            "status": "DEPRECATED",
        },
    )
    assert deprecated_dim.status_code == 200

    e2e_adapter = db_session.scalar(
        select(SutAdapter).where(SutAdapter.adapter_code == "gov_e2e_routing_blocking")
    )
    comp_adapter = db_session.scalar(
        select(SutAdapter).where(SutAdapter.adapter_code == "gov_comp_routing_harness")
    )
    default_provider = db_session.scalar(
        select(DecisionProvider).where(
            DecisionProvider.provider_code == "ollama_local_tev1"
        )
    )
    assert e2e_adapter is not None and comp_adapter is not None and default_provider is not None

    # 4. POST /suites pinning historical v1 (still ACTIVE) succeeds
    valid_suite_payload = {
        "suite_code": "suite_custom_e2e_gate",
        "suite_name": "自定义 E2E 门禁套件",
        "eval_level": "E2E",
        "default_adapter_id": e2e_adapter.id,
        "default_provider_id": default_provider.id,
        "default_model_override": "tev1:0.8b",
        "dimensions_json": [
            {
                "dimension_code": "rag_faithfulness_s1",
                "version": 1,
                "weight": 2.0,
                "threshold": 0.80,
                "is_blocker": True,
            },
            {
                "dimension_code": "numeric_fact_grounding",
                "version": 1,
                "weight": 1.5,
                "threshold": 1.0,
                "is_blocker": True,
            },
        ],
        "pass_policy": "ALL_DIMENSIONS",
        "suite_pass_threshold": 0.82,
        "confidence_review_floor": 0.55,
        "enable_semantic_regression": False,
        "description": "锁定 v1 忠实度与数字核对",
    }
    created_suite_resp = await client.post(
        "/api/v1/suites", headers=admin_headers, json=valid_suite_payload
    )
    assert created_suite_resp.status_code == 200
    created_suite = created_suite_resp.json()["data"]
    assert created_suite["suite_code"] == "suite_custom_e2e_gate"
    assert created_suite["pass_policy"] == "ALL_DIMENSIONS"
    assert created_suite["suite_pass_threshold"] == 0.82
    assert created_suite["confidence_review_floor"] == 0.55
    assert created_suite["enable_semantic_regression"] is False
    assert created_suite["dimensions_json"][0]["version"] == 1

    # Duplicate suite_code on POST -> 400
    dup_resp = await client.post(
        "/api/v1/suites", headers=admin_headers, json=valid_suite_payload
    )
    assert dup_resp.status_code == 400

    # 5. Reject invalid suite configurations (non-existent version, DRAFT v3, DEPRECATED,
    # invalid pass_policy, out-of-range thresholds, bad adapter/provider IDs, mismatched eval_level)
    invalid_mutations = [
        # Non-existent version
        {
            **valid_suite_payload,
            "suite_code": "bad_s1",
            "dimensions_json": [
                {
                    "dimension_code": "rag_faithfulness_s1",
                    "version": 99,
                    "weight": 1.0,
                    "threshold": 0.7,
                    "is_blocker": True,
                }
            ],
        },
        # DRAFT version (v3)
        {
            **valid_suite_payload,
            "suite_code": "bad_s2",
            "dimensions_json": [
                {
                    "dimension_code": "rag_faithfulness_s1",
                    "version": 3,
                    "weight": 1.0,
                    "threshold": 0.7,
                    "is_blocker": True,
                }
            ],
        },
        # DEPRECATED dimension
        {
            **valid_suite_payload,
            "suite_code": "bad_s3",
            "dimensions_json": [
                {
                    "dimension_code": "legacy_route_dim",
                    "version": 1,
                    "weight": 1.0,
                    "threshold": 1.0,
                    "is_blocker": False,
                }
            ],
        },
        # Invalid pass_policy
        {
            **valid_suite_payload,
            "suite_code": "bad_s4",
            "pass_policy": "ANY_ONE_DIMENSION",
        },
        # Out-of-range suite_pass_threshold
        {
            **valid_suite_payload,
            "suite_code": "bad_s5",
            "suite_pass_threshold": 1.25,
        },
        # Out-of-range confidence_review_floor
        {
            **valid_suite_payload,
            "suite_code": "bad_s6",
            "confidence_review_floor": -0.1,
        },
        # Non-existent default_adapter_id
        {
            **valid_suite_payload,
            "suite_code": "bad_s7",
            "default_adapter_id": 999999,
        },
        # Mismatched adapter eval_level (COMPONENT adapter on E2E suite)
        {
            **valid_suite_payload,
            "suite_code": "bad_s8",
            "default_adapter_id": comp_adapter.id,
        },
        # Non-existent default_provider_id
        {
            **valid_suite_payload,
            "suite_code": "bad_s9",
            "default_provider_id": 999999,
        },
    ]
    for bad_payload in invalid_mutations:
        bad_s = await client.post(
            "/api/v1/suites", headers=admin_headers, json=bad_payload
        )
        assert bad_s.status_code == 400, f"Expected 400 for {bad_payload['suite_code']}, got {bad_s.status_code}"

    # 6. PUT /suites/{suite_code}: upgrade pinned dimension from v1 -> v2 and switch pass_policy
    update_payload = {
        "suite_name": "自定义 E2E 门禁套件 (升级忠实度 v2)",
        "eval_level": "E2E",
        "default_adapter_id": e2e_adapter.id,
        "default_provider_id": default_provider.id,
        "dimensions_json": [
            {
                "dimension_code": "rag_faithfulness_s1",
                "version": 2,
                "weight": 2.5,
                "threshold": 0.85,
                "is_blocker": True,
            },
            {
                "dimension_code": "policy_and_action_accuracy_s1",
                "version": 1,
                "weight": 2.0,
                "threshold": 0.75,
                "is_blocker": False,
            },
        ],
        "pass_policy": "ALL_BLOCKERS_AND_WEIGHTED",
        "suite_pass_threshold": 0.80,
        "confidence_review_floor": 0.60,
        "enable_semantic_regression": True,
        "status": "ACTIVE",
        "description": "已升级锁定 v2 忠实度",
    }
    put_resp = await client.put(
        "/api/v1/suites/suite_custom_e2e_gate",
        headers=admin_headers,
        json=update_payload,
    )
    assert put_resp.status_code == 200
    updated = put_resp.json()["data"]
    assert updated["suite_name"] == "自定义 E2E 门禁套件 (升级忠实度 v2)"
    assert updated["pass_policy"] == "ALL_BLOCKERS_AND_WEIGHTED"
    assert updated["suite_pass_threshold"] == 0.80
    assert updated["confidence_review_floor"] == 0.60
    assert updated["enable_semantic_regression"] is True
    assert updated["dimensions_json"][0] == {
        "dimension_code": "rag_faithfulness_s1",
        "version": 2,
        "weight": 2.5,
        "threshold": 0.85,
        "is_blocker": True,
    }

    # PUT attempting to pin DRAFT v3 or duplicate dimension_code -> 400
    put_draft = await client.put(
        "/api/v1/suites/suite_custom_e2e_gate",
        headers=admin_headers,
        json={
            **update_payload,
            "dimensions_json": [
                {
                    "dimension_code": "rag_faithfulness_s1",
                    "version": 3,
                    "weight": 1.0,
                    "threshold": 0.75,
                    "is_blocker": True,
                }
            ],
        },
    )
    assert put_draft.status_code == 400

    put_dup_dim = await client.put(
        "/api/v1/suites/suite_custom_e2e_gate",
        headers=admin_headers,
        json={
            **update_payload,
            "dimensions_json": [
                {
                    "dimension_code": "rag_faithfulness_s1",
                    "version": 1,
                    "weight": 1.0,
                    "threshold": 0.75,
                    "is_blocker": True,
                },
                {
                    "dimension_code": "rag_faithfulness_s1",
                    "version": 2,
                    "weight": 1.0,
                    "threshold": 0.85,
                    "is_blocker": False,
                },
            ],
        },
    )
    assert put_dup_dim.status_code == 400

    # PUT against missing suite -> 404
    put_missing = await client.put(
        "/api/v1/suites/unknown_suite_code",
        headers=admin_headers,
        json=update_payload,
    )
    assert put_missing.status_code == 404


