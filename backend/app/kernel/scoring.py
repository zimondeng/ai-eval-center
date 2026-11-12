from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.providers.client import compute_normalized_confidence, parse_noul_answer


def _is_real_num(val: Any) -> bool:
    return (
        isinstance(val, (int, float))
        and not isinstance(val, bool)
        and math.isfinite(float(val))
    )


def compute_noul_score(raw_or_p_true: float | Mapping[str, Any]) -> float:
    """计算 noul 是非题归一化得分 s = p_true in [0, 1]。"""
    if isinstance(raw_or_p_true, Mapping):
        p_true, _ = parse_noul_answer(raw_or_p_true)
        return p_true
    if not _is_real_num(raw_or_p_true):
        raise ValueError(f"非法的 noul 概率值: {raw_or_p_true!r}")
    p_val = float(raw_or_p_true)
    if not (-1e-6 <= p_val <= 1.0 + 1e-6):
        raise ValueError(f"noul 概率超出 [0, 1] 范围: {raw_or_p_true!r}")
    return round(max(0.0, min(1.0, p_val)), 6)


def compute_score_question_score(
    probabilities: Mapping[str | int, float] | Sequence[float],
    num_tiers: int | None = None,
) -> tuple[float, float]:
    """计算 worst-first 有序量表题归一化期望分与原始加权档位 (normalized_score, raw_weighted_level)。"""
    if isinstance(probabilities, Mapping):
        k = num_tiers if num_tiers is not None else len(probabilities)
        if k < 2:
            raise ValueError(f"Score 题档位数必须 >= 2，实际为 {k}")
        tier_probs = [
            float(probabilities.get(str(i), probabilities.get(i, 0.0)))
            for i in range(k)
        ]
    elif isinstance(probabilities, Sequence) and not isinstance(probabilities, (str, bytes)):
        tier_probs = [float(p) for p in probabilities]
        k = num_tiers if num_tiers is not None else len(tier_probs)
        if k < 2 or len(tier_probs) != k:
            raise ValueError(f"Score 题档位数必须 >= 2 且长度匹配，实际为 len={len(tier_probs)}, k={k}")
    else:
        raise ValueError(f"非法的 Score 概率分布: {probabilities!r}")

    total_p = sum(tier_probs)
    if total_p <= 1e-9:
        raise ValueError("Score 题概率总和不能为 0")
    if abs(total_p - 1.0) > 1e-6:
        tier_probs = [p / total_p for p in tier_probs]

    raw_weighted_level = sum(i * p for i, p in enumerate(tier_probs))
    normalized_score = raw_weighted_level / float(k - 1)
    return (
        round(max(0.0, min(1.0, normalized_score)), 6),
        round(max(0.0, min(float(k - 1), raw_weighted_level)), 6),
    )


def compute_choice_score(
    probabilities: Mapping[str, float],
    option_scores: Mapping[str, float | None],
) -> tuple[float | None, bool]:
    """计算 Choice 题期望得分；若不适用选项(score=None)概率之和 >= 0.5，返回 (None, False) 剔除出分母。"""
    na_prob = sum(float(p) for opt, p in probabilities.items() if option_scores.get(opt) is None)
    if na_prob >= 0.5:
        return None, False
    applicable_prob = sum(
        float(p) for opt, p in probabilities.items() if option_scores.get(opt) is not None
    )
    if applicable_prob <= 1e-9:
        return None, False
    weighted = sum(
        (float(probabilities.get(opt, 0.0)) / applicable_prob) * float(score)
        for opt, score in option_scores.items()
        if score is not None
    )
    return round(max(0.0, min(1.0, weighted)), 6), True


def parse_score_answer(
    raw_answer: Any,
    criteria: Sequence[str],
) -> tuple[dict[str, float], float]:
    """解析三端 System One 的 score 题响应，返回 (probabilities_by_index_str, confidence)。"""
    k = len(criteria)
    if k < 2:
        raise ValueError(f"Score 题 criteria 档位数必须 >= 2，实际为 {k}")

    upstream_conf: float | None = None
    dist_source: Any = raw_answer

    if isinstance(raw_answer, Mapping):
        if _is_real_num(raw_answer.get("confidence")):
            upstream_conf = float(raw_answer["confidence"])
        for key in ("probabilities", "distribution", "probs", "scores"):
            val = raw_answer.get(key)
            if isinstance(val, (Mapping, Sequence)) and not isinstance(val, (str, bytes)):
                dist_source = val
                break
        else:
            score_val = raw_answer.get("score")
            if isinstance(score_val, (Mapping, Sequence)) and not isinstance(
                score_val, (str, bytes)
            ):
                dist_source = score_val

    tier_probs: list[float] | None = None
    if isinstance(dist_source, Sequence) and not isinstance(dist_source, (str, bytes)):
        if len(dist_source) != k or not all(_is_real_num(v) for v in dist_source):
            raise ValueError(f"Score 题概率数组长度或数值非法: {raw_answer!r}")
        tier_probs = [max(0.0, float(v)) for v in dist_source]
    elif isinstance(dist_source, Mapping):
        if any(
            (str(i) in dist_source and _is_real_num(dist_source[str(i)]))
            or (i in dist_source and _is_real_num(dist_source[i]))
            for i in range(k)
        ):
            tier_probs = [
                max(0.0, float(dist_source.get(str(i), dist_source.get(i, 0.0))))
                for i in range(k)
            ]
        elif "0" not in dist_source and all(
            str(i) in dist_source and _is_real_num(dist_source[str(i)])
            for i in range(1, k + 1)
        ):
            tier_probs = [max(0.0, float(dist_source[str(i)])) for i in range(1, k + 1)]
        elif any(str(c) in dist_source and _is_real_num(dist_source[str(c)]) for c in criteria):
            tier_probs = [max(0.0, float(dist_source.get(str(c), 0.0))) for c in criteria]

    if tier_probs is None:
        raise ValueError(f"无法解析 Score 题概率响应: {raw_answer!r}")

    total_p = sum(tier_probs)
    if total_p <= 1e-9:
        raise ValueError(f"Score 题概率总和为 0: {raw_answer!r}")
    if abs(total_p - 1.0) > 1e-6:
        tier_probs = [p / total_p for p in tier_probs]

    probs_map = {str(i): round(p, 6) for i, p in enumerate(tier_probs)}
    conf = compute_normalized_confidence(tier_probs, upstream_confidence=upstream_conf)
    return probs_map, round(conf, 4)


def parse_choice_answer(
    raw_answer: Any,
    criteria: Mapping[str, Any],
) -> tuple[dict[str, float], str, float]:
    """解析三端 System One 的 choice 题响应，返回 (probabilities, chosen_option, confidence)。"""
    option_keys = [str(k) for k in criteria.keys()]
    if len(option_keys) < 2:
        raise ValueError(f"Choice 题选项数必须 >= 2，实际为 {len(option_keys)}")

    upstream_conf: float | None = None
    explicit_chosen: str | None = None
    dist_source: Any = raw_answer

    if isinstance(raw_answer, str) and raw_answer in option_keys:
        probs_map = {opt: (1.0 if opt == raw_answer else 0.0) for opt in option_keys}
        return probs_map, raw_answer, 1.0

    if isinstance(raw_answer, Mapping):
        if _is_real_num(raw_answer.get("confidence")):
            upstream_conf = float(raw_answer["confidence"])
        for chosen_field in ("chosen", "selected", "answer"):
            cval = raw_answer.get(chosen_field)
            if isinstance(cval, str) and cval in option_keys:
                explicit_chosen = cval
                break
        if explicit_chosen is None and isinstance(raw_answer.get("choice"), str):
            if raw_answer["choice"] in option_keys:
                explicit_chosen = raw_answer["choice"]

        for key in ("probabilities", "distribution", "probs", "scores"):
            val = raw_answer.get(key)
            if isinstance(val, (Mapping, Sequence)) and not isinstance(val, (str, bytes)):
                dist_source = val
                break
        else:
            choice_val = raw_answer.get("choice")
            if isinstance(choice_val, (Mapping, Sequence)) and not isinstance(
                choice_val, (str, bytes)
            ):
                dist_source = choice_val

    raw_probs: list[float] | None = None
    if isinstance(dist_source, Sequence) and not isinstance(dist_source, (str, bytes)):
        if len(dist_source) == len(option_keys) and all(_is_real_num(v) for v in dist_source):
            raw_probs = [max(0.0, float(v)) for v in dist_source]
    elif isinstance(dist_source, Mapping):
        if any(opt in dist_source and _is_real_num(dist_source[opt]) for opt in option_keys):
            raw_probs = [
                max(0.0, float(dist_source[opt])) if _is_real_num(dist_source.get(opt)) else 0.0
                for opt in option_keys
            ]

    if raw_probs is None:
        if explicit_chosen is not None:
            raw_probs = [1.0 if opt == explicit_chosen else 0.0 for opt in option_keys]
        else:
            raise ValueError(f"无法解析 Choice 题概率响应: {raw_answer!r}")

    total_p = sum(raw_probs)
    if total_p <= 1e-9:
        raise ValueError(f"Choice 题概率总和为 0: {raw_answer!r}")
    if abs(total_p - 1.0) > 1e-6:
        raw_probs = [p / total_p for p in raw_probs]

    probs_map = {opt: round(p, 6) for opt, p in zip(option_keys, raw_probs, strict=True)}
    if explicit_chosen is not None:
        chosen = explicit_chosen
    else:
        best_idx = max(range(len(option_keys)), key=lambda i: raw_probs[i])
        chosen = option_keys[best_idx]

    conf = compute_normalized_confidence(raw_probs, upstream_confidence=upstream_conf)
    return probs_map, chosen, round(conf, 4)


@dataclass(slots=True)
class DimensionScoreSynthesis:
    raw_dimension_score: float | None
    passed: bool
    min_question_confidence: float | None
    deterministic_reason: str
    raw_answers_json: dict[str, Any]


def compute_dimension_score_from_questions(
    question_entries: Sequence[Mapping[str, Any]],
    *,
    strict_mode: bool | int = False,
) -> tuple[float | None, str | None]:
    """根据原子题账本数组合成连续维度得分；返回 (raw_dimension_score, strict_zero_trigger_key)。"""
    applicable_items = [
        q
        for q in question_entries
        if bool(q.get("applicable", True)) and q.get("normalized_score") is not None
    ]
    if not applicable_items:
        return None, None

    if bool(strict_mode):
        for q in applicable_items:
            if float(q["normalized_score"]) < 1.0 - 1e-6:
                return 0.0, str(q.get("key") or "unknown")

    total_weight = sum(float(q.get("weight", 1.0)) for q in applicable_items)
    if total_weight <= 1e-9:
        return None, None

    weighted_sum = sum(
        float(q.get("weight", 1.0)) * float(q["normalized_score"])
        for q in applicable_items
    )
    return round(max(0.0, min(1.0, weighted_sum / total_weight)), 5), None


def synthesize_dimension_result(
    questions_spec: Sequence[Mapping[str, Any]],
    answers: Mapping[str, Any],
    *,
    threshold: float = 0.75,
    strict_mode: bool | int = False,
) -> DimensionScoreSynthesis:
    """将单维度各原子题概率分布合成为原始概率账本记录与及格判定。"""
    question_entries: list[dict[str, Any]] = []

    for q_spec in questions_spec:
        q_key = str(q_spec["key"])
        q_type = str(q_spec["type"]).strip().lower()
        q_weight = float(q_spec.get("weight", 1.0))
        if q_key not in answers:
            raise ValueError(f"缺少原子题响应: {q_key}")
        raw_ans = answers[q_key]

        if q_type == "noul":
            p_true, conf_raw = parse_noul_answer(raw_ans)
            norm_score = compute_noul_score(p_true)
            question_entries.append(
                {
                    "key": q_key,
                    "type": "noul",
                    "weight": q_weight,
                    "applicable": True,
                    "normalized_score": norm_score,
                    "p_true": p_true,
                    "confidence": round(conf_raw, 4),
                }
            )
        elif q_type == "score":
            criteria = q_spec.get("criteria")
            if not isinstance(criteria, Sequence) or isinstance(criteria, (str, bytes)):
                raise ValueError(f"Score 题 {q_key} 缺少 criteria 数组")
            probs_map, conf = parse_score_answer(raw_ans, criteria)
            norm_score, raw_level = compute_score_question_score(
                probs_map, num_tiers=len(criteria)
            )
            question_entries.append(
                {
                    "key": q_key,
                    "type": "score",
                    "weight": q_weight,
                    "applicable": True,
                    "normalized_score": norm_score,
                    "raw_weighted_level": raw_level,
                    "probabilities": probs_map,
                    "confidence": conf,
                }
            )
        elif q_type == "choice":
            criteria = q_spec.get("criteria")
            option_scores = q_spec.get("option_scores")
            if not isinstance(criteria, Mapping) or not isinstance(option_scores, Mapping):
                raise ValueError(f"Choice 题 {q_key} 缺少 criteria 或 option_scores 字典")
            probs_map, chosen, conf = parse_choice_answer(raw_ans, criteria)
            norm_score, applicable = compute_choice_score(probs_map, option_scores)
            question_entries.append(
                {
                    "key": q_key,
                    "type": "choice",
                    "weight": q_weight,
                    "applicable": applicable,
                    "normalized_score": norm_score,
                    "chosen": chosen,
                    "probabilities": probs_map,
                    "confidence": conf,
                }
            )
        else:
            raise ValueError(f"不支持的原子题类型: {q_type!r}")

    raw_dim_score, strict_trigger_key = compute_dimension_score_from_questions(
        question_entries, strict_mode=strict_mode
    )

    applicable_confs = [
        float(q["confidence"])
        for q in question_entries
        if q.get("applicable") and q.get("confidence") is not None
    ]
    all_confs = [
        float(q["confidence"])
        for q in question_entries
        if q.get("confidence") is not None
    ]
    if applicable_confs:
        min_q_conf: float | None = round(min(applicable_confs), 4)
    elif all_confs:
        min_q_conf = round(min(all_confs), 4)
    else:
        min_q_conf = None

    excluded_keys = [str(q["key"]) for q in question_entries if not q.get("applicable")]
    excluded_note = (
        f"（已剔除不适用项：{', '.join(excluded_keys)}）" if excluded_keys else ""
    )

    target_t = float(threshold)
    if raw_dim_score is None:
        passed = True
        reason = f"所有子题均判定为不适用，本条用例不计入该维度扣分{excluded_note}"
    elif strict_trigger_key is not None:
        passed = raw_dim_score >= target_t
        trigger_q = next(q for q in question_entries if q["key"] == strict_trigger_key)
        reason = (
            f"严格模式触发：子题 {strict_trigger_key}"
            f" 得分 {float(trigger_q['normalized_score']):.4f} 未达满分，整维度置 0{excluded_note}"
        )
    else:
        passed = raw_dim_score >= target_t
        weak_items = [
            f"{q['key']}({float(q['normalized_score']):.4f})"
            for q in question_entries
            if q.get("applicable")
            and q.get("normalized_score") is not None
            and float(q["normalized_score"]) < target_t
        ]
        if passed:
            reason = f"维度达标（得分 {raw_dim_score:.4f} >= 阈值 {target_t:.4f}）{excluded_note}"
        elif weak_items:
            reason = (
                f"维度未达标（得分 {raw_dim_score:.4f} < 阈值 {target_t:.4f}）；"
                f"低分项：{', '.join(weak_items)}{excluded_note}"
            )
        else:
            reason = f"维度未达标（得分 {raw_dim_score:.4f} < 阈值 {target_t:.4f}）{excluded_note}"

    return DimensionScoreSynthesis(
        raw_dimension_score=raw_dim_score,
        passed=passed,
        min_question_confidence=min_q_conf,
        deterministic_reason=reason,
        raw_answers_json={"questions": question_entries},
    )


def _read_field(obj: Any, *names: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        for name in names:
            if name in obj:
                return obj[name]
        return default
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


@dataclass(slots=True)
class DimensionGateSnapshot:
    dimension_code: str
    raw_dimension_score: float | None
    applied_threshold: float
    applied_weight: float
    is_blocker: bool
    passed: bool
    min_question_confidence: float | None


@dataclass(slots=True)
class CaseGateResult:
    overall_score: float
    overall_passed: bool
    min_confidence: float | None
    needs_human_review: bool
    dimension_results: dict[str, DimensionGateSnapshot]


def evaluate_suite_gate(
    dimension_items: Sequence[Any],
    *,
    pass_policy: str = "ALL_BLOCKERS_AND_WEIGHTED",
    suite_pass_threshold: float = 0.75,
    confidence_review_floor: float = 0.50,
) -> CaseGateResult:
    """根据各维度连续得分与阈值、权重、一票否决配置判定单条用例套件总分与门禁结果。"""
    policy = (pass_policy or "ALL_BLOCKERS_AND_WEIGHTED").strip().upper()
    if policy not in ("ALL_BLOCKERS_AND_WEIGHTED", "ALL_DIMENSIONS"):
        raise ValueError(f"不支持的套件门禁策略: {pass_policy!r}")

    suite_t = float(suite_pass_threshold)
    review_floor = float(confidence_review_floor)

    dim_snaps: dict[str, DimensionGateSnapshot] = {}
    weighted_sum = 0.0
    total_weight = 0.0
    confs: list[float] = []

    for idx, item in enumerate(dimension_items):
        code = str(_read_field(item, "dimension_code", default=f"dim_{idx + 1}"))
        raw_score_val = _read_field(item, "raw_dimension_score", "score", default=None)
        raw_score = float(raw_score_val) if raw_score_val is not None else None
        threshold = float(_read_field(item, "applied_threshold", "threshold", default=0.75))
        weight = float(_read_field(item, "applied_weight", "weight", default=1.0))
        is_blocker = bool(_read_field(item, "is_blocker", default=False))
        min_q_conf_val = _read_field(
            item, "min_question_confidence", "min_confidence", default=None
        )
        min_q_conf = float(min_q_conf_val) if min_q_conf_val is not None else None

        if raw_score is None:
            passed = True
        else:
            passed = raw_score >= threshold
            weighted_sum += weight * raw_score
            total_weight += weight

        if min_q_conf is not None:
            confs.append(min_q_conf)

        dim_snaps[code] = DimensionGateSnapshot(
            dimension_code=code,
            raw_dimension_score=round(raw_score, 5) if raw_score is not None else None,
            applied_threshold=threshold,
            applied_weight=weight,
            is_blocker=is_blocker,
            passed=passed,
            min_question_confidence=round(min_q_conf, 4) if min_q_conf is not None else None,
        )

    if total_weight > 1e-9:
        overall_score = round(max(0.0, min(1.0, weighted_sum / total_weight)), 4)
    else:
        overall_score = 1.0 if dim_snaps else 0.0

    if not dim_snaps:
        overall_passed = False
    elif policy == "ALL_DIMENSIONS":
        all_dims_ok = all(s.passed for s in dim_snaps.values())
        overall_passed = all_dims_ok and (overall_score >= suite_t)
    else:
        blockers_ok = all(s.passed for s in dim_snaps.values() if s.is_blocker)
        overall_passed = blockers_ok and (overall_score >= suite_t)

    min_conf = round(min(confs), 4) if confs else None
    needs_human_review = min_conf is not None and min_conf < review_floor

    return CaseGateResult(
        overall_score=overall_score,
        overall_passed=overall_passed,
        min_confidence=min_conf,
        needs_human_review=needs_human_review,
        dimension_results=dim_snaps,
    )


def recalculate_case_from_ledgers(
    ledgers: Sequence[Any],
    *,
    pass_policy: str = "ALL_BLOCKERS_AND_WEIGHTED",
    suite_pass_threshold: float = 0.75,
    confidence_review_floor: float = 0.50,
    dimension_overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> CaseGateResult:
    """基于原始概率账本记录纯内存零模型调用重算单条用例总分与及格态（与在线跑批 100% 数值一致）。"""
    overrides = dimension_overrides or {}
    rebuilt_items: list[dict[str, Any]] = []

    for idx, ledger in enumerate(ledgers):
        code = str(_read_field(ledger, "dimension_code", default=f"dim_{idx + 1}"))
        ov = overrides.get(code) or {}

        threshold = float(
            ov["threshold"]
            if "threshold" in ov and ov["threshold"] is not None
            else _read_field(ledger, "applied_threshold", "threshold", default=0.75)
        )
        weight = float(
            ov["weight"]
            if "weight" in ov and ov["weight"] is not None
            else _read_field(ledger, "applied_weight", "weight", default=1.0)
        )
        is_blocker = bool(
            ov["is_blocker"]
            if "is_blocker" in ov and ov["is_blocker"] is not None
            else _read_field(ledger, "is_blocker", default=False)
        )

        raw_score_val = _read_field(ledger, "raw_dimension_score", default=None)
        raw_answers = _read_field(ledger, "raw_answers_json", default=None)

        if "strict_mode" in ov and ov["strict_mode"] is not None and isinstance(raw_answers, Mapping):
            questions = raw_answers.get("questions")
            if isinstance(questions, Sequence):
                raw_score_val, _ = compute_dimension_score_from_questions(
                    questions, strict_mode=bool(ov["strict_mode"])
                )
        elif raw_score_val is None and isinstance(raw_answers, Mapping):
            questions = raw_answers.get("questions")
            if isinstance(questions, Sequence):
                raw_score_val, _ = compute_dimension_score_from_questions(questions)

        min_q_conf = _read_field(ledger, "min_question_confidence", default=None)

        rebuilt_items.append(
            {
                "dimension_code": code,
                "raw_dimension_score": raw_score_val,
                "applied_threshold": threshold,
                "applied_weight": weight,
                "is_blocker": is_blocker,
                "min_question_confidence": min_q_conf,
            }
        )

    return evaluate_suite_gate(
        rebuilt_items,
        pass_policy=pass_policy,
        suite_pass_threshold=suite_pass_threshold,
        confidence_review_floor=confidence_review_floor,
    )

