from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.errors import AppError
from app.db.models import EvalCase, EvalRunCase, ProbabilityLedger, Suite
from app.kernel import recalculate_case_from_ledgers
from app.runner.executor import get_run_by_no_or_404
from app.runner.schemas import (
    DimensionOverrideInput,
    RecalculateRunRequest,
    RecommendThresholdsRequest,
)

_THRESHOLD_GRID: tuple[float, ...] = tuple(round(0.10 + 0.01 * i, 2) for i in range(86))


@dataclass(slots=True)
class _DimensionCaseScore:
    run_case_id: int
    raw_score: float
    applied_threshold: float
    applied_weight: float
    is_blocker: bool
    evaluator_kind: str


def _normalize_overrides_dict(
    raw_overrides: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if not raw_overrides:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for dim_code, ov in raw_overrides.items():
        if hasattr(ov, "model_dump"):
            dumped = ov.model_dump(exclude_none=True)
        elif isinstance(ov, Mapping):
            dumped = {k: v for k, v in ov.items() if v is not None}
        else:
            continue
        out[str(dim_code)] = dumped
    return out


def _load_ledgers_by_run_case(
    db: Session,
    *,
    run_id: int,
    include_raw_answers: bool = False,
) -> dict[int, list[dict[str, Any]]]:
    columns: list[Any] = [
        ProbabilityLedger.run_case_id,
        ProbabilityLedger.dimension_code,
        ProbabilityLedger.evaluator_kind,
        ProbabilityLedger.raw_dimension_score,
        ProbabilityLedger.applied_threshold,
        ProbabilityLedger.applied_weight,
        ProbabilityLedger.is_blocker,
        ProbabilityLedger.min_question_confidence,
    ]
    if include_raw_answers:
        columns.append(ProbabilityLedger.raw_answers_json)

    rows = db.execute(
        select(*columns)
        .where(
            ProbabilityLedger.run_id == run_id,
            ProbabilityLedger.is_deleted == 0,
        )
        .order_by(ProbabilityLedger.id.asc())
    ).all()

    ledgers_by_rc: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        item: dict[str, Any] = {
            "dimension_code": row[1],
            "evaluator_kind": row[2],
            "raw_dimension_score": row[3],
            "applied_threshold": row[4],
            "applied_weight": row[5],
            "is_blocker": bool(row[6]),
            "min_question_confidence": row[7],
        }
        if include_raw_answers:
            item["raw_answers_json"] = row[8]
        ledgers_by_rc.setdefault(int(row[0]), []).append(item)
    return ledgers_by_rc


def recalculate_run(
    db: Session,
    run_no: str,
    payload: RecalculateRunRequest | None = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    req = payload or RecalculateRunRequest()
    run = get_run_by_no_or_404(db, run_no)
    suite = db.scalar(select(Suite).where(Suite.id == run.suite_id))

    eff_policy = (
        req.pass_policy
        if req.pass_policy is not None
        else (suite.pass_policy if suite is not None else "ALL_BLOCKERS_AND_WEIGHTED")
    )
    eff_suite_t = (
        float(req.suite_pass_threshold)
        if req.suite_pass_threshold is not None
        else float(suite.suite_pass_threshold if suite is not None else 0.75)
    )
    eff_floor = (
        float(req.confidence_review_floor)
        if req.confidence_review_floor is not None
        else float(suite.confidence_review_floor if suite is not None else 0.50)
    )

    overrides_map = _normalize_overrides_dict(req.dimension_overrides)
    needs_raw_answers = any("strict_mode" in ov for ov in overrides_map.values())

    case_rows = db.execute(
        select(
            EvalRunCase.id,
            EvalRunCase.case_id,
            EvalRunCase.overall_passed,
        )
        .where(
            EvalRunCase.run_id == run.id,
            EvalRunCase.exec_status == "SUCCESS",
            EvalRunCase.is_deleted == 0,
        )
        .order_by(EvalRunCase.id.asc())
    ).all()

    if not case_rows:
        elapsed_empty = int(round((time.perf_counter() - t0) * 1000))
        return {
            "recalculated_pass_rate": 0.0,
            "delta_pass_rate": 0.0,
            "recalculated_weighted_avg_score": 0.0,
            "delta_weighted_avg_score": 0.0,
            "passed_cases": 0,
            "failed_cases": 0,
            "dimension_pass_rates": {},
            "flipped_case_ids": [],
            "compute_elapsed_ms": elapsed_empty,
        }

    ledgers_by_rc = _load_ledgers_by_run_case(
        db,
        run_id=run.id,
        include_raw_answers=needs_raw_answers,
    )

    passed_cases = 0
    failed_cases = 0
    score_sum = 0.0
    flipped_case_ids: list[int] = []
    dim_passed_counts: dict[str, int] = {}
    dim_total_counts: dict[str, int] = {}

    for rc_id, case_id, orig_passed in case_rows:
        rc_ledgers = ledgers_by_rc.get(int(rc_id), [])
        gate = recalculate_case_from_ledgers(
            rc_ledgers,
            pass_policy=eff_policy,
            suite_pass_threshold=eff_suite_t,
            confidence_review_floor=eff_floor,
            dimension_overrides=overrides_map,
        )
        if gate.overall_passed:
            passed_cases += 1
        else:
            failed_cases += 1
        score_sum += gate.overall_score

        orig_bool = bool(orig_passed) if orig_passed is not None else False
        if gate.overall_passed != orig_bool:
            flipped_case_ids.append(int(case_id))

        for d_code, d_snap in gate.dimension_results.items():
            dim_total_counts[d_code] = dim_total_counts.get(d_code, 0) + 1
            if d_snap.passed:
                dim_passed_counts[d_code] = dim_passed_counts.get(d_code, 0) + 1

    n_cases = len(case_rows)
    recalc_pr = round(passed_cases / n_cases, 4)
    orig_pr = float(run.pass_rate) if run.pass_rate is not None else 0.0
    delta_pr = round(recalc_pr - orig_pr, 4)

    recalc_avg = round(score_sum / n_cases, 4)
    orig_avg = float(run.weighted_avg_score) if run.weighted_avg_score is not None else 0.0
    delta_avg = round(recalc_avg - orig_avg, 4)

    dim_pass_rates = {
        d_code: round(dim_passed_counts.get(d_code, 0) / total_c, 4)
        for d_code, total_c in dim_total_counts.items()
        if total_c > 0
    }

    elapsed_ms = int(round((time.perf_counter() - t0) * 1000))
    return {
        "recalculated_pass_rate": recalc_pr,
        "delta_pass_rate": delta_pr,
        "recalculated_weighted_avg_score": recalc_avg,
        "delta_weighted_avg_score": delta_avg,
        "passed_cases": passed_cases,
        "failed_cases": failed_cases,
        "dimension_pass_rates": dim_pass_rates,
        "flipped_case_ids": flipped_case_ids,
        "compute_elapsed_ms": elapsed_ms,
    }


def _coerce_binary_label(val: Any) -> bool | None:
    if isinstance(val, bool):
        return val
    if isinstance(val, int) and val in (0, 1):
        return bool(val)
    if isinstance(val, str):
        norm = val.strip().upper()
        if norm in {"1", "TRUE", "PASS", "PASSED", "POSITIVE", "YES"}:
            return True
        if norm in {"0", "FALSE", "FAIL", "FAILED", "NEGATIVE", "NO"}:
            return False
    return None


def _extract_case_label(
    *,
    case_id: int,
    case_code: str,
    expected_structured: Any,
    expected_outcome: str | None = None,
    tags_json: Any = None,
    explicit_labels: Mapping[str, Any] | None,
) -> bool | None:
    if explicit_labels:
        for k in (str(case_id), case_code):
            if k in explicit_labels:
                coerced = _coerce_binary_label(explicit_labels[k])
                if coerced is not None:
                    return coerced
    if isinstance(expected_structured, Mapping):
        for key in (
            "expected_pass",
            "human_pass",
            "ground_truth_pass",
            "usable",
            "is_positive",
            "label",
            "pass",
        ):
            if key in expected_structured:
                coerced = _coerce_binary_label(expected_structured[key])
                if coerced is not None:
                    return coerced
    if expected_outcome:
        coerced_out = _coerce_binary_label(expected_outcome)
        if coerced_out is not None:
            return coerced_out
    if isinstance(tags_json, Sequence) and not isinstance(tags_json, (str, bytes)):
        for tag in tags_json:
            norm_tag = str(tag).strip().upper()
            if norm_tag in {"GT:PASS", "HUMAN_PASS", "LABEL:POSITIVE"}:
                return True
            if norm_tag in {"GT:FAIL", "HUMAN_FAIL", "LABEL:NEGATIVE"}:
                return False
    return None


def _evaluate_binary_metrics(
    pairs: Sequence[tuple[float, bool]],
    threshold: float,
) -> tuple[float, float]:
    tp = fp = tn = fn = 0
    for score, label in pairs:
        pred = score >= threshold
        if pred and label:
            tp += 1
        elif pred and not label:
            fp += 1
        elif not pred and not label:
            tn += 1
        else:
            fn += 1
    tpr = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    tnr = tn / (tn + fp) if (tn + fp) > 0 else 0.0
    youden_j = round(tpr + tnr - 1.0, 6)
    denom_f1 = 2 * tp + fp + fn
    f1 = round((2 * tp) / denom_f1, 6) if denom_f1 > 0 else 0.0
    return youden_j, f1


def _grid_search_supervised(
    pairs: Sequence[tuple[float, bool]],
    metric: str,
) -> tuple[float, float, float]:
    best_primary = -2.0
    best_secondary = -2.0
    best_candidates: list[tuple[float, float, float]] = []

    for t in _THRESHOLD_GRID:
        yj, f1 = _evaluate_binary_metrics(pairs, t)
        primary = yj if metric == "youden_j" else f1
        secondary = f1 if metric == "youden_j" else yj
        if primary > best_primary + 1e-9 or (
            abs(primary - best_primary) <= 1e-9 and secondary > best_secondary + 1e-9
        ):
            best_primary = primary
            best_secondary = secondary
            best_candidates = [(t, yj, f1)]
        elif abs(primary - best_primary) <= 1e-9 and abs(secondary - best_secondary) <= 1e-9:
            best_candidates.append((t, yj, f1))

    mid = best_candidates[len(best_candidates) // 2]
    return mid[0], round(mid[1], 4), round(mid[2], 4)


def recommend_run_thresholds(
    db: Session,
    run_no: str,
    payload: RecommendThresholdsRequest | None = None,
) -> dict[str, Any]:
    req = payload or RecommendThresholdsRequest()
    run = get_run_by_no_or_404(db, run_no)

    # STRICT GUARDRAIL (docs/01 §8 & DESIGN.md §12): DEV split ONLY!
    if run.dataset_split_filter != "DEV":
        raise AppError(
            "当前批次基于 HOLDOUT（留出验真集）或混合分集执行。"
            "为防止测试集过拟合，自动阈值寻优仅允许在 DEV（调优集）上运行。",
            status_code=400,
            code=400,
        )

    joined_cases = db.execute(
        select(
            EvalRunCase.id,
            EvalRunCase.case_id,
            EvalRunCase.exec_status,
            EvalRunCase.overall_score,
            EvalCase.case_code,
            EvalCase.dataset_split,
            EvalCase.expected_structured_json,
            EvalCase.expected_outcome,
            EvalCase.tags_json,
        )
        .join(EvalCase, EvalRunCase.case_id == EvalCase.id)
        .where(
            EvalRunCase.run_id == run.id,
            EvalRunCase.is_deleted == 0,
        )
        .order_by(EvalRunCase.id.asc())
    ).all()

    if any(str(row.dataset_split).upper() != "DEV" for row in joined_cases):
        raise AppError(
            "当前批次包含 HOLDOUT（留出验真集）样本。"
            "为防止测试集过拟合，自动阈值寻优仅允许读取 DEV（调优集）样本。",
            status_code=400,
            code=400,
        )

    success_rows = [r for r in joined_cases if r.exec_status == "SUCCESS"]
    if not success_rows:
        raise AppError(
            "当前 DEV 批次暂无成功完成的评测样本，无法执行自动阈值寻优",
            status_code=400,
            code=400,
        )

    labels_by_rc: dict[int, bool] = {}
    for r in success_rows:
        lbl = _extract_case_label(
            case_id=int(r.case_id),
            case_code=str(r.case_code),
            expected_structured=r.expected_structured_json,
            expected_outcome=r.expected_outcome,
            tags_json=r.tags_json,
            explicit_labels=req.labels,
        )
        if lbl is not None:
            labels_by_rc[int(r.id)] = lbl

    pos_count = sum(1 for v in labels_by_rc.values() if v is True)
    neg_count = sum(1 for v in labels_by_rc.values() if v is False)
    if pos_count < 1 or neg_count < 1:
        raise AppError(
            "监督式阈值寻优要求 DEV 批次至少包含 1 条正例与 1 条负例 Ground Truth 标注"
            "（请在真值校准台的 expected_structured_json 中标注 human_pass / expected_pass，"
            "或在请求 labels 中传入正负例标签后再试）。",
            status_code=400,
            code=400,
        )

    ledgers_by_rc = _load_ledgers_by_run_case(db, run_id=run.id, include_raw_answers=False)

    by_dim: dict[str, list[_DimensionCaseScore]] = {}
    for rc_id, rc_ledgers in ledgers_by_rc.items():
        for entry in rc_ledgers:
            raw_s = entry["raw_dimension_score"]
            if raw_s is None:
                continue
            d_code = str(entry["dimension_code"])
            by_dim.setdefault(d_code, []).append(
                _DimensionCaseScore(
                    run_case_id=int(rc_id),
                    raw_score=float(raw_s),
                    applied_threshold=float(entry["applied_threshold"]),
                    applied_weight=float(entry["applied_weight"]),
                    is_blocker=bool(entry["is_blocker"]),
                    evaluator_kind=str(entry["evaluator_kind"]),
                )
            )

    recommended_dim_overrides: dict[str, dict[str, Any]] = {}
    recalc_ov_models: dict[str, DimensionOverrideInput] = {}

    for d_code, items in by_dim.items():
        first = items[0]
        orig_t = first.applied_threshold
        orig_w = first.applied_weight
        orig_blocker = first.is_blocker
        ev_kind = first.evaluator_kind
        all_scores = [it.raw_score for it in items]

        is_binary_det = (
            ev_kind == "DETERMINISTIC"
            and orig_t >= 0.99
            and all(s <= 1e-6 or s >= 1.0 - 1e-6 for s in all_scores)
        )
        labeled_pairs = [
            (it.raw_score, labels_by_rc[it.run_case_id])
            for it in items
            if it.run_case_id in labels_by_rc
        ]
        has_dim_both_classes = (
            len(labeled_pairs) >= 2
            and any(lbl for _, lbl in labeled_pairs)
            and any(not lbl for _, lbl in labeled_pairs)
        )

        if is_binary_det or not has_dim_both_classes:
            rec_t = orig_t
            dim_yj: float | None = None
            dim_f1: float | None = None
        else:
            rec_t, dim_yj, dim_f1 = _grid_search_supervised(labeled_pairs, req.metric)

        recommended_dim_overrides[d_code] = {
            "threshold": rec_t,
            "weight": orig_w,
            "is_blocker": orig_blocker,
            "best_youden_j": dim_yj,
            "best_f1": dim_f1,
        }
        recalc_ov_models[d_code] = DimensionOverrideInput(
            threshold=rec_t,
            weight=orig_w,
            is_blocker=orig_blocker,
        )

    suite_pairs = [
        (float(r.overall_score), labels_by_rc[int(r.id)])
        for r in success_rows
        if r.overall_score is not None and int(r.id) in labels_by_rc
    ]
    if (
        len(suite_pairs) < 2
        or not any(lbl for _, lbl in suite_pairs)
        or not any(not lbl for _, lbl in suite_pairs)
    ):
        raise AppError(
            "当前已完成评分的 DEV 样本中缺少成对的正负例 Ground Truth 标注，无法执行监督式阈值寻优。",
            status_code=400,
            code=400,
        )
    rec_suite_t, best_yj, best_f1 = _grid_search_supervised(suite_pairs, req.metric)

    preview = recalculate_run(
        db,
        run_no,
        RecalculateRunRequest(
            suite_pass_threshold=rec_suite_t,
            dimension_overrides=recalc_ov_models,
        ),
    )

    return {
        "run_no": run.run_no,
        "dataset_split_filter": run.dataset_split_filter,
        "metric": req.metric,
        "label_source": "ground_truth",
        "labeled_cases": len(labels_by_rc),
        "suite_pass_threshold": rec_suite_t,
        "dimension_overrides": recommended_dim_overrides,
        "best_youden_j": best_yj,
        "best_f1": best_f1,
        "preview": preview,
    }
