from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field

LintSeverity = Literal["ERROR", "WARN"]
LintCode = Literal[
    "LINT_OPT_COUNT",
    "LINT_SCORE_ORDER",
    "LINT_STATE_FIELD_REF",
    "LINT_COMPOUND_QUESTION",
    "LINT_ARITHMETIC_CHECK",
]

STANDARD_STATE_FIELDS: tuple[str, ...] = (
    "input",
    "actual_output",
    "expected_output",
    "references",
    "conversation_turns",
    "expected_outcome",
)

_STATE_FIELD_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])("
    + "|".join(re.escape(f) for f in STANDARD_STATE_FIELDS)
    + r")(?![A-Za-z0-9_])"
)

_ZH_POSITIVE_FULL_MARK_PHRASES: tuple[str, ...] = (
    "完全准确",
    "准确无误",
    "准确完整",
    "无遗漏",
    "毫无遗漏",
    "没有遗漏",
    "全部忠实",
    "完全忠实",
    "完全一致",
    "清晰完整",
    "完全正确",
    "全部正确",
    "完全符合",
    "无任何错误",
    "没有任何错误",
    "满分",
)

_EN_POSITIVE_FULL_MARK_PHRASES: tuple[str, ...] = (
    "fully grounded",
    "completely accurate",
    "fully accurate",
    "completely correct",
    "completely faithful",
    "no omissions",
    "exact match",
    "full marks",
    "flawless",
    "perfect",
)

_ZH_NEGATIVE_ZERO_MARK_PHRASES: tuple[str, ...] = (
    "完全错误",
    "核心结论错误",
    "毫无依据",
    "直接矛盾",
    "直接相反",
    "严重遗漏",
    "完全不符",
    "完全偏离",
    "答非所问",
    "虚假编造",
    "纯属编造",
    "完全无关",
)

_EN_NEGATIVE_ZERO_MARK_PHRASES: tuple[str, ...] = (
    "completely wrong",
    "totally wrong",
    "entirely wrong",
    "hallucinated",
    "pure fabrication",
    "directly contradicts",
    "completely irrelevant",
    "major omissions",
    "no basis",
    "unfounded",
)

_ZH_NEG_BEFORE_POSITIVE = ("不", "未", "并非", "不够", "未能", "非")
_ZH_NEG_BEFORE_NEGATIVE = ("无", "没有", "未", "不", "零", "不存在", "不含", "避免", "绝无")
_EN_NEG_PREFIX_RE = re.compile(
    r"(?:(?<![A-Za-z])(?:not|no|never|without|zero|hardly|barely|free\s+of))\s+(?:[A-Za-z]+\s+){0,2}$",
    re.IGNORECASE,
)

_COMPOUND_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"是否.{1,60}是否"),
    re.compile(r"既(?:要|需|应当|包含|满足).{1,45}又(?:要|需|应当|包含|满足)"),
    re.compile(r"不仅.{1,45}(?:而且|还要|还需|并且)"),
    re.compile(r"一方面.{1,50}另一方面"),
    re.compile(r"(?:同时|并且|而且|以及)(?:还要|还需|判断|检查|核对|评估)"),
    re.compile(r"\bwhether\b.{1,80}\b(?:and|or)\s+whether\b", re.IGNORECASE),
    re.compile(r"\bnot\s+only\b.{1,80}\bbut\s+also\b", re.IGNORECASE),
    re.compile(r"\bas\s+well\s+as\s+whether\b", re.IGNORECASE),
)

_ARITHMETIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:电话号码|手机号码?|固话号码|座机号码?|热线号码|11\s*位|多位数字|逐位核对|数字逐位)"
    ),
    re.compile(
        r"(?:加减|相加|相减|求和|加总|算术|四则运算|精确计算|核算总和|差额计算|乘除|合计金额)"
    ),
    re.compile(
        r"(?:日期差|天数差|相隔天数|间隔天数|工作日数|几个工作日|多少个工作日|天数计算|推算.{0,8}日期|节假日顺延)"
    ),
    re.compile(
        r"\b(?:phone\s+numbers?|telephone\s+numbers?|multi-digit|digit-by-digit|"
        r"arithmetic|addition|subtraction|exact\s+sum|date\s+difference|"
        r"workday\s+count|business\s+days?\s+count|days\s+between)\b",
        re.IGNORECASE,
    ),
)


class LintIssue(BaseModel):
    severity: LintSeverity
    code: LintCode
    question_key: str
    message: str


class LintReport(BaseModel):
    passed: bool
    issues: list[LintIssue] = Field(default_factory=list)


def _is_valid_option_score_value(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, bool):
        return False
    if isinstance(val, (int, float)):
        fval = float(val)
        return math.isfinite(fval) and 0.0 <= fval <= 1.0
    return False


def _check_opt_count(
    q_key: str,
    q_type: str,
    question: Mapping[str, Any],
    *,
    min_options: int,
    max_options: int,
) -> list[LintIssue]:
    issues: list[LintIssue] = []
    criteria = question.get("criteria")

    if q_type == "score":
        if not isinstance(criteria, list) or not (min_options <= len(criteria) <= max_options):
            count = len(criteria) if isinstance(criteria, list) else 0
            issues.append(
                LintIssue(
                    severity="ERROR",
                    code="LINT_OPT_COUNT",
                    question_key=q_key,
                    message=(
                        f"Score 题档位数量({count})必须在 [{min_options}, {max_options}] 区间内"
                    ),
                )
            )
    elif q_type == "choice":
        if not isinstance(criteria, Mapping) or not (
            min_options <= len(criteria) <= max_options
        ):
            count = len(criteria) if isinstance(criteria, Mapping) else 0
            issues.append(
                LintIssue(
                    severity="ERROR",
                    code="LINT_OPT_COUNT",
                    question_key=q_key,
                    message=(
                        f"Choice 题选项数量({count})必须在 [{min_options}, {max_options}] 区间内"
                    ),
                )
            )
            return issues

        option_scores = question.get("option_scores")
        if not isinstance(option_scores, Mapping):
            issues.append(
                LintIssue(
                    severity="ERROR",
                    code="LINT_OPT_COUNT",
                    question_key=q_key,
                    message="Choice 题必须提供与 criteria 键集合一致的 option_scores 字典",
                )
            )
            return issues

        criteria_keys = set(criteria.keys())
        score_keys = set(option_scores.keys())
        if criteria_keys != score_keys:
            missing = sorted(str(k) for k in criteria_keys - score_keys)
            extra = sorted(str(k) for k in score_keys - criteria_keys)
            issues.append(
                LintIssue(
                    severity="ERROR",
                    code="LINT_OPT_COUNT",
                    question_key=q_key,
                    message=(
                        f"Choice 题 option_scores 键集合与 criteria 不一致"
                        f" (缺失={missing}, 多余={extra})"
                    ),
                )
            )

        bad_opts = [
            str(k)
            for k, v in option_scores.items()
            if not _is_valid_option_score_value(v)
        ]
        if bad_opts:
            issues.append(
                LintIssue(
                    severity="ERROR",
                    code="LINT_OPT_COUNT",
                    question_key=q_key,
                    message=(
                        f"Choice 题 option_scores 分值必须位于 [0.0, 1.0] 或为 null，"
                        f"非法选项: {bad_opts}"
                    ),
                )
            )
        elif option_scores and all(v is None for v in option_scores.values()):
            issues.append(
                LintIssue(
                    severity="ERROR",
                    code="LINT_OPT_COUNT",
                    question_key=q_key,
                    message="Choice 题 option_scores 至少需要一个非 null 的适用计分选项",
                )
            )

    return issues


def _find_unnegated_zh_phrase(
    text: str,
    phrases: Sequence[str],
    neg_prefixes: Sequence[str],
) -> str | None:
    for phrase in phrases:
        start = 0
        while True:
            idx = text.find(phrase, start)
            if idx == -1:
                break
            prefix_window = text[max(0, idx - 4) : idx]
            if not any(prefix_window.endswith(neg) or neg in prefix_window for neg in neg_prefixes):
                return phrase
            start = idx + len(phrase)
    return None


def _find_unnegated_en_phrase(text: str, phrases: Sequence[str]) -> str | None:
    for phrase in phrases:
        pattern = re.compile(rf"(?<![A-Za-z]){re.escape(phrase)}(?![A-Za-z])", re.IGNORECASE)
        for match in pattern.finditer(text):
            before = text[: match.start()]
            if not _EN_NEG_PREFIX_RE.search(before):
                return match.group(0)
    return None


def _check_score_order(
    q_key: str,
    q_type: str,
    question: Mapping[str, Any],
) -> list[LintIssue]:
    if q_type != "score":
        return []
    criteria = question.get("criteria")
    if not isinstance(criteria, list) or len(criteria) < 2:
        return []

    first_tier = str(criteria[0])
    last_tier = str(criteria[-1])

    pos_in_first = _find_unnegated_zh_phrase(
        first_tier, _ZH_POSITIVE_FULL_MARK_PHRASES, _ZH_NEG_BEFORE_POSITIVE
    ) or _find_unnegated_en_phrase(first_tier, _EN_POSITIVE_FULL_MARK_PHRASES)
    if pos_in_first is not None:
        return [
            LintIssue(
                severity="ERROR",
                code="LINT_SCORE_ORDER",
                question_key=q_key,
                message=(
                    f"Score 第 0 档检测到正向高分措辞('{pos_in_first}')，"
                    "必须按最差到最好(worst-first)升序排列"
                ),
            )
        ]

    neg_in_last = _find_unnegated_zh_phrase(
        last_tier, _ZH_NEGATIVE_ZERO_MARK_PHRASES, _ZH_NEG_BEFORE_NEGATIVE
    ) or _find_unnegated_en_phrase(last_tier, _EN_NEGATIVE_ZERO_MARK_PHRASES)
    if neg_in_last is not None:
        return [
            LintIssue(
                severity="ERROR",
                code="LINT_SCORE_ORDER",
                question_key=q_key,
                message=(
                    f"Score 末档检测到负向零分措辞('{neg_in_last}')，"
                    "必须按最差到最好(worst-first)升序排列"
                ),
            )
        ]

    return []


def _check_state_field_refs(
    q_key: str,
    question: Mapping[str, Any],
    declared_fields: set[str],
) -> list[LintIssue]:
    texts_to_scan: list[str] = []
    instructions = question.get("instructions")
    if isinstance(instructions, str):
        texts_to_scan.append(instructions)

    criteria = question.get("criteria")
    if isinstance(criteria, list):
        texts_to_scan.extend(str(item) for item in criteria)
    elif isinstance(criteria, Mapping):
        texts_to_scan.extend(str(val) for val in criteria.values())

    missing_ordered: list[str] = []
    for text in texts_to_scan:
        for match in _STATE_FIELD_TOKEN_RE.finditer(text):
            token = match.group(1)
            if token not in declared_fields and token not in missing_ordered:
                missing_ordered.append(token)

    if not missing_ordered:
        return []

    joined = ", ".join(missing_ordered)
    return [
        LintIssue(
            severity="ERROR",
            code="LINT_STATE_FIELD_REF",
            question_key=q_key,
            message=(
                f"题干或选项引用了未在 state_fields_json 中声明的标准上下文字段: {joined}"
            ),
        )
    ]


def _check_compound_question(
    q_key: str,
    question: Mapping[str, Any],
) -> list[LintIssue]:
    instructions = question.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        return []

    qmark_count = instructions.count("?") + instructions.count("？")
    has_compound_conjunction = any(p.search(instructions) for p in _COMPOUND_PATTERNS)
    if qmark_count >= 2 or has_compound_conjunction:
        return [
            LintIssue(
                severity="WARN",
                code="LINT_COMPOUND_QUESTION",
                question_key=q_key,
                message="题干包含复合多问句，建议拆分为多道独立 noul 原子题由权重合成",
            )
        ]
    return []


def _check_arithmetic(
    q_key: str,
    question: Mapping[str, Any],
) -> list[LintIssue]:
    instructions = question.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        return []

    if any(p.search(instructions) for p in _ARITHMETIC_PATTERNS):
        return [
            LintIssue(
                severity="WARN",
                code="LINT_ARITHMETIC_CHECK",
                question_key=q_key,
                message=(
                    "题干要求神经网络逐位核对电话号码、精确算术加减或日期/工作日数计算，"
                    "建议改配 DETERMINISTIC 的 numeric_fact_grounding 规则"
                ),
            )
        ]
    return []


def lint_dimension(
    state_fields: Sequence[str] | None = None,
    questions: Sequence[Mapping[str, Any]] | None = None,
    *,
    state_fields_json: Sequence[str] | None = None,
    questions_json: Sequence[Mapping[str, Any]] | None = None,
    min_options: int = 2,
    max_options: int = 24,
) -> LintReport:
    resolved_state_fields = (
        state_fields if state_fields is not None else (state_fields_json or ())
    )
    declared_fields = {str(f).strip() for f in resolved_state_fields if str(f).strip()}
    resolved_questions = (
        questions if questions is not None else (questions_json or ())
    )

    issues: list[LintIssue] = []
    for idx, q in enumerate(resolved_questions):
        if not isinstance(q, Mapping):
            continue
        q_key = str(q.get("key") or f"question_{idx + 1}")
        q_type = str(q.get("type") or "").strip().lower()
        issues.extend(
            _check_opt_count(
                q_key,
                q_type,
                q,
                min_options=min_options,
                max_options=max_options,
            )
        )
        issues.extend(_check_score_order(q_key, q_type, q))
        issues.extend(_check_state_field_refs(q_key, q, declared_fields))
        issues.extend(_check_compound_question(q_key, q))
        issues.extend(_check_arithmetic(q_key, q))

    passed = not any(issue.severity == "ERROR" for issue in issues)
    return LintReport(passed=passed, issues=issues)
