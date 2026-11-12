from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
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

    # Bulk query 1: lightweight tuple projection of SUCCESS EvalRunCase rows
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

    # Bulk query 2: lightweight tuple projection of ProbabilityLedger rows (no N+1)
    if needs_raw_answers:
        ledger_tuples = db.execute(
            select(
                ProbabilityLedger.run_case_id,
                ProbabilityLedger.dimension_code,
                ProbabilityLedger.raw_dimension_score,
                ProbabilityLedger.applied_threshold,
                ProbabilityLedger.applied_weight,
                ProbabilityLedger.is_blocker,
                ProbabilityLedger.min_question_confidence,
                ProbabilityLedger.raw_answers_json,
            )
            .where(
                ProbabilityLedger.run_id == run.id,
                ProbabilityLedger.is_deleted == 0,
            )
            .order_by(ProbabilityLedger.id.asc())
        ).all()
        ledgers_by_rc: dict[int, list[dict[str, Any]]] = {}
        for (
            rc_id,
            d_code,
            raw_s,
            app_t,
            app_w,
            blocker,
            min_c,
            raw_ans,
        ) in ledger_tuples:
            ledgers_by_rc.setdefault(int(rc_id), []).append(
                {
                    "dimension_code": d_code,
                    "raw_dimension_score": raw_s,
                    "applied_threshold": app_t,
                    "applied_weight": app_w,
                    "is_blocker": bool(blocker),
                    "min_question_confidence": min_c,
                    "raw_answers_json": raw_ans,
                }
            )
    else:
        ledger_tuples_fast = db.execute(
            select(
                ProbabilityLedger.run_case_id,
                ProbabilityLedger.dimension_code,
                ProbabilityLedger.raw_dimension_score,
                ProbabilityLedger.applied_threshold,
                ProbabilityLedger.applied_weight,
                ProbabilityLedger.is_blocker,
                ProbabilityLedger.min_question_confidence,
            )
            .where(
                ProbabilityLedger.run_id == run.id,
                ProbabilityLedger.is_deleted == 0,
            )
            .order_by(ProbabilityLedger.id.asc())
        ).all()
        ledgers_by_rc = {}
        for rc_id, d_code, raw_s, app_t, app_w, blocker, min_c in ledger_tuples_fast:
            ledgers_by_rc.setdefault(int(rc_id), []).append(
                {
                    "dimension_code": d_code,
                    "raw_dimension_score": raw_s,
                    "applied_threshold": app_t,
                    "applied_weight": app_w,
                    "is_blocker": bool(blocker),
                    "min_question_confidence": min_c,
                }
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
    return None


def _extract_case_label(
    *,
    case_id: int,
    case_code: str,
    expected_structured: Any,
    explicit_labels: Mapping[str, Any] | None,
) -> bool | None:
    if explicit_labels:
        for k in (str(case_id), case_code):
            if k in explicit_labels:
                coerced = _coerce_binary_label(explicit_labels[k])
                if coerced is not None:
                    return coerced
    if isinstance(expected_structured, Mapping):
        for key in ("expected_pass", "human_pass", "ground_truth_pass", "usable"):
            if key in expected_structured:
                coerced = _coerce_binary_label(expected_structured[key])
                if coerced is not None:
                    return coerced
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


def _grid_search_distribution_fallback(scores: Sequence[float], default_t: float) -> float:
    if not scores:
        return round(min(0.95, max(0.10, default_t)), 2)
    s_sorted = sorted(scores)
    spread = s_sorted[-1] - s_sorted[0]
    if spread < 0.12:
        p25_idx = max(0, int(len(s_sorted) * 0.25))
        cand = min(0.90, max(0.50, round(s_sorted[p25_idx], 2)))
        return cand

    # Otsu between-class variance maximization across grid
    n = float(len(s_sorted))
    best_var = -1.0
    best_ts: list[float] = []
    for t in _THRESHOLD_GRID:
        low = [x for x in s_sorted if x < t]
        high = [x for x in s_sorted if x >= t]
        if not low or not high:
            continue
        w0 = len(low) / n
        w1 = len(high) / n
        mu0 = sum(low) / len(low)
        mu1 = sum(high) / len(high)
        between_var = w0 * w1 * ((mu1 - mu0) ** 2)
        if between_var > best_var + 1e-9:
            best_var = between_var
            best_ts = [t]
        elif abs(between_var - best_var) <= 1e-9:
            best_ts.append(t)

    if not best_ts:
        return round(min(0.95, max(0.10, default_t)), 2)
    return best_ts[len(best_ts) // 2]


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
        raise AppError("当前 DEV 批次暂无成功完成的评测样本，无法执行自动阈值寻优", status_code=400, code=400)

    suite = db.scalar(select(Suite).where(Suite.id == run.suite_id))
    default_suite_t = float(suite.suite_pass_threshold) if suite is not None else 0.75

    labels_by_rc: dict[int, bool] = {}
    for r in success_rows:
        lbl = _extract_case_label(
            case_id=int(r.case_id),
            case_code=str(r.case_code),
            expected_structured=r.expected_structured_json,
            explicit_labels=req.labels,
        )
        if lbl is not None:
            labels_by_rc[int(r.id)] = lbl

    has_both_classes = (
        len(labels_by_rc) >= 2
        and any(v is True for v in labels_by_rc.values())
        and any(v is False for v in labels_by_rc.values())
    )
    label_source = "ground_truth" if has_both_classes else "distribution_fallback"

    ledger_rows = db.execute(
        select(
            ProbabilityLedger.run_case_id,
            ProbabilityLedger.dimension_code,
            ProbabilityLedger.evaluator_kind,
            ProbabilityLedger.raw_dimension_score,
            ProbabilityLedger.applied_threshold,
            ProbabilityLedger.applied_weight,
            ProbabilityLedger.is_blocker,
        )
        .where(
            ProbabilityLedger.run_id == run.id,
            ProbabilityLedger.is_deleted == 0,
        )
        .order_by(ProbabilityLedger.id.asc())
    ).all()

    by_dim: dict[str, list[tuple[int, float, float, float, bool, str]]] = {}
    for rc_id, d_code, ev_kind, raw_s, app_t, app_w, blocker in ledger_rows:
        if raw_s is None:
            continue
        by_dim.setdefault(str(d_code), []).append(
            (int(rc_id), float(raw_s), float(app_t), float(app_w), bool(blocker), str(ev_kind))
        )

    recommended_dim_overrides: dict[str, dict[str, Any]] = {}
    recalc_ov_models: dict[str, DimensionOverrideInput] = {}

    for d_code, items in by_dim.items():
        orig_t = items[0][2]
        orig_w = items[0][3]
        orig_blocker = items[0][4]
        ev_kind = items[0][5]
        all_scores = [it[1] for it in items]

        # Keep binary exact-match deterministic dimensions (scores strictly in {0.0, 1.0}) at their exact threshold
        is_binary_det = (
            ev_kind == "DETERMINISTIC"
            and orig_t >= 0.99
            and all(s <= 1e-6 or s >= 1.0 - 1e-6 for s in all_scores)
        )
        if is_binary_det:
            rec_t = orig_t
            dim_yj: float | None = None
            dim_f1: float | None = None
        elif has_both_classes:
            labeled_pairs = [
                (it[1], labels_by_rc[it[0]]) for it in items if it[0] in labels_by_rc
            ]
            if (
                len(labeled_pairs) >= 2
                and any(lbl for _, lbl in labeled_pairs)
                and any(not lbl for _, lbl in labeled_pairs)
            ):
                rec_t, dim_yj, dim_f1 = _grid_search_supervised(labeled_pairs, req.metric)
            else:
                rec_t = _grid_search_distribution_fallback(all_scores, orig_t)
                dim_yj = None
                dim_f1 = None
        else:
            rec_t = _grid_search_distribution_fallback(all_scores, orig_t)
            dim_yj = None
            dim_f1 = None

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

    overall_scores = [
        (int(r.id), float(r.overall_score))
        for r in success_rows
        if r.overall_score is not None
    ]
    if has_both_classes:
        suite_pairs = [
            (score, labels_by_rc[rc_id])
            for rc_id, score in overall_scores
            if rc_id in labels_by_rc
        ]
        rec_suite_t, best_yj, best_f1 = _grid_search_supervised(suite_pairs, req.metric)
    else:
        rec_suite_t = _grid_search_distribution_fallback(
            [s for _, s in overall_scores], default_suite_t
        )
        best_yj = None
        best_f1 = None

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
        "label_source": label_source,
        "labeled_cases": len(labels_by_rc),
        "suite_pass_threshold": rec_suite_t,
        "dimension_overrides": recommended_dim_overrides,
        "best_youden_j": best_yj,
        "best_f1": best_f1,
        "preview": preview,
    }
