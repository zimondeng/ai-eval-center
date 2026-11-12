from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from jsonpath_ng.ext import parse as parse_jsonpath

from app.kernel.scoring import DimensionScoreSynthesis

_JSONPATH_CACHE: dict[str, Any] = {}


def _compile_jsonpath(expr_str: str) -> Any:
    cleaned = expr_str.strip()
    cached = _JSONPATH_CACHE.get(cleaned)
    if cached is None:
        cached = parse_jsonpath(cleaned)
        _JSONPATH_CACHE[cleaned] = cached
    return cached


def _strict_value_equals(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)
    return a == b


def _eval_json_exact_or_in(
    rule: Mapping[str, Any],
    *,
    threshold: float,
    actual_structured_json: Mapping[str, Any] | None,
    expected_structured_json: Mapping[str, Any] | None,
) -> DimensionScoreSynthesis:
    actual_path = str(rule.get("actual_path") or "").strip()
    if not actual_path:
        raise ValueError("json_exact_or_in 规则缺少 actual_path")

    actual_doc = dict(actual_structured_json) if isinstance(actual_structured_json, Mapping) else {}
    actual_matches = [m.value for m in _compile_jsonpath(actual_path).find(actual_doc)]

    expected_candidates: list[Any] = []
    if isinstance(rule.get("expected_values"), list):
        expected_candidates = list(rule["expected_values"])
    elif "expected_value" in rule and rule["expected_value"] is not None:
        expected_candidates = [rule["expected_value"]]
    elif isinstance(rule.get("expected_path"), str) and rule["expected_path"].strip():
        exp_path = rule["expected_path"].strip()
        exp_doc = (
            dict(expected_structured_json)
            if isinstance(expected_structured_json, Mapping)
            else {}
        )
        raw_exp_matches = [m.value for m in _compile_jsonpath(exp_path).find(exp_doc)]
        for item in raw_exp_matches:
            if isinstance(item, list):
                expected_candidates.extend(item)
            elif item is not None:
                expected_candidates.append(item)

    if not actual_matches or actual_matches[0] is None:
        actual_val = None
        hit = False
        reason = f"未命中：实际输出路径 {actual_path} 为空或不存在"
    elif not expected_candidates:
        actual_val = actual_matches[0] if len(actual_matches) == 1 else actual_matches
        hit = False
        reason = "未命中：用例未提供有效结构化期望目标"
    else:
        actual_val = actual_matches[0] if len(actual_matches) == 1 else actual_matches
        hit = any(_strict_value_equals(actual_val, cand) for cand in expected_candidates)
        if hit:
            reason = f"命中期望值：{actual_path}={actual_val!r} 落在期望集合 {expected_candidates!r} 中"
        else:
            reason = f"未命中期望值：实际值 {actual_val!r} 不在期望集合 {expected_candidates!r} 中"

    score = 1.0 if hit else 0.0
    passed = score >= float(threshold)
    return DimensionScoreSynthesis(
        raw_dimension_score=score,
        passed=passed,
        min_question_confidence=None,
        deterministic_reason=reason,
        raw_answers_json={
            "rule_type": "json_exact_or_in",
            "actual_path": actual_path,
            "actual_value": actual_val,
            "expected_candidates": expected_candidates,
            "matched": hit,
        },
    )


def _extract_id_set(doc: Mapping[str, Any] | None, path_str: str) -> list[str]:
    if not isinstance(doc, Mapping):
        return []
    matches = [m.value for m in _compile_jsonpath(path_str).find(dict(doc))]
    ordered: list[str] = []
    seen: set[str] = set()
    for val in matches:
        items = val if isinstance(val, list) else [val]
        for elem in items:
            if elem is None:
                continue
            token = str(elem).strip()
            if token and token not in seen:
                seen.add(token)
                ordered.append(token)
    return ordered


def _eval_set_recall_precision(
    rule: Mapping[str, Any],
    *,
    threshold: float,
    actual_structured_json: Mapping[str, Any] | None,
    expected_structured_json: Mapping[str, Any] | None,
) -> DimensionScoreSynthesis:
    actual_path = str(rule.get("actual_path") or "$.selected_agent_ids").strip()
    expected_path = str(rule.get("expected_path") or "$.expected_agent_ids").strip()
    metric = str(rule.get("metric") or "f1").strip().lower()
    if metric not in ("recall", "precision", "f1"):
        raise ValueError(f"set_recall_precision 不支持的 metric: {metric!r}")

    actual_list = _extract_id_set(actual_structured_json, actual_path)
    expected_list = _extract_id_set(expected_structured_json, expected_path)
    actual_set = set(actual_list)
    expected_set = set(expected_list)
    inter = [x for x in expected_list if x in actual_set]
    missing = [x for x in expected_list if x not in actual_set]
    extra = [x for x in actual_list if x not in expected_set]

    if not expected_set and not actual_set:
        recall, precision, f1 = 1.0, 1.0, 1.0
    elif not expected_set and actual_set:
        recall, precision, f1 = 1.0, 0.0, 0.0
    elif expected_set and not actual_set:
        recall, precision, f1 = 0.0, 0.0, 0.0
    else:
        recall = len(inter) / float(len(expected_set))
        precision = len(inter) / float(len(actual_set))
        f1 = (
            (2.0 * precision * recall / (precision + recall))
            if (precision + recall) > 1e-12
            else 0.0
        )

    metric_map = {"recall": recall, "precision": precision, "f1": f1}
    score = round(metric_map[metric], 5)
    passed = score >= float(threshold)
    reason = (
        f"集合评估({metric.upper()}={score:.4f}, Recall={recall:.4f}, "
        f"Precision={precision:.4f}, F1={f1:.4f})；"
        f"命中={inter}，漏召回={missing}，多召回={extra}"
    )

    return DimensionScoreSynthesis(
        raw_dimension_score=score,
        passed=passed,
        min_question_confidence=None,
        deterministic_reason=reason,
        raw_answers_json={
            "rule_type": "set_recall_precision",
            "metric": metric,
            "recall": round(recall, 6),
            "precision": round(precision, 6),
            "f1": round(f1, 6),
            "actual_items": actual_list,
            "expected_items": expected_list,
            "matched_items": inter,
            "missing_items": missing,
            "extra_items": extra,
        },
    )


_PHONE_RE = re.compile(
    r"(?<![\d-])(?:"
    r"0\d{2,3}[-\s]?\d{7,8}"
    r"|[48]00[-\s]?\d{3}[-\s]?\d{4}"
    r"|1[3-9]\d{9}"
    r"|(?:12\d{3}|95\d{3,4}|96\d{3,4})"
    r")(?!\d)"
    r"(?!(?:\.\d+)?\s*(?:万元|亿元|千元|百元|元|角|分|块|%|％))"
)

_AMOUNT_PREFIX_RE = re.compile(
    r"[¥￥]\s*(\d[\d,]*(?:\.\d+)?)\s*(亿|万|千|百)?(?:\s*元)?"
)
_AMOUNT_SUFFIX_RE = re.compile(
    r"(?<!\d)(\d[\d,]*(?:\.\d+)?)\s*(亿元|万元|千元|百元|元|角|分|块)"
)

_PERCENT_RE = re.compile(
    r"(?:(?<!\d)(\d+(?:\.\d+)?)\s*[%％]|百分之\s*(\d+(?:\.\d+)?))"
)

_WORKDAYS_RE = re.compile(
    r"(?<!月)(?<!月\s)(?<!\d)(\d+(?:\.\d+)?)\s*(?:个)?\s*"
    r"(法定工作日|工作日|自然日|天|日(?!(?:元|常|本|语|志|期|历|报|均|间|夜)))"
)

_UNIT_MULTIPLIERS: dict[str, float] = {
    "亿元": 1e8,
    "亿": 1e8,
    "万元": 1e4,
    "万": 1e4,
    "千元": 1e3,
    "千": 1e3,
    "百元": 1e2,
    "百": 1e2,
    "元": 1.0,
    "块": 1.0,
    "": 1.0,
    "角": 0.1,
    "分": 0.01,
}


def _extract_numeric_claims(
    text: str,
    enabled_types: set[str],
) -> list[dict[str, Any]]:
    if not text:
        return []
    claims: list[dict[str, Any]] = []

    if "phone" in enabled_types:
        for m in _PHONE_RE.finditer(text):
            raw_txt = m.group(0)
            canon = re.sub(r"[-\s]", "", raw_txt)
            claims.append(
                {
                    "type": "phone",
                    "raw": raw_txt,
                    "canonical": canon,
                }
            )

    if "amount" in enabled_types:
        occupied_spans: list[tuple[int, int]] = []
        for m in _AMOUNT_PREFIX_RE.finditer(text):
            occupied_spans.append((m.start(), m.end()))
            num_val = float(m.group(1).replace(",", ""))
            mult = _UNIT_MULTIPLIERS.get(m.group(2) or "", 1.0)
            claims.append(
                {
                    "type": "amount",
                    "raw": m.group(0),
                    "canonical": round(num_val * mult, 4),
                }
            )
        for m in _AMOUNT_SUFFIX_RE.finditer(text):
            s, e = m.start(), m.end()
            if any(not (e <= os or s >= oe) for os, oe in occupied_spans):
                continue
            num_val = float(m.group(1).replace(",", ""))
            mult = _UNIT_MULTIPLIERS.get(m.group(2), 1.0)
            claims.append(
                {
                    "type": "amount",
                    "raw": m.group(0),
                    "canonical": round(num_val * mult, 4),
                }
            )

    if "percent" in enabled_types:
        for m in _PERCENT_RE.finditer(text):
            num_str = m.group(1) or m.group(2)
            claims.append(
                {
                    "type": "percent",
                    "raw": m.group(0),
                    "canonical": round(float(num_str), 6),
                }
            )

    if "workdays" in enabled_types:
        for m in _WORKDAYS_RE.finditer(text):
            num_val = round(float(m.group(1)), 4)
            unit_raw = m.group(2)
            day_kind = "workday" if "工作日" in unit_raw else "calendar_day"
            claims.append(
                {
                    "type": "workdays",
                    "raw": m.group(0),
                    "canonical": f"{day_kind}:{num_val:g}",
                }
            )

    return claims


def _is_claim_grounded(
    claim: Mapping[str, Any],
    source_claims: Sequence[Mapping[str, Any]],
    normalized_phone_corpus: str,
) -> bool:
    ctype = claim["type"]
    canon = claim["canonical"]
    for sc in source_claims:
        if sc["type"] != ctype:
            continue
        if isinstance(canon, float) and isinstance(sc["canonical"], float):
            if math.isclose(canon, sc["canonical"], rel_tol=1e-6, abs_tol=1e-4):
                return True
        elif canon == sc["canonical"]:
            return True

    if ctype == "phone" and isinstance(canon, str):
        if re.search(rf"(?<!\d){re.escape(canon)}(?!\d)", normalized_phone_corpus):
            return True
    return False


def _eval_numeric_fact_grounding(
    rule: Mapping[str, Any],
    *,
    threshold: float,
    actual_output: str | None,
    references: Sequence[str] | None,
    expected_output: str | None,
) -> DimensionScoreSynthesis:
    raw_types = rule.get("check_types") or ["phone", "amount", "percent", "workdays"]
    enabled_types: set[str] = set()
    for t in raw_types:
        t_clean = str(t).strip().lower()
        if t_clean == "percentage":
            t_clean = "percent"
        enabled_types.add(t_clean)

    sources_cfg = rule.get("grounding_sources") or ["references", "expected_output"]
    corpus_parts: list[str] = []
    if "references" in sources_cfg and references:
        corpus_parts.extend(str(r) for r in references if r)
    if "expected_output" in sources_cfg and expected_output:
        corpus_parts.append(str(expected_output))

    full_corpus = "\n".join(corpus_parts)
    normalized_phone_corpus = re.sub(r"(?<=\d)[-\s](?=\d)", "", full_corpus)

    actual_claims = _extract_numeric_claims(actual_output or "", enabled_types)
    if not actual_claims:
        return DimensionScoreSynthesis(
            raw_dimension_score=1.0,
            passed=1.0 >= float(threshold),
            min_question_confidence=None,
            deterministic_reason="回答中未包含需核对的电话/金额/百分比/时限数字项，无虚构数字风险",
            raw_answers_json={
                "rule_type": "numeric_fact_grounding",
                "checked_count": 0,
                "grounded_claims": [],
                "ungrounded_claims": [],
            },
        )

    source_claims = _extract_numeric_claims(full_corpus, enabled_types)
    grounded: list[dict[str, Any]] = []
    ungrounded: list[dict[str, Any]] = []

    for claim in actual_claims:
        if _is_claim_grounded(claim, source_claims, normalized_phone_corpus):
            grounded.append(claim)
        else:
            ungrounded.append(claim)

    score = round(len(grounded) / float(len(actual_claims)), 5)
    passed = score >= float(threshold)
    if not ungrounded:
        reason = f"数字事实核对全部通过（共核对 {len(actual_claims)} 项，均在参考依据中证实）"
    else:
        bad_desc = ", ".join(f"{c['type']}={c['raw']}" for c in ungrounded)
        reason = (
            f"发现未证实数字事实：{bad_desc}"
            f"（共核对 {len(actual_claims)} 项，证实 {len(grounded)} 项）"
        )

    return DimensionScoreSynthesis(
        raw_dimension_score=score,
        passed=passed,
        min_question_confidence=None,
        deterministic_reason=reason,
        raw_answers_json={
            "rule_type": "numeric_fact_grounding",
            "checked_count": len(actual_claims),
            "grounded_claims": grounded,
            "ungrounded_claims": ungrounded,
        },
    )


def _eval_latency_sla(
    rule: Mapping[str, Any],
    *,
    threshold: float,
    sut_latency_ms: int | float | None,
    ttft_ms: int | float | None,
) -> DimensionScoreSynthesis:
    checks: list[tuple[str, float, float | None]] = []

    if rule.get("max_sut_latency_ms") is not None:
        checks.append(
            (
                "sut_latency_ms",
                float(rule["max_sut_latency_ms"]),
                float(sut_latency_ms) if sut_latency_ms is not None else None,
            )
        )
    if rule.get("max_ttft_ms") is not None:
        checks.append(
            (
                "ttft_ms",
                float(rule["max_ttft_ms"]),
                float(ttft_ms) if ttft_ms is not None else None,
            )
        )

    if not checks:
        target_field = str(
            rule.get("target_field") or rule.get("metric") or rule.get("field") or "sut_latency_ms"
        ).strip()
        bound_val: float | None = None
        for bkey in ("max_ms", "max_latency_ms", "sla_ms", "limit_ms"):
            if rule.get(bkey) is not None:
                bound_val = float(rule[bkey])
                break
        if bound_val is None:
            raise ValueError("latency_sla 规则缺少正数耗时阈值上限")
        actual_val = ttft_ms if target_field == "ttft_ms" else sut_latency_ms
        checks.append(
            (
                target_field,
                bound_val,
                float(actual_val) if actual_val is not None else None,
            )
        )

    check_details: list[dict[str, Any]] = []
    reasons: list[str] = []
    passed_count = 0
    for field_name, bound, actual in checks:
        if actual is None:
            ok = False
            reasons.append(f"缺少实际耗时指标 {field_name}")
        elif actual <= bound:
            ok = True
            passed_count += 1
            reasons.append(f"{field_name}={actual:g}ms <= 上限 {bound:g}ms")
        else:
            ok = False
            reasons.append(f"{field_name}={actual:g}ms > 上限 {bound:g}ms")
        check_details.append(
            {
                "field": field_name,
                "actual_ms": actual,
                "max_ms": bound,
                "passed": ok,
            }
        )

    score = round(passed_count / float(len(checks)), 5)
    passed = score >= float(threshold)
    prefix = "耗时达标：" if passed else "耗时超限："
    return DimensionScoreSynthesis(
        raw_dimension_score=score,
        passed=passed,
        min_question_confidence=None,
        deterministic_reason=prefix + "；".join(reasons),
        raw_answers_json={
            "rule_type": "latency_sla",
            "checks": check_details,
        },
    )


def evaluate_deterministic_dimension(
    rule: Mapping[str, Any],
    *,
    threshold: float = 1.0,
    actual_output: str | None = None,
    expected_output: str | None = None,
    references: Sequence[str] | None = None,
    actual_structured_json: Mapping[str, Any] | None = None,
    expected_structured_json: Mapping[str, Any] | None = None,
    sut_latency_ms: int | float | None = None,
    ttft_ms: int | float | None = None,
) -> DimensionScoreSynthesis:
    """零模型调用确定性规则引擎：支持 json_exact_or_in / set_recall_precision / numeric_fact_grounding / latency_sla。"""
    if not isinstance(rule, Mapping):
        raise ValueError("确定性规则配置必须为字典")
    rule_type = str(rule.get("rule_type") or "").strip()

    if rule_type == "json_exact_or_in":
        return _eval_json_exact_or_in(
            rule,
            threshold=threshold,
            actual_structured_json=actual_structured_json,
            expected_structured_json=expected_structured_json,
        )
    if rule_type == "set_recall_precision":
        return _eval_set_recall_precision(
            rule,
            threshold=threshold,
            actual_structured_json=actual_structured_json,
            expected_structured_json=expected_structured_json,
        )
    if rule_type == "numeric_fact_grounding":
        return _eval_numeric_fact_grounding(
            rule,
            threshold=threshold,
            actual_output=actual_output,
            references=references,
            expected_output=expected_output,
        )
    if rule_type == "latency_sla":
        return _eval_latency_sla(
            rule,
            threshold=threshold,
            sut_latency_ms=sut_latency_ms,
            ttft_ms=ttft_ms,
        )
    raise ValueError(f"不支持的确定性规则类型: {rule_type!r}")
