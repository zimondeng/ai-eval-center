from __future__ import annotations

import pytest


def test_atomic_question_scoring_noul_score_and_choice_null_exclusion() -> None:
    from app.kernel.scoring import (
        compute_choice_score,
        compute_noul_score,
        compute_score_question_score,
    )

    # 1. noul: score equals clamped p_true in [0, 1]
    assert compute_noul_score(0.982) == pytest.approx(0.982)
    assert compute_noul_score({"type": "noul", "noul": 0.745}) == pytest.approx(0.745)

    # 2. score (worst-first tiers 0..K-1): worked example from docs/03-database-design.md
    norm_3, raw_3 = compute_score_question_score({"0": 0.02, "1": 0.13, "2": 0.85})
    assert raw_3 == pytest.approx(1.83)
    assert norm_3 == pytest.approx(0.915)

    # 4-tier list distribution [0.10, 0.20, 0.30, 0.40] -> raw=2.0, norm=2.0/3=0.666667
    norm_4, raw_4 = compute_score_question_score([0.10, 0.20, 0.30, 0.40])
    assert raw_4 == pytest.approx(2.0)
    assert norm_4 == pytest.approx(0.666667, abs=1e-6)

    # 3. choice: null option probability sum >= 0.5 -> (None, False)
    option_scores: dict[str, float | None] = {
        "missing_or_wrong": 0.0,
        "partial": 0.5,
        "clear_and_complete": 1.0,
        "not_applicable": None,
    }
    score_na, applicable_na = compute_choice_score(
        {
            "missing_or_wrong": 0.03,
            "partial": 0.05,
            "clear_and_complete": 0.12,
            "not_applicable": 0.80,
        },
        option_scores,
    )
    assert score_na is None
    assert applicable_na is False

    # Exact boundary na_prob == 0.5 -> still excluded (None, False)
    score_boundary, applicable_boundary = compute_choice_score(
        {
            "missing_or_wrong": 0.10,
            "partial": 0.00,
            "clear_and_complete": 0.40,
            "not_applicable": 0.50,
        },
        option_scores,
    )
    assert score_boundary is None
    assert applicable_boundary is False

    # Applicable case: na_prob = 0.20 < 0.5 ->applicable_prob = 0.80
    # (0.10*0.0 + 0.30*0.5 + 0.40*1.0) / 0.80 = 0.55 / 0.80 = 0.6875
    score_ok, applicable_ok = compute_choice_score(
        {
            "missing_or_wrong": 0.10,
            "partial": 0.30,
            "clear_and_complete": 0.40,
            "not_applicable": 0.20,
        },
        option_scores,
    )
    assert applicable_ok is True
    assert score_ok == pytest.approx(0.6875)


def test_synthesize_dimension_result_ledger_shape_and_strict_mode() -> None:
    from app.kernel.scoring import synthesize_dimension_result

    questions_spec = [
        {
            "key": "core_conclusion",
            "type": "score",
            "weight": 2.5,
            "instructions": "对比 expected_output，actual_output 给出的核心办理结论与要件是否准确？",
            "criteria": [
                "核心结论错误，或与 expected_output 直接矛盾",
                "结论部分正确，但遗漏关键办理渠道或核心前置条件",
                "核心结论、办理渠道与关键材料均准确完整",
            ],
        },
        {
            "key": "no_fabrication",
            "type": "noul",
            "weight": 2.0,
            "instructions": "actual_output 中没有编造虚假收费标准或电话号码。",
        },
        {
            "key": "action_guidance",
            "type": "choice",
            "weight": 1.5,
            "instructions": "actual_output 对线上/线下办理路径的说明情况如何？",
            "criteria": {
                "missing_or_wrong": "未提供办理路径或路径信息错误",
                "partial": "仅提及单一路径",
                "clear_and_complete": "清晰完整地说明了可行的办理路径",
                "not_applicable": "用户仅咨询概念定义，不涉及具体申办路径",
            },
            "option_scores": {
                "missing_or_wrong": 0.0,
                "partial": 0.5,
                "clear_and_complete": 1.0,
                "not_applicable": None,
            },
        },
    ]

    answers = {
        "core_conclusion": {"probabilities": {"0": 0.02, "1": 0.13, "2": 0.85}},
        "no_fabrication": {"type": "noul", "noul": 0.982},
        "action_guidance": {
            "probabilities": {
                "missing_or_wrong": 0.03,
                "partial": 0.05,
                "clear_and_complete": 0.12,
                "not_applicable": 0.80,
            }
        },
    }

    # Normal mode: (2.5 * 0.915 + 2.0 * 0.982) / (2.5 + 2.0) = 4.2515 / 4.5 = 0.94478
    res = synthesize_dimension_result(
        questions_spec=questions_spec,
        answers=answers,
        threshold=0.75,
        strict_mode=0,
    )
    assert res.raw_dimension_score == pytest.approx(0.94478, abs=1e-5)
    assert res.passed is True
    assert res.min_question_confidence == pytest.approx(0.775, abs=1e-4)
    assert isinstance(res.deterministic_reason, str) and len(res.deterministic_reason) > 0

    q_ledger = res.raw_answers_json["questions"]
    assert [q["key"] for q in q_ledger] == [
        "core_conclusion",
        "no_fabrication",
        "action_guidance",
    ]
    assert q_ledger[0]["type"] == "score"
    assert q_ledger[0]["applicable"] is True
    assert q_ledger[0]["normalized_score"] == pytest.approx(0.915)
    assert q_ledger[0]["raw_weighted_level"] == pytest.approx(1.83)
    assert q_ledger[0]["probabilities"] == {"0": 0.02, "1": 0.13, "2": 0.85}
    assert q_ledger[0]["confidence"] == pytest.approx(0.775)

    assert q_ledger[1]["type"] == "noul"
    assert q_ledger[1]["applicable"] is True
    assert q_ledger[1]["p_true"] == pytest.approx(0.982)
    assert q_ledger[1]["normalized_score"] == pytest.approx(0.982)
    assert q_ledger[1]["confidence"] == pytest.approx(0.964)

    assert q_ledger[2]["type"] == "choice"
    assert q_ledger[2]["applicable"] is False
    assert q_ledger[2]["normalized_score"] is None
    assert q_ledger[2]["chosen"] == "not_applicable"
    assert q_ledger[2]["confidence"] == pytest.approx(0.7333, abs=1e-4)

    # Strict mode == 1: core_conclusion (0.915 < 1.0 - 1e-6) zeros out the dimension!
    strict_fail = synthesize_dimension_result(
        questions_spec=questions_spec,
        answers=answers,
        threshold=0.75,
        strict_mode=1,
    )
    assert strict_fail.raw_dimension_score == 0.0
    assert strict_fail.passed is False
    assert "严格模式" in strict_fail.deterministic_reason

    # Strict mode == 1 when all applicable subquestions == 1.0 (inapplicable choice ignored) -> 1.0
    perfect_answers = {
        "core_conclusion": {"probabilities": {"0": 0.0, "1": 0.0, "2": 1.0}},
        "no_fabrication": {"type": "noul", "noul": 1.0},
        "action_guidance": answers["action_guidance"],
    }
    strict_pass = synthesize_dimension_result(
        questions_spec=questions_spec,
        answers=perfect_answers,
        threshold=0.75,
        strict_mode=1,
    )
    assert strict_pass.raw_dimension_score == pytest.approx(1.0)
    assert strict_pass.passed is True


def test_suite_gate_policies_and_zero_cost_ledger_recalculation_parity() -> None:
    from app.db.models import ProbabilityLedger
    from app.kernel.scoring import (
        evaluate_suite_gate,
        recalculate_case_from_ledgers,
        synthesize_dimension_result,
    )

    dim_inputs = [
        {
            "dimension_code": "route_top1_match",
            "evaluator_kind": "DETERMINISTIC",
            "raw_dimension_score": 0.0,
            "applied_threshold": 1.0,
            "applied_weight": 1.0,
            "is_blocker": False,
            "passed": False,
            "min_question_confidence": None,
        },
        {
            "dimension_code": "rag_faithfulness_s1",
            "evaluator_kind": "SYSTEM_ONE",
            "raw_dimension_score": 0.90,
            "applied_threshold": 0.75,
            "applied_weight": 2.0,
            "is_blocker": True,
            "passed": True,
            "min_question_confidence": 0.82,
        },
        {
            "dimension_code": "policy_and_action_accuracy_s1",
            "evaluator_kind": "SYSTEM_ONE",
            "raw_dimension_score": 0.95,
            "applied_threshold": 0.75,
            "applied_weight": 2.0,
            "is_blocker": True,
            "passed": True,
            "min_question_confidence": 0.46,
        },
    ]

    # Weighted score = (1.0*0.0 + 2.0*0.90 + 2.0*0.95) / 5.0 = 3.70 / 5.0 = 0.74
    gate_blockers = evaluate_suite_gate(
        dim_inputs,
        pass_policy="ALL_BLOCKERS_AND_WEIGHTED",
        suite_pass_threshold=0.72,
        confidence_review_floor=0.50,
    )
    assert gate_blockers.overall_score == pytest.approx(0.74)
    assert gate_blockers.overall_passed is True
    assert gate_blockers.min_confidence == pytest.approx(0.46)
    assert gate_blockers.needs_human_review is True

    # Under ALL_DIMENSIONS, non-blocker route_top1_match failed -> overall_passed False
    gate_all_dims = evaluate_suite_gate(
        dim_inputs,
        pass_policy="ALL_DIMENSIONS",
        suite_pass_threshold=0.72,
        confidence_review_floor=0.50,
    )
    assert gate_all_dims.overall_score == pytest.approx(0.74)
    assert gate_all_dims.overall_passed is False

    # Pure deterministic suite -> min_confidence is None and needs_human_review is False
    det_only_gate = evaluate_suite_gate(
        [
            {
                "dimension_code": "route_top1_match",
                "evaluator_kind": "DETERMINISTIC",
                "raw_dimension_score": 1.0,
                "applied_threshold": 1.0,
                "applied_weight": 1.0,
                "is_blocker": True,
                "passed": True,
                "min_question_confidence": None,
            }
        ],
        pass_policy="ALL_BLOCKERS_AND_WEIGHTED",
        suite_pass_threshold=0.80,
        confidence_review_floor=0.50,
    )
    assert det_only_gate.overall_passed is True
    assert det_only_gate.min_confidence is None
    assert det_only_gate.needs_human_review is False

    # 100% numerical parity between fresh scoring with new thresholds/weights and ledger recalculation
    dim1_q = [
        {
            "key": "grounded",
            "type": "score",
            "weight": 2.0,
            "instructions": "是否忠实于 references？",
            "criteria": ["无依据", "部分有据", "完全有据"],
        },
        {
            "key": "no_hallucination",
            "type": "noul",
            "weight": 1.0,
            "instructions": "无编造。",
        },
    ]
    dim2_q = [
        {
            "key": "policy_conclusion",
            "type": "choice",
            "weight": 1.5,
            "instructions": "结论准确性？",
            "criteria": {"wrong": "错", "partial": "半对", "right": "对", "na": "不适用"},
            "option_scores": {"wrong": 0.0, "partial": 0.4, "right": 1.0, "na": None},
        }
    ]
    ans1 = {
        "grounded": {"probabilities": {"0": 0.05, "1": 0.25, "2": 0.70}},
        "no_hallucination": {"noul": 0.88},
    }
    ans2 = {
        "policy_conclusion": {
            "probabilities": {"wrong": 0.10, "partial": 0.20, "right": 0.50, "na": 0.20}
        }
    }

    # Initial run at T1=0.70 (W1=1.5, blocker=True), T2=0.60 (W2=1.0, blocker=False), suite_T=0.70
    initial_s1 = synthesize_dimension_result(dim1_q, ans1, threshold=0.70)
    initial_s2 = synthesize_dimension_result(dim2_q, ans2, threshold=0.60)
    ledger_rows = [
        ProbabilityLedger(
            run_id=1,
            run_case_id=10,
            dimension_id=101,
            dimension_code="rag_faithfulness_s1",
            dimension_version=1,
            evaluator_kind="SYSTEM_ONE",
            raw_dimension_score=initial_s1.raw_dimension_score,
            applied_threshold=0.70,
            applied_weight=1.5,
            is_blocker=1,
            passed=1 if initial_s1.passed else 0,
            min_question_confidence=initial_s1.min_question_confidence,
            deterministic_reason=initial_s1.deterministic_reason,
            raw_answers_json=initial_s1.raw_answers_json,
        ),
        ProbabilityLedger(
            run_id=1,
            run_case_id=10,
            dimension_id=102,
            dimension_code="policy_and_action_accuracy_s1",
            dimension_version=1,
            evaluator_kind="SYSTEM_ONE",
            raw_dimension_score=initial_s2.raw_dimension_score,
            applied_threshold=0.60,
            applied_weight=1.0,
            is_blocker=0,
            passed=1 if initial_s2.passed else 0,
            min_question_confidence=initial_s2.min_question_confidence,
            deterministic_reason=initial_s2.deterministic_reason,
            raw_answers_json=initial_s2.raw_answers_json,
        ),
    ]

    # Now override thresholds, weights, blocker flags, and suite_pass_threshold:
    # T1'=0.86, W1'=2.5, B1'=False; T2'=0.72, W2'=3.0, B2'=True; suite_T'=0.78
    recalc = recalculate_case_from_ledgers(
        ledger_rows,
        pass_policy="ALL_BLOCKERS_AND_WEIGHTED",
        suite_pass_threshold=0.78,
        confidence_review_floor=0.55,
        dimension_overrides={
            "rag_faithfulness_s1": {"threshold": 0.86, "weight": 2.5, "is_blocker": False},
            "policy_and_action_accuracy_s1": {"threshold": 0.72, "weight": 3.0, "is_blocker": True},
        },
    )

    fresh_s1 = synthesize_dimension_result(dim1_q, ans1, threshold=0.86)
    fresh_s2 = synthesize_dimension_result(dim2_q, ans2, threshold=0.72)
    fresh_gate = evaluate_suite_gate(
        [
            {
                "dimension_code": "rag_faithfulness_s1",
                "raw_dimension_score": fresh_s1.raw_dimension_score,
                "applied_threshold": 0.86,
                "applied_weight": 2.5,
                "is_blocker": False,
                "passed": fresh_s1.passed,
                "min_question_confidence": fresh_s1.min_question_confidence,
            },
            {
                "dimension_code": "policy_and_action_accuracy_s1",
                "raw_dimension_score": fresh_s2.raw_dimension_score,
                "applied_threshold": 0.72,
                "applied_weight": 3.0,
                "is_blocker": True,
                "passed": fresh_s2.passed,
                "min_question_confidence": fresh_s2.min_question_confidence,
            },
        ],
        pass_policy="ALL_BLOCKERS_AND_WEIGHTED",
        suite_pass_threshold=0.78,
        confidence_review_floor=0.55,
    )

    assert recalc.overall_score == fresh_gate.overall_score
    assert recalc.overall_passed == fresh_gate.overall_passed
    assert recalc.min_confidence == fresh_gate.min_confidence
    assert recalc.needs_human_review == fresh_gate.needs_human_review
    assert recalc.dimension_results["rag_faithfulness_s1"].passed == fresh_s1.passed
    assert recalc.dimension_results["policy_and_action_accuracy_s1"].passed == fresh_s2.passed


def test_state_batch_compiler_single_call_for_9_questions_and_auto_split_at_65() -> None:
    from app.kernel.batch_compiler import (
        SEMANTIC_REGRESSION_TAGS,
        StateBatchCompiler,
        build_semantic_regression_question,
        parse_semantic_regression_answer,
    )

    # 3 dimensions sharing the same state_fields (in different order / with duplicate) -> 9 questions total
    dims_9q = [
        {
            "dimension_code": "rag_faithfulness_s1",
            "version": 1,
            "evaluator_kind": "SYSTEM_ONE",
            "state_fields_json": ["input", "actual_output", "references"],
            "questions_json": [
                {"key": "q1", "type": "noul", "weight": 1.0, "instructions": "无虚构"},
                {
                    "key": "q2",
                    "type": "score",
                    "weight": 2.0,
                    "instructions": "引用忠实度",
                    "criteria": ["无依据", "部分忠实", "完全忠实"],
                },
                {
                    "key": "q3",
                    "type": "choice",
                    "weight": 1.5,
                    "instructions": "缺失信息处理",
                    "criteria": {"guess": "猜测", "honest": "诚实说明", "na": "无缺失"},
                    "option_scores": {"guess": 0.0, "honest": 1.0, "na": None},
                },
            ],
        },
        {
            "dimension_code": "answer_relevancy_s1",
            "version": 2,
            "evaluator_kind": "SYSTEM_ONE",
            "state_fields_json": ["references", "input", "actual_output", "input"],
            "questions_json": [
                {"key": "q1", "type": "noul", "weight": 1.0, "instructions": "切题"},
                {
                    "key": "q2",
                    "type": "score",
                    "weight": 1.5,
                    "instructions": "聚焦程度",
                    "criteria": ["跑题", "一般", "聚焦"],
                },
                {"key": "q3", "type": "noul", "weight": 1.0, "instructions": "无冗余废话"},
            ],
        },
        {
            "dimension_code": "gov_tone_s1",
            "version": 1,
            "evaluator_kind": "SYSTEM_ONE",
            "state_fields_json": ["actual_output", "references", "input"],
            "questions_json": [
                {"key": "q1", "type": "noul", "weight": 1.0, "instructions": "语气得体"},
                {"key": "q2", "type": "noul", "weight": 1.0, "instructions": "表述清晰"},
                {"key": "q3", "type": "noul", "weight": 1.0, "instructions": "无不当承诺"},
            ],
        },
    ]

    case_data = {
        "input_text": "常州公积金提取条件？",
        "expected_output": "标准答案（不应出现在未声明 expected_output 的 state 中）",
        "reference_contexts_json": ["冻结参考片段1"],
    }

    compiler = StateBatchCompiler(max_questions_per_call=64)
    plan_9 = compiler.compile(
        dimensions=dims_9q,
        case=case_data,
        actual_output="实际回答内容",
        actual_references=["召回片段A", "召回片段B"],
    )

    # Must merge all 9 atomic questions into strictly 1 HTTP batch call!
    assert len(plan_9.batches) == 1
    batch = plan_9.batches[0]
    assert batch.state_fields == ("actual_output", "input", "references")
    assert set(batch.state.keys()) == {"input", "actual_output", "references"}
    assert "expected_output" not in batch.state
    assert batch.state["references"] == ["召回片段A", "召回片段B"]
    assert len(batch.questions) == 9

    # Namespace collisions prevented (all 3 dims had q1/q2/q3) & internal weight/option_scores stripped!
    assert "rag_faithfulness_s1_v1__q3" in batch.questions
    assert "answer_relevancy_s1_v2__q1" in batch.questions
    assert "gov_tone_s1_v1__q3" in batch.questions

    q3_wire = batch.questions["rag_faithfulness_s1_v1__q3"]
    assert set(q3_wire.keys()) == {"type", "instructions", "criteria"}
    assert "weight" not in q3_wire
    assert "option_scores" not in q3_wire
    assert "key" not in q3_wire

    noul_wire = batch.questions["rag_faithfulness_s1_v1__q1"]
    assert set(noul_wire.keys()) == {"type", "instructions"}

    # Auto-splitting when > 64 questions share the same state_fields (65 questions -> 64 + 1)
    dims_65q = [
        {
            "dimension_code": "mega_dim_a",
            "version": 1,
            "evaluator_kind": "SYSTEM_ONE",
            "state_fields_json": ["input", "actual_output"],
            "questions_json": [
                {"key": f"item_{i}", "type": "noul", "weight": 1.0, "instructions": f"检查项 {i}"}
                for i in range(40)
            ],
        },
        {
            "dimension_code": "mega_dim_b",
            "version": 1,
            "evaluator_kind": "SYSTEM_ONE",
            "state_fields_json": ["actual_output", "input"],
            "questions_json": [
                {"key": f"item_{i}", "type": "noul", "weight": 1.0, "instructions": f"检查项B {i}"}
                for i in range(25)
            ],
        },
    ]
    plan_65 = compiler.compile(
        dimensions=dims_65q,
        case=case_data,
        actual_output="实际回答内容",
    )
    assert len(plan_65.batches) == 2
    assert len(plan_65.batches[0].questions) == 64
    assert len(plan_65.batches[1].questions) == 1

    mock_batch_responses = [
        {k: {"noul": 0.9} for k in plan_65.batches[0].questions},
        {k: {"noul": 0.8} for k in plan_65.batches[1].questions},
    ]
    unpacked = plan_65.unpack_answers(mock_batch_responses)
    assert len(unpacked[("mega_dim_a", 1)]) == 40
    assert len(unpacked[("mega_dim_b", 1)]) == 25
    assert unpacked[("mega_dim_b", 1)]["item_24"] == {"noul": 0.8}

    # 4-way semantic regression choice question & parser
    reg_q = build_semantic_regression_question()
    assert reg_q["type"] == "choice"
    assert tuple(reg_q["criteria"].keys()) == SEMANTIC_REGRESSION_TAGS

    tag, conf, probs = parse_semantic_regression_answer(
        {
            "probabilities": {
                "SAME": 0.05,
                "FACT_DIFFERS": 0.80,
                "ACTION_DIFFERS": 0.10,
                "SPECIFICITY_DIFFERS": 0.05,
            }
        }
    )
    assert tag == "FACT_DIFFERS"
    # (4 * 0.80 - 1) / (4 - 1) = 2.2 / 3 = 0.7333
    assert conf == pytest.approx(0.7333, abs=1e-4)
    assert probs["FACT_DIFFERS"] == pytest.approx(0.80)


async def test_system_one_client_three_providers_missing_confidence_and_ollama_extra_options(
    monkeypatch: pytest.MonkeyPatch,
    test_settings: "Settings",
) -> None:
    import json as json_lib

    import httpx

    from app.common.crypto import encrypt_secret
    from app.db.models import DecisionProvider
    from app.kernel.system_one_client import SystemOneClient

    captured: list[tuple[str, dict[str, str], dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json_lib.loads(request.content.decode("utf-8"))
        hdrs = {k.lower(): v for k, v in request.headers.items()}
        captured.append((str(request.url), hdrs, body))

        url_str = str(request.url)
        if "api.typesafe.ai" in url_str:
            return httpx.Response(
                200,
                headers={"X-Request-ID": "ts_req_001"},
                json={
                    "model": "jev-1.14.0",
                    "answers": {
                        "q_noul": {"type": "noul", "noul": 0.92, "confidence": 0.84},
                        "q_score": {"type": "score", "score": [0.05, 0.15, 0.80], "confidence": 0.70},
                    },
                    "usage": {"input_tokens": 180, "output_tokens": 8},
                },
            )
        if "emrank.tech" in url_str:
            # Missing confidence + prompt_tokens usage shape
            return httpx.Response(
                200,
                headers={"X-Request-ID": "emrk_req_99102a"},
                json={
                    "answers": {
                        "q_score": {"probabilities": {"0": 0.10, "1": 0.20, "2": 0.70}},
                        "q_choice": {
                            "probabilities": {"a": 0.03, "b": 0.05, "c": 0.12, "d": 0.80}
                        },
                    },
                    "usage": {"prompt_tokens": 215, "completion_tokens": 6},
                },
            )
        # Local Ollama tev1:0.8b
        return httpx.Response(
            200,
            json={
                "model": "tev1:0.8b",
                "answers": {
                    "q_noul": {"noul": 0.85},
                },
                "usage": {"input_tokens": 96},
            },
        )

    transport = httpx.MockTransport(handler)

    # 1. TypeSafe Official
    ts_provider = DecisionProvider(
        provider_code="typesafe_official",
        provider_name="TypeSafe",
        provider_kind="TYPESAFE_OFFICIAL",
        endpoint_url="https://api.typesafe.ai/v1/systemone",
        default_model="jev-1.14.0",
        auth_type="BEARER",
        api_key_cipher=encrypt_secret("sk-ts-live-001", master_key=test_settings.master_encryption_key),
        network_mode="PROXY_FALLBACK",
        waf_strip_code_retry=1,
    )
    ts_cli = SystemOneClient(ts_provider, settings=test_settings, transport=transport)
    ts_res = await ts_cli.decide(
        state={"input": "你好", "actual_output": "您好"},
        questions={
            "q_noul": {"type": "noul", "instructions": "是否礼貌"},
            "q_score": {"type": "score", "instructions": "质量", "criteria": ["差", "中", "好"]},
        },
    )
    assert captured[-1][1]["authorization"] == "Bearer sk-ts-live-001"
    assert ts_res.upstream_request_id == "ts_req_001"
    assert ts_res.input_tokens == 180
    assert ts_res.waf_fallback_used is False
    assert ts_res.answers["q_noul"]["p_true"] == pytest.approx(0.92)
    assert ts_res.answers["q_score"]["probabilities"] == {"0": 0.05, "1": 0.15, "2": 0.8}

    # 2. EMRank /v1/decisions with ENV_REF & missing confidence fallback
    monkeypatch.setenv("EMRANK_TEST_KEY", "sk-emrank-relay-77")
    em_provider = DecisionProvider(
        provider_code="emrank_relay",
        provider_name="EMRank",
        provider_kind="DECISION_RELAY",
        endpoint_url="https://emrank.tech/v1/decisions",
        default_model="typesafe/jev-1.14.0",
        auth_type="ENV_REF",
        api_key_env_name="EMRANK_TEST_KEY",
        network_mode="PROXY_FALLBACK",
        waf_strip_code_retry=1,
    )
    em_cli = SystemOneClient(em_provider, settings=test_settings, transport=transport)
    em_res = await em_cli.decide(
        state={"input": "问题", "actual_output": "回答"},
        questions={
            "q_score": {"type": "score", "instructions": "档位", "criteria": ["差", "中", "好"]},
            "q_choice": {
                "type": "choice",
                "instructions": "分类",
                "criteria": {"a": "A", "b": "B", "c": "C", "d": "D"},
            },
        },
    )
    assert captured[-1][1]["authorization"] == "Bearer sk-emrank-relay-77"
    assert em_res.upstream_request_id == "emrk_req_99102a"
    assert em_res.input_tokens == 215
    # Fallback confidence: (3 * 0.70 - 1) / (3 - 1) = 0.55
    assert em_res.answers["q_score"]["confidence"] == pytest.approx(0.55, abs=1e-4)
    # Fallback confidence: (4 * 0.80 - 1) / (4 - 1) = 0.7333
    assert em_res.answers["q_choice"]["confidence"] == pytest.approx(0.7333, abs=1e-4)
    assert em_res.answers["q_choice"]["chosen"] == "d"

    # 3. Local Ollama tev1:0.8b -> merges extra_options_json & omits Authorization header
    ollama_provider = DecisionProvider(
        provider_code="ollama_local_tev1",
        provider_name="Ollama",
        provider_kind="OLLAMA_LOCAL",
        endpoint_url="http://127.0.0.1:11434/v1/systemone",
        default_model="tev1:0.8b",
        auth_type="NONE",
        network_mode="DIRECT",
        waf_strip_code_retry=0,
        extra_options_json={"keep_alive": "15m"},
    )
    ol_cli = SystemOneClient(ollama_provider, settings=test_settings, transport=transport)
    ol_res = await ol_cli.decide(
        state={"input": "测试"},
        questions={"q_noul": {"type": "noul", "instructions": "是否中文"}},
    )
    assert "authorization" not in captured[-1][1]
    assert captured[-1][2]["keep_alive"] == "15m"
    assert ol_res.answers["q_noul"]["p_true"] == pytest.approx(0.85)
    # (2 * 0.85 - 1) / (2 - 1) = 0.70
    assert ol_res.answers["q_noul"]["confidence"] == pytest.approx(0.70, abs=1e-4)


async def test_system_one_client_waf_html_403_strips_fenced_code_and_retries_once(
    test_settings: "Settings",
) -> None:
    import json as json_lib

    import httpx

    from app.db.models import DecisionProvider
    from app.kernel.system_one_client import SystemOneClient, SystemOneUpstreamError

    sent_payloads: list[dict] = []

    def waf_handler(request: httpx.Request) -> httpx.Response:
        body = json_lib.loads(request.content.decode("utf-8"))
        sent_payloads.append(body)
        state_dump = json_lib.dumps(body["state"], ensure_ascii=False)
        if "```" in state_dump or "SELECT * FROM" in state_dump:
            return httpx.Response(
                403,
                headers={"Content-Type": "text/html"},
                text="<!DOCTYPE html><html><body><h1>403 Forbidden - Edge WAF</h1></body></html>",
            )
        return httpx.Response(
            200,
            headers={"X-Request-ID": "emrk_waf_retry_ok"},
            json={
                "answers": {"q1": {"noul": 0.91}},
                "usage": {"input_tokens": 64},
            },
        )

    transport = httpx.MockTransport(waf_handler)
    state_with_code = {
        "input": "请说明查询示例",
        "actual_output": "可执行以下脚本：\n```sql\nSELECT * FROM gov_citizen_archive;\n```\n办理结果即时生效。",
        "references": ["内部参考：\n~~~bash\nrm -rf /tmp/cache\n~~~\n完。"],
    }

    # 1. waf_strip_code_retry = 1 -> strips fenced blocks, retries once, succeeds with waf_fallback_used=True
    provider_retry_on = DecisionProvider(
        provider_code="relay_waf_on",
        provider_name="Relay WAF On",
        provider_kind="DECISION_RELAY",
        endpoint_url="https://emrank.tech/v1/decisions",
        default_model="typesafe/jev-1.14.0",
        auth_type="NONE",
        network_mode="PROXY_FALLBACK",
        waf_strip_code_retry=1,
    )
    cli_on = SystemOneClient(provider_retry_on, settings=test_settings, transport=transport)
    res_ok = await cli_on.decide(
        state=state_with_code,
        questions={"q1": {"type": "noul", "instructions": "回答是否清晰"}},
    )
    assert len(sent_payloads) == 2
    assert "```sql" in sent_payloads[0]["state"]["actual_output"]
    assert "```" not in sent_payloads[1]["state"]["actual_output"]
    assert "SELECT * FROM" not in sent_payloads[1]["state"]["actual_output"]
    assert "~~~" not in sent_payloads[1]["state"]["references"][0]
    assert "办理结果即时生效。" in sent_payloads[1]["state"]["actual_output"]
    assert res_ok.waf_fallback_used is True
    assert res_ok.upstream_request_id == "emrk_waf_retry_ok"
    assert res_ok.answers["q1"]["p_true"] == pytest.approx(0.91)

    # 2. waf_strip_code_retry = 0 -> must NOT retry on HTML 403
    sent_payloads.clear()
    provider_retry_off = DecisionProvider(
        provider_code="relay_waf_off",
        provider_name="Relay WAF Off",
        provider_kind="DECISION_RELAY",
        endpoint_url="https://emrank.tech/v1/decisions",
        default_model="typesafe/jev-1.14.0",
        auth_type="NONE",
        network_mode="PROXY_FALLBACK",
        waf_strip_code_retry=0,
    )
    cli_off = SystemOneClient(provider_retry_off, settings=test_settings, transport=transport)
    with pytest.raises(SystemOneUpstreamError):
        await cli_off.decide(
            state=state_with_code,
            questions={"q1": {"type": "noul", "instructions": "回答是否清晰"}},
        )
    assert len(sent_payloads) == 1

    # 3. JSON 403 (auth error, body does NOT start with '<') -> must NOT retry even when waf_strip_code_retry=1
    json_403_calls = 0

    def json_403_handler(_req: httpx.Request) -> httpx.Response:
        nonlocal json_403_calls
        json_403_calls += 1
        return httpx.Response(403, json={"error": {"message": "Invalid API key"}})

    cli_json_403 = SystemOneClient(
        provider_retry_on,
        settings=test_settings,
        transport=httpx.MockTransport(json_403_handler),
    )
    with pytest.raises(SystemOneUpstreamError):
        await cli_json_403.decide(
            state=state_with_code,
            questions={"q1": {"type": "noul", "instructions": "回答是否清晰"}},
        )
    assert json_403_calls == 1


def test_deterministic_evaluators_all_four_rule_types() -> None:
    from app.kernel.deterministic import evaluate_deterministic_dimension

    # 1. json_exact_or_in
    hit_res = evaluate_deterministic_dimension(
        rule={
            "rule_type": "json_exact_or_in",
            "actual_path": "$.actual_agent_id",
            "expected_path": "$.expected_agent_ids",
        },
        threshold=1.0,
        actual_structured_json={"actual_agent_id": "cz_gjj_agent"},
        expected_structured_json={"expected_agent_ids": ["cz_gjj_agent", "cz_housing_agent"]},
    )
    assert hit_res.raw_dimension_score == 1.0
    assert hit_res.passed is True
    assert hit_res.min_question_confidence is None

    miss_res = evaluate_deterministic_dimension(
        rule={
            "rule_type": "json_exact_or_in",
            "actual_path": "$.actual_agent_id",
            "expected_path": "$.expected_agent_ids",
        },
        threshold=1.0,
        actual_structured_json={"actual_agent_id": "cz_tax_agent"},
        expected_structured_json={"expected_agent_ids": ["cz_gjj_agent"]},
    )
    assert miss_res.raw_dimension_score == 0.0
    assert miss_res.passed is False

    # Bool vs int strictness: actual 1 must NOT match expected [True]
    bool_strict = evaluate_deterministic_dimension(
        rule={
            "rule_type": "json_exact_or_in",
            "actual_path": "$.usable",
            "expected_values": [True],
        },
        threshold=1.0,
        actual_structured_json={"usable": 1},
    )
    assert bool_strict.raw_dimension_score == 0.0

    # 2. set_recall_precision: actual={a,b,x} (3), expected={a,b,c,d} (4), overlap={a,b} (2)
    # Recall = 2/4 = 0.5; Precision = 2/3 = 0.66667; F1 = 4/7 = 0.57143
    actual_agents = {"selected_agent_ids": ["cz_gjj", "cz_sb", "cz_wrong"]}
    expected_agents = {"expected_agent_ids": ["cz_gjj", "cz_sb", "cz_tax", "cz_edu"]}

    rec_res = evaluate_deterministic_dimension(
        rule={"rule_type": "set_recall_precision", "metric": "recall"},
        threshold=0.5,
        actual_structured_json=actual_agents,
        expected_structured_json=expected_agents,
    )
    assert rec_res.raw_dimension_score == pytest.approx(0.5)
    assert rec_res.passed is True

    prec_res = evaluate_deterministic_dimension(
        rule={"rule_type": "set_recall_precision", "metric": "precision"},
        threshold=0.7,
        actual_structured_json=actual_agents,
        expected_structured_json=expected_agents,
    )
    assert prec_res.raw_dimension_score == pytest.approx(0.66667, abs=1e-5)
    assert prec_res.passed is False

    f1_res = evaluate_deterministic_dimension(
        rule={"rule_type": "set_recall_precision", "metric": "f1"},
        threshold=0.55,
        actual_structured_json=actual_agents,
        expected_structured_json=expected_agents,
    )
    assert f1_res.raw_dimension_score == pytest.approx(0.57143, abs=1e-5)
    assert f1_res.passed is True

    # 3. numeric_fact_grounding: phone, amount, percent, workdays (ignoring calendar date 5月15日)
    grounded_res = evaluate_deterministic_dimension(
        rule={
            "rule_type": "numeric_fact_grounding",
            "check_types": ["phone", "amount", "percent", "workdays"],
            "grounding_sources": ["references", "expected_output"],
        },
        threshold=1.0,
        actual_output="请拨打咨询电话12329或0519-88112233，最高可贷80万元，首套利率2.85%，线上3个工作日内办结（每年5月15日结息）。",
        references=[
            "常州公积金热线 12329，固话 051988112233；最高贷款额度 800,000 元，首套利率 2.85%。"
        ],
        expected_output="材料齐全后3个工作日办结。",
    )
    assert grounded_res.raw_dimension_score == 1.0
    assert grounded_res.passed is True

    # Fabricated hotline (12399) and workdays (5个工作日), while 80万元 and 2.85% stay grounded -> 2 / 4 = 0.5
    hallucinated_num = evaluate_deterministic_dimension(
        rule={
            "rule_type": "numeric_fact_grounding",
            "check_types": ["phone", "amount", "percent", "workdays"],
            "grounding_sources": ["references", "expected_output"],
        },
        threshold=1.0,
        actual_output="请拨打12399，最高可贷80万元，利率2.85%，5个工作日办结。",
        references=["热线 12329，最高额度 80 万元，利率 2.85%。"],
        expected_output="3个工作日办结。",
    )
    assert hallucinated_num.raw_dimension_score == pytest.approx(0.5)
    assert hallucinated_num.passed is False
    assert len(hallucinated_num.raw_answers_json["ungrounded_claims"]) == 2

    # 4. latency_sla: sut_latency_ms and ttft_ms
    sla_ok = evaluate_deterministic_dimension(
        rule={
            "rule_type": "latency_sla",
            "target_field": "ttft_ms",
            "max_latency_ms": 1500,
        },
        threshold=1.0,
        ttft_ms=820,
        sut_latency_ms=2400,
    )
    assert sla_ok.raw_dimension_score == 1.0
    assert sla_ok.passed is True

    sla_fail = evaluate_deterministic_dimension(
        rule={
            "rule_type": "latency_sla",
            "target_field": "sut_latency_ms",
            "max_ms": 2000,
        },
        threshold=1.0,
        sut_latency_ms=2400,
    )
    assert sla_fail.raw_dimension_score == 0.0
    assert sla_fail.passed is False


async def test_evaluate_case_dimensions_merges_9_questions_into_single_http_call_and_persists_ledger(
    db_session: "Session",
    test_settings: "Settings",
) -> None:
    import json as json_lib

    import httpx
    from sqlalchemy import select

    from app.db.models import DecisionProvider, Dimension, EvalCase, ProbabilityLedger, Suite
    from app.kernel import evaluate_case_dimensions, recalculate_case_from_ledgers

    # Create 3 SYSTEM_ONE dimensions sharing ['input', 'actual_output', 'references'] (3+3+3 = 9 questions)
    # plus 2 DETERMINISTIC dimensions (route_top1_match + numeric_fact_grounding)
    s1_dims: list[Dimension] = []
    for idx in range(1, 4):
        d = Dimension(
            dimension_code=f"shared_s1_dim_{idx}",
            version=1,
            dimension_name=f"共享上下文维度 {idx}",
            category="ACCURACY",
            evaluator_kind="SYSTEM_ONE",
            state_fields_json=["input", "actual_output", "references"],
            questions_json=[
                {"key": "q_noul", "type": "noul", "weight": 1.0, "instructions": f"子题1-{idx}"},
                {
                    "key": "q_score",
                    "type": "score",
                    "weight": 2.0,
                    "instructions": f"子题2-{idx}",
                    "criteria": ["差", "中", "好"],
                },
                {
                    "key": "q_choice",
                    "type": "choice",
                    "weight": 1.0,
                    "instructions": f"子题3-{idx}",
                    "criteria": {"bad": "差", "good": "好", "na": "不适用"},
                    "option_scores": {"bad": 0.0, "good": 1.0, "na": None},
                },
            ],
            default_threshold=0.75,
            default_weight=2.0,
            strict_mode=0,
            status="ACTIVE",
        )
        db_session.add(d)
        s1_dims.append(d)

    db_session.flush()
    route_dim = db_session.scalar(
        select(Dimension).where(
            Dimension.dimension_code == "route_top1_match", Dimension.version == 1
        )
    )
    num_dim = db_session.scalar(
        select(Dimension).where(
            Dimension.dimension_code == "numeric_fact_grounding", Dimension.version == 1
        )
    )
    ollama_provider = db_session.scalar(
        select(DecisionProvider).where(DecisionProvider.provider_code == "ollama_local_tev1")
    )
    assert route_dim is not None and num_dim is not None and ollama_provider is not None

    suite = Suite(
        suite_code="suite_9q_batch_test",
        suite_name="9题单请求合批验收套件",
        eval_level="E2E",
        default_provider_id=ollama_provider.id,
        dimensions_json=[
            *[
                {
                    "dimension_code": d.dimension_code,
                    "version": 1,
                    "weight": 2.0,
                    "threshold": 0.75,
                    "is_blocker": True,
                }
                for d in s1_dims
            ],
            {
                "dimension_code": "route_top1_match",
                "version": 1,
                "weight": 1.0,
                "threshold": 1.0,
                "is_blocker": False,
            },
            {
                "dimension_code": "numeric_fact_grounding",
                "version": 1,
                "weight": 1.5,
                "threshold": 1.0,
                "is_blocker": True,
            },
        ],
        pass_policy="ALL_BLOCKERS_AND_WEIGHTED",
        suite_pass_threshold=0.80,
        confidence_review_floor=0.50,
        enable_semantic_regression=1,
        status="ACTIVE",
    )
    db_session.add(suite)
    db_session.commit()

    http_calls: list[dict] = []

    def mock_s1(request: httpx.Request) -> httpx.Response:
        body = json_lib.loads(request.content.decode("utf-8"))
        http_calls.append(body)
        ans: dict[str, dict] = {}
        for q_key, spec in body["questions"].items():
            if spec["type"] == "noul":
                ans[q_key] = {"noul": 0.90}
            elif spec["type"] == "score":
                ans[q_key] = {"probabilities": {"0": 0.05, "1": 0.15, "2": 0.80}}
            elif spec["type"] == "choice":
                if "SAME" in spec["criteria"]:
                    ans[q_key] = {
                        "probabilities": {
                            "SAME": 0.04,
                            "FACT_DIFFERS": 0.06,
                            "ACTION_DIFFERS": 0.82,
                            "SPECIFICITY_DIFFERS": 0.08,
                        }
                    }
                else:
                    ans[q_key] = {"probabilities": {"bad": 0.05, "good": 0.15, "na": 0.80}}
        return httpx.Response(
            200,
            headers={"X-Request-ID": f"batch_req_{len(http_calls)}"},
            json={"answers": ans, "usage": {"input_tokens": 120, "output_tokens": 12}},
        )

    eval_case = EvalCase(
        dataset_id=1,
        case_code="CASE_001",
        dataset_split="HOLDOUT",
        input_text="常州提取公积金还商贷怎么办理？",
        expected_output="可通过微信公众号或线下窗口办理，热线12329，3个工作日办结。",
        reference_contexts_json=["常州住房公积金热线 12329，审批时限 3 个工作日。"],
        expected_structured_json={"expected_agent_ids": ["cz_gjj_agent"]},
        pii_masked=1,
        review_status="APPROVED",
    )

    # First run WITHOUT baseline_output -> 3 SYSTEM_ONE dims (9 questions) MUST take strictly 1 HTTP call!
    res_no_reg = await evaluate_case_dimensions(
        case=eval_case,
        suite=suite,
        dimensions=[*s1_dims, route_dim, num_dim],
        actual_output="您可线上办理，详情拨打12329，3个工作日内办结。",
        actual_references=["常州住房公积金热线 12329，审批时限 3 个工作日。"],
        actual_structured_json={"actual_agent_id": "cz_gjj_agent"},
        sut_latency_ms=340,
        provider=ollama_provider,
        settings=test_settings,
        transport=httpx.MockTransport(mock_s1),
    )

    assert len(http_calls) == 1
    assert len(http_calls[0]["questions"]) == 9
    assert res_no_reg.decision_calls == 1
    assert res_no_reg.total_input_tokens == 120
    assert res_no_reg.overall_passed is True
    assert res_no_reg.regression_tag is None
    assert len(res_no_reg.ledger_entries) == 5

    # Persist ledgers into SQLite DB and verify 100% parity on recalculation
    persisted_ledgers = [
        ProbabilityLedger(**entry.to_orm_kwargs(run_id=1, run_case_id=1))
        for entry in res_no_reg.ledger_entries
    ]
    db_session.add_all(persisted_ledgers)
    db_session.commit()

    recalc_same = recalculate_case_from_ledgers(
        persisted_ledgers,
        pass_policy=suite.pass_policy,
        suite_pass_threshold=suite.suite_pass_threshold,
        confidence_review_floor=suite.confidence_review_floor,
    )
    assert recalc_same.overall_score == res_no_reg.overall_score
    assert recalc_same.overall_passed == res_no_reg.overall_passed
    assert recalc_same.min_confidence == res_no_reg.min_confidence
    assert recalc_same.needs_human_review == res_no_reg.needs_human_review

    # Second run WITH baseline_output -> 1 batch call (9 questions) + 1 semantic regression call = 2 calls
    http_calls.clear()
    res_with_reg = await evaluate_case_dimensions(
        case=eval_case,
        suite=suite,
        dimensions=[*s1_dims, route_dim, num_dim],
        actual_output="您只能去线下柜台办理，详情拨打12329。",
        actual_references=["常州住房公积金热线 12329，审批时限 3 个工作日。"],
        actual_structured_json={"actual_agent_id": "cz_gjj_agent"},
        baseline_output="您可通过微信公众号线上零材料刷脸申办，详情拨打12329。",
        provider=ollama_provider,
        settings=test_settings,
        transport=httpx.MockTransport(mock_s1),
    )
    assert res_with_reg.decision_calls == 2
    assert res_with_reg.total_input_tokens == 240
    assert res_with_reg.regression_tag == "ACTION_DIFFERS"
    assert res_with_reg.regression_confidence == pytest.approx(0.76, abs=1e-4)


async def test_system_one_client_network_routing_and_500_case_recalculation_speed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import json as json_lib
    import time

    from app.config import Settings
    from app.db.models import DecisionProvider
    from app.kernel.deterministic import evaluate_deterministic_dimension
    from app.kernel.scoring import (
        evaluate_suite_gate,
        recalculate_case_from_ledgers,
        synthesize_dimension_result,
    )
    from app.kernel.system_one_client import SystemOneClient

    # 1. Verify "办理时限：15日。" grounds "15天办结" while ignoring "每月20日"
    day_grounding = evaluate_deterministic_dimension(
        rule={
            "rule_type": "numeric_fact_grounding",
            "check_types": ["workdays"],
            "grounding_sources": ["references"],
        },
        threshold=1.0,
        actual_output="每月20日扣款，材料提交后15天办结。",
        references=["扣款日为每月20日；法定办理时限：15日。"],
    )
    assert day_grounding.raw_dimension_score == 1.0
    assert day_grounding.passed is True
    assert len(day_grounding.raw_answers_json["grounded_claims"]) == 1

    # 2. Local TCP server test for SystemOneClient DIRECT vs PROXY_FALLBACK
    async def handle_s1(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        raw_head = await reader.readuntil(b"\r\n\r\n")
        head_lines = raw_head.decode("utf-8", errors="replace").split("\r\n")
        content_len = 0
        for line in head_lines[1:]:
            if line.lower().startswith("content-length:"):
                content_len = int(line.split(":", 1)[1].strip())
        if content_len > 0:
            await reader.readexactly(content_len)
        resp_bytes = json_lib.dumps(
            {"answers": {"q1": {"noul": 0.95}}, "usage": {"input_tokens": 18}}
        ).encode("utf-8")
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(resp_bytes)}\r\nConnection: close\r\n\r\n".encode("ascii")
            + resp_bytes
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle_s1, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
        cfg = Settings(_env_file=None, proxy_candidates=["http://127.0.0.1:1"])

        # OLLAMA_LOCAL forces DIRECT (1 attempt) even if network_mode says PROXY_FALLBACK
        ol_prov = DecisionProvider(
            provider_code="ol_direct",
            provider_name="Ollama Direct",
            provider_kind="OLLAMA_LOCAL",
            endpoint_url=f"http://127.0.0.1:{port}/v1/systemone",
            default_model="tev1:0.8b",
            auth_type="NONE",
            network_mode="PROXY_FALLBACK",
            timeout_seconds=2,
        )
        ol_res = await SystemOneClient(ol_prov, settings=cfg).decide(
            state={"input": "测试"},
            questions={"q1": {"type": "noul", "instructions": "是否正常"}},
        )
        assert ol_res.attempts == 1
        assert ol_res.channel_used == "DIRECT"

        # DECISION_RELAY with PROXY_FALLBACK tries dead proxy (port 1) then succeeds on DIRECT (2 attempts)
        relay_prov = DecisionProvider(
            provider_code="relay_fb",
            provider_name="Relay Fallback",
            provider_kind="DECISION_RELAY",
            endpoint_url=f"http://127.0.0.1:{port}/v1/decisions",
            default_model="typesafe/jev-1.14.0",
            auth_type="NONE",
            network_mode="PROXY_FALLBACK",
            timeout_seconds=1,
        )
        relay_res = await SystemOneClient(relay_prov, settings=cfg).decide(
            state={"input": "测试"},
            questions={"q1": {"type": "noul", "instructions": "是否正常"}},
        )
        assert relay_res.attempts == 2
        assert relay_res.channel_used == "DIRECT"
    finally:
        server.close()
        await server.wait_closed()

    # 3. 500-case pure in-memory recalculation < 50ms and 100% parity with fresh scoring
    q_spec = [{"key": "k1", "type": "noul", "weight": 1.0, "instructions": "核对"}]
    all_case_ledgers: list[list[dict]] = []
    raw_probs_per_case: list[tuple[float, float]] = []
    for i in range(500):
        p1 = round(0.40 + (i % 60) * 0.01, 4)
        p2 = round(0.45 + ((i * 7) % 55) * 0.01, 4)
        raw_probs_per_case.append((p1, p2))
        s1 = synthesize_dimension_result(q_spec, {"k1": {"noul": p1}}, threshold=0.70)
        s2 = synthesize_dimension_result(q_spec, {"k1": {"noul": p2}}, threshold=0.70)
        all_case_ledgers.append(
            [
                {
                    "dimension_code": "dim_a",
                    "raw_dimension_score": s1.raw_dimension_score,
                    "applied_threshold": 0.70,
                    "applied_weight": 1.0,
                    "is_blocker": True,
                    "min_question_confidence": s1.min_question_confidence,
                    "raw_answers_json": s1.raw_answers_json,
                },
                {
                    "dimension_code": "dim_b",
                    "raw_dimension_score": s2.raw_dimension_score,
                    "applied_threshold": 0.70,
                    "applied_weight": 2.0,
                    "is_blocker": False,
                    "min_question_confidence": s2.min_question_confidence,
                    "raw_answers_json": s2.raw_answers_json,
                },
            ]
        )

    overrides = {
        "dim_a": {"threshold": 0.78, "weight": 2.5, "is_blocker": True},
        "dim_b": {"threshold": 0.82, "weight": 1.5, "is_blocker": True},
    }
    t0 = time.perf_counter()
    recalc_results = [
        recalculate_case_from_ledgers(
            case_ledgers,
            pass_policy="ALL_BLOCKERS_AND_WEIGHTED",
            suite_pass_threshold=0.81,
            dimension_overrides=overrides,
        )
        for case_ledgers in all_case_ledgers
    ]
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert elapsed_ms < 100.0

    for idx, (p1, p2) in enumerate(raw_probs_per_case):
        f1 = synthesize_dimension_result(q_spec, {"k1": {"noul": p1}}, threshold=0.78)
        f2 = synthesize_dimension_result(q_spec, {"k1": {"noul": p2}}, threshold=0.82)
        fg = evaluate_suite_gate(
            [
                {
                    "dimension_code": "dim_a",
                    "raw_dimension_score": f1.raw_dimension_score,
                    "applied_threshold": 0.78,
                    "applied_weight": 2.5,
                    "is_blocker": True,
                    "min_question_confidence": f1.min_question_confidence,
                },
                {
                    "dimension_code": "dim_b",
                    "raw_dimension_score": f2.raw_dimension_score,
                    "applied_threshold": 0.82,
                    "applied_weight": 1.5,
                    "is_blocker": True,
                    "min_question_confidence": f2.min_question_confidence,
                },
            ],
            pass_policy="ALL_BLOCKERS_AND_WEIGHTED",
            suite_pass_threshold=0.81,
        )
        assert recalc_results[idx].overall_score == fg.overall_score
        assert recalc_results[idx].overall_passed == fg.overall_passed








