from __future__ import annotations

from app.db.seed import DEFAULT_DIMENSIONS
from app.linter import lint_dimension


def test_seeded_system_one_dimensions_pass_linter_with_zero_issues() -> None:
    system_one_seeds = [
        d for d in DEFAULT_DIMENSIONS if d["evaluator_kind"] == "SYSTEM_ONE"
    ]
    assert len(system_one_seeds) >= 2

    for dim in system_one_seeds:
        report = lint_dimension(
            state_fields=dim["state_fields_json"],
            questions=dim["questions_json"],
        )
        assert report.passed is True, f"{dim['dimension_code']} failed lint: {report.issues}"
        assert report.issues == []


def test_lint_opt_count_rejects_score_and_choice_outside_2_to_24() -> None:
    too_few_score = lint_dimension(
        state_fields=["actual_output"],
        questions=[
            {
                "key": "single_tier_score",
                "type": "score",
                "instructions": "actual_output 的表述质量如何。",
                "criteria": ["仅有一个档位"],
            }
        ],
    )
    assert too_few_score.passed is False
    assert len(too_few_score.issues) == 1
    assert too_few_score.issues[0].severity == "ERROR"
    assert too_few_score.issues[0].code == "LINT_OPT_COUNT"
    assert too_few_score.issues[0].question_key == "single_tier_score"

    too_many_choice_criteria = {f"opt_{i}": f"选项说明 {i}" for i in range(25)}
    too_many_choice_scores = {f"opt_{i}": round(i / 24, 4) for i in range(25)}
    too_many_choice = lint_dimension(
        state_fields=["actual_output"],
        questions=[
            {
                "key": "twenty_five_choices",
                "type": "choice",
                "instructions": "actual_output 属于哪一类回复。",
                "criteria": too_many_choice_criteria,
                "option_scores": too_many_choice_scores,
            }
        ],
    )
    assert too_many_choice.passed is False
    assert any(
        issue.code == "LINT_OPT_COUNT"
        and issue.severity == "ERROR"
        and issue.question_key == "twenty_five_choices"
        for issue in too_many_choice.issues
    )


def test_lint_opt_count_validates_choice_option_scores_keys_and_value_range() -> None:
    mismatched_keys = lint_dimension(
        state_fields=["actual_output"],
        questions=[
            {
                "key": "choice_key_mismatch",
                "type": "choice",
                "instructions": "actual_output 是否提供了办理路径。",
                "criteria": {
                    "bad": "未提供路径",
                    "good": "提供了清晰路径",
                    "na": "不涉及办理路径",
                },
                "option_scores": {
                    "bad": 0.0,
                    "good": 1.0,
                },
            }
        ],
    )
    assert mismatched_keys.passed is False
    assert [
        (i.severity, i.code, i.question_key) for i in mismatched_keys.issues
    ] == [("ERROR", "LINT_OPT_COUNT", "choice_key_mismatch")]

    out_of_range_score = lint_dimension(
        state_fields=["actual_output"],
        questions=[
            {
                "key": "choice_bad_score_val",
                "type": "choice",
                "instructions": "actual_output 是否提供了办理路径。",
                "criteria": {
                    "bad": "未提供路径",
                    "good": "提供了清晰路径",
                    "na": "不涉及办理路径",
                },
                "option_scores": {
                    "bad": -0.1,
                    "good": 1.2,
                    "na": None,
                },
            }
        ],
    )
    assert out_of_range_score.passed is False
    assert any(
        i.code == "LINT_OPT_COUNT"
        and i.severity == "ERROR"
        and i.question_key == "choice_bad_score_val"
        for i in out_of_range_score.issues
    )

    all_null_scores = lint_dimension(
        state_fields=["actual_output"],
        questions=[
            {
                "key": "choice_all_null",
                "type": "choice",
                "instructions": "actual_output 是否提供了路径。",
                "criteria": {"a": "选项A", "b": "选项B"},
                "option_scores": {"a": None, "b": None},
            }
        ],
    )
    assert all_null_scores.passed is False
    assert [
        (i.severity, i.code, i.question_key) for i in all_null_scores.issues
    ] == [("ERROR", "LINT_OPT_COUNT", "choice_all_null")]


def test_lint_score_order_rejects_inverted_criteria_in_zh_and_en() -> None:
    inverted_zh = lint_dimension(
        state_fields=["expected_output", "actual_output"],
        questions=[
            {
                "key": "core_conclusion",
                "type": "score",
                "instructions": "对比 expected_output，actual_output 是否准确？",
                "criteria": [
                    "核心结论完全准确且无遗漏",
                    "部分准确，存在次要遗漏",
                    "核心结论完全错误或直接矛盾",
                ],
            }
        ],
    )
    assert inverted_zh.passed is False
    assert [
        (i.severity, i.code, i.question_key) for i in inverted_zh.issues
    ] == [("ERROR", "LINT_SCORE_ORDER", "core_conclusion")]

    inverted_en_last_only = lint_dimension(
        state_fields=["references", "actual_output"],
        questions=[
            {
                "key": "en_grounding",
                "type": "score",
                "instructions": "How faithful is actual_output to references?",
                "criteria": [
                    "Contains minor unsupported details",
                    "Mostly supported by references",
                    "Completely wrong and hallucinated",
                ],
            }
        ],
    )
    assert inverted_en_last_only.passed is False
    assert [
        (i.severity, i.code, i.question_key) for i in inverted_en_last_only.issues
    ] == [("ERROR", "LINT_SCORE_ORDER", "en_grounding")]

    inverted_en_first_only = lint_dimension(
        state_fields=["references", "actual_output"],
        questions=[
            {
                "key": "en_first_positive",
                "type": "score",
                "instructions": "How faithful is actual_output to references?",
                "criteria": [
                    "Fully grounded and completely accurate",
                    "Partially supported",
                    "Lacks supporting evidence",
                ],
            }
        ],
    )
    assert inverted_en_first_only.passed is False
    assert [
        (i.severity, i.code, i.question_key) for i in inverted_en_first_only.issues
    ] == [("ERROR", "LINT_SCORE_ORDER", "en_first_positive")]


def test_lint_score_order_allows_negated_phrases_in_valid_worst_first_rubric() -> None:
    valid_with_negations = lint_dimension(
        state_fields=["expected_output", "actual_output"],
        questions=[
            {
                "key": "negated_valid_order",
                "type": "score",
                "instructions": "对比 expected_output，actual_output 是否准确？",
                "criteria": [
                    "结论不完全准确，存在多处偏差",
                    "基本符合 expected_output，略有疏漏",
                    "结论与 expected_output 一致且无严重遗漏",
                ],
            }
        ],
    )
    assert valid_with_negations.passed is True
    assert valid_with_negations.issues == []


def test_lint_state_field_ref_detects_undeclared_standard_fields_in_instructions_and_criteria() -> None:
    missing_in_instructions = lint_dimension(
        state_fields=["input", "actual_output"],
        questions=[
            {
                "key": "compare_gt",
                "type": "noul",
                "instructions": "对比expected_output判断actual_output中未出现相悖事实。",
            }
        ],
    )
    assert missing_in_instructions.passed is False
    assert [
        (i.severity, i.code, i.question_key) for i in missing_in_instructions.issues
    ] == [("ERROR", "LINT_STATE_FIELD_REF", "compare_gt")]
    assert "expected_output" in missing_in_instructions.issues[0].message

    missing_in_choice_criteria = lint_dimension(
        state_fields=["input", "actual_output"],
        questions=[
            {
                "key": "multi_turn_goal",
                "type": "choice",
                "instructions": "评估当前回复是否推进了对话目标。",
                "criteria": {
                    "missed": "偏离了 conversation_turns 历史脉络且未达成 expected_outcome",
                    "met": "符合 references 依据并达成目标",
                },
                "option_scores": {"missed": 0.0, "met": 1.0},
            }
        ],
    )
    assert missing_in_choice_criteria.passed is False
    ref_issues = [
        i for i in missing_in_choice_criteria.issues if i.code == "LINT_STATE_FIELD_REF"
    ]
    assert len(ref_issues) >= 1
    combined_msg = " ".join(i.message for i in ref_issues)
    for field_name in ("conversation_turns", "expected_outcome", "references"):
        assert field_name in combined_msg

    no_false_positive = lint_dimension(
        state_fields=["actual_output"],
        questions=[
            {
                "key": "english_prose",
                "type": "noul",
                "instructions": "The actual_output respects user preferences and subinput formatting.",
            }
        ],
    )
    assert no_false_positive.passed is True
    assert no_false_positive.issues == []


def test_lint_compound_question_warns_on_multi_question_marks_and_conjunctions() -> None:
    multi_qmark = lint_dimension(
        state_fields=["actual_output", "references"],
        questions=[
            {
                "key": "double_ask",
                "type": "noul",
                "instructions": "actual_output 是否忠实于 references？语气是否自然礼貌？",
            }
        ],
    )
    assert multi_qmark.passed is True
    assert [
        (i.severity, i.code, i.question_key) for i in multi_qmark.issues
    ] == [("WARN", "LINT_COMPOUND_QUESTION", "double_ask")]

    conjunction_zh = lint_dimension(
        state_fields=["actual_output", "references"],
        questions=[
            {
                "key": "conjunction_ask",
                "type": "noul",
                "instructions": "判断 actual_output 是否忠实于 references，并且是否提供了线上申办链接？",
            },
            {
                "key": "both_and_ask",
                "type": "noul",
                "instructions": "既要判断 actual_output 事实准确，又要评估排版美观程度。",
            },
            {
                "key": "en_compound",
                "type": "noul",
                "instructions": "Verify whether actual_output matches references and whether the tone is empathetic.",
            },
        ],
    )
    assert conjunction_zh.passed is True
    warn_keys = [
        i.question_key
        for i in conjunction_zh.issues
        if i.code == "LINT_COMPOUND_QUESTION" and i.severity == "WARN"
    ]
    assert warn_keys == ["conjunction_ask", "both_and_ask", "en_compound"]


def test_lint_arithmetic_check_warns_and_suggests_numeric_fact_grounding() -> None:
    report = lint_dimension(
        state_fields=["actual_output", "references", "expected_output"],
        questions=[
            {
                "key": "phone_digits",
                "type": "noul",
                "instructions": "请逐位核对 actual_output 中的电话号码是否与 references 完全相同。",
            },
            {
                "key": "sum_calc",
                "type": "noul",
                "instructions": "验算 actual_output 中各项补贴金额加减求和是否与 expected_output 一致。",
            },
            {
                "key": "workday_diff",
                "type": "noul",
                "instructions": "检查 actual_output 推算的两个办理节点之间的工作日数与日期差是否准确。",
            },
        ],
    )
    assert report.passed is True
    arith_issues = [
        i for i in report.issues if i.code == "LINT_ARITHMETIC_CHECK"
    ]
    assert [i.question_key for i in arith_issues] == [
        "phone_digits",
        "sum_calc",
        "workday_diff",
    ]
    for issue in arith_issues:
        assert issue.severity == "WARN"
        assert "numeric_fact_grounding" in issue.message


def test_02_seed_defaults_sql_dimensions_match_and_pass_with_zero_errors() -> None:
    from pathlib import Path

    sql_path = (
        Path(__file__).resolve().parents[3] / "docs" / "sql" / "02_seed_defaults.sql"
    )
    sql_text = sql_path.read_text(encoding="utf-8")

    for dim in DEFAULT_DIMENSIONS:
        assert f"'{dim['dimension_code']}'" in sql_text
        if dim["evaluator_kind"] == "SYSTEM_ONE":
            for q in dim["questions_json"]:
                assert q["instructions"] in sql_text
            report = lint_dimension(
                state_fields_json=dim["state_fields_json"],
                questions_json=dim["questions_json"],
            )
            assert report.passed is True
            assert [i for i in report.issues if i.severity == "ERROR"] == []
            assert report.issues == []



