from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.common.errors import AppError
from app.db.models import EvalCase, EvalRun, EvalRunCase, ProbabilityLedger
from app.kernel import SEMANTIC_REGRESSION_TAGS
from app.runner.executor import get_run_by_no_or_404, serialize_run_detail

_REGRESSION_SORT_PRIORITY: dict[str | None, int] = {
    "FACT_DIFFERS": 0,
    "ACTION_DIFFERS": 1,
    "SPECIFICITY_DIFFERS": 2,
    "SAME": 3,
    None: 4,
}


def _normalize_bool_filter(val: int | bool | str | None, field_name: str) -> int | None:
    if val is None:
        return None
    if isinstance(val, bool):
        return 1 if val else 0
    if isinstance(val, int):
        if val in (0, 1):
            return val
        raise AppError(f"{field_name} 仅允许 0 或 1", status_code=400, code=400)
    txt = str(val).strip().lower()
    if txt in ("1", "true"):
        return 1
    if txt in ("0", "false"):
        return 0
    raise AppError(f"{field_name} 参数格式非法: {val!r}", status_code=400, code=400)


def _parse_case_ids_csv(case_ids: str | None) -> list[int] | None:
    if case_ids is None or not case_ids.strip():
        return None
    parsed: list[int] = []
    for part in case_ids.split(","):
        tok = part.strip()
        if not tok:
            continue
        try:
            parsed.append(int(tok))
        except ValueError as exc:
            raise AppError(f"case_ids 包含非法整数: {tok!r}", status_code=400, code=400) from exc
    return parsed


def _serialize_ledger(row: ProbabilityLedger) -> dict[str, Any]:
    return {
        "id": row.id,
        "dimension_id": row.dimension_id,
        "dimension_code": row.dimension_code,
        "dimension_version": int(row.dimension_version),
        "evaluator_kind": row.evaluator_kind,
        "raw_dimension_score": (
            float(row.raw_dimension_score) if row.raw_dimension_score is not None else None
        ),
        "applied_threshold": float(row.applied_threshold),
        "applied_weight": float(row.applied_weight),
        "is_blocker": bool(row.is_blocker),
        "passed": bool(row.passed) if row.passed is not None else None,
        "min_question_confidence": (
            float(row.min_question_confidence)
            if row.min_question_confidence is not None
            else None
        ),
        "deterministic_reason": row.deterministic_reason,
        "raw_answers_json": row.raw_answers_json,
        "waf_fallback_used": bool(row.waf_fallback_used),
        "upstream_request_id": row.upstream_request_id,
        "eval_latency_ms": row.eval_latency_ms,
        "input_tokens": row.input_tokens,
    }


def list_run_cases(
    db: Session,
    run_no: str,
    *,
    passed: int | bool | str | None = None,
    needs_human_review: int | bool | str | None = None,
    regression_tag: str | None = None,
    exec_status: str | None = None,
    case_ids: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    run = get_run_by_no_or_404(db, run_no)

    passed_int = _normalize_bool_filter(passed, "passed")
    review_int = _normalize_bool_filter(needs_human_review, "needs_human_review")
    parsed_case_ids = _parse_case_ids_csv(case_ids)

    filters = [
        EvalRunCase.run_id == run.id,
        EvalRunCase.is_deleted == 0,
    ]
    if passed_int is not None:
        filters.append(EvalRunCase.overall_passed == passed_int)
    if review_int is not None:
        filters.append(EvalRunCase.needs_human_review == review_int)
    if regression_tag and regression_tag.strip():
        filters.append(EvalRunCase.regression_tag == regression_tag.strip().upper())
    if exec_status and exec_status.strip():
        filters.append(EvalRunCase.exec_status == exec_status.strip().upper())
    if parsed_case_ids is not None:
        if not parsed_case_ids:
            return {"items": [], "total": 0, "page": page, "page_size": page_size}
        filters.append(EvalRunCase.case_id.in_(parsed_case_ids))

    if q and q.strip():
        kw = f"%{q.strip()}%"
        filters.append(
            or_(
                EvalCase.case_code.like(kw),
                EvalCase.input_text.like(kw),
                EvalCase.expected_output.like(kw),
                EvalRunCase.actual_output.like(kw),
            )
        )

    total = int(
        db.scalar(
            select(func.count(EvalRunCase.id))
            .select_from(EvalRunCase)
            .join(EvalCase, EvalRunCase.case_id == EvalCase.id)
            .where(*filters)
        )
        or 0
    )

    offset_val = max(0, (page - 1) * page_size)
    joined_rows = db.execute(
        select(EvalRunCase, EvalCase)
        .join(EvalCase, EvalRunCase.case_id == EvalCase.id)
        .where(*filters)
        .order_by(EvalRunCase.id.asc())
        .offset(offset_val)
        .limit(page_size)
    ).all()

    if not joined_rows:
        return {"items": [], "total": total, "page": page, "page_size": page_size}

    rc_ids = [rc.id for rc, _ in joined_rows]
    c_ids = [rc.case_id for rc, _ in joined_rows]

    ledger_rows = db.scalars(
        select(ProbabilityLedger)
        .where(
            ProbabilityLedger.run_case_id.in_(rc_ids),
            ProbabilityLedger.is_deleted == 0,
        )
        .order_by(ProbabilityLedger.id.asc())
    ).all()
    ledgers_by_rc: dict[int, list[dict[str, Any]]] = {}
    for l_row in ledger_rows:
        ledgers_by_rc.setdefault(l_row.run_case_id, []).append(_serialize_ledger(l_row))

    baseline_outputs: dict[int, str | None] = {}
    if run.baseline_run_id is not None:
        b_rows = db.execute(
            select(EvalRunCase.case_id, EvalRunCase.actual_output).where(
                EvalRunCase.run_id == run.baseline_run_id,
                EvalRunCase.case_id.in_(c_ids),
                EvalRunCase.is_deleted == 0,
            )
        ).all()
        for b_cid, b_out in b_rows:
            baseline_outputs[int(b_cid)] = b_out

    items: list[dict[str, Any]] = []
    for rc, ev_case in joined_rows:
        items.append(
            {
                "id": rc.id,
                "run_id": rc.run_id,
                "case_id": rc.case_id,
                "case_code": ev_case.case_code,
                "dataset_split": ev_case.dataset_split,
                "turn_mode": ev_case.turn_mode,
                "difficulty": ev_case.difficulty,
                "input_text": ev_case.input_text,
                "conversation_turns_json": ev_case.conversation_turns_json,
                "expected_output": ev_case.expected_output,
                "expected_structured_json": ev_case.expected_structured_json,
                "reference_contexts_json": ev_case.reference_contexts_json,
                "exec_status": rc.exec_status,
                "actual_output": rc.actual_output,
                "baseline_output": baseline_outputs.get(rc.case_id),
                "actual_references_json": rc.actual_references_json,
                "actual_structured_json": rc.actual_structured_json,
                "sut_trace_json": rc.sut_trace_json,
                "sut_latency_ms": rc.sut_latency_ms,
                "ttft_ms": rc.ttft_ms,
                "overall_score": float(rc.overall_score) if rc.overall_score is not None else None,
                "overall_passed": (
                    bool(rc.overall_passed) if rc.overall_passed is not None else None
                ),
                "min_confidence": (
                    float(rc.min_confidence) if rc.min_confidence is not None else None
                ),
                "needs_human_review": bool(rc.needs_human_review),
                "regression_tag": rc.regression_tag,
                "regression_confidence": (
                    float(rc.regression_confidence)
                    if rc.regression_confidence is not None
                    else None
                ),
                "error_detail": rc.error_detail,
                "ledgers": ledgers_by_rc.get(rc.id, []),
            }
        )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _opt_delta(cur: float | int | None, base: float | int | None, digits: int = 4) -> float | int | None:
    if cur is None or base is None:
        return None
    if digits == 0:
        return int(cur) - int(base)
    return round(float(cur) - float(base), digits)


def compare_eval_runs(
    db: Session,
    run_no: str,
    baseline_run_no: str,
    *,
    regression_tag: str | None = None,
    only_changed: bool = False,
) -> dict[str, Any]:
    cur_run = get_run_by_no_or_404(db, run_no)
    base_run = get_run_by_no_or_404(db, baseline_run_no)

    cur_detail = serialize_run_detail(db, cur_run)
    base_detail = serialize_run_detail(db, base_run)

    cross_model_warning = bool(cur_run.pinned_model_name != base_run.pinned_model_name)
    cross_model_warning_message = (
        f"标尺变更提示：当前批次判别模型（{cur_run.pinned_model_name}）与基线批次"
        f"（{base_run.pinned_model_name}）不一致。不同判别模型的原始概率标尺存在差异，"
        "涨跌分值仅供参考，请重点审阅「语义回归差异」明细。"
        if cross_model_warning
        else None
    )

    kpi_deltas = {
        "current_pass_rate": cur_detail["pass_rate"],
        "baseline_pass_rate": base_detail["pass_rate"],
        "delta_pass_rate": _opt_delta(cur_detail["pass_rate"], base_detail["pass_rate"], 4),
        "current_weighted_avg_score": cur_detail["weighted_avg_score"],
        "baseline_weighted_avg_score": base_detail["weighted_avg_score"],
        "delta_weighted_avg_score": _opt_delta(
            cur_detail["weighted_avg_score"], base_detail["weighted_avg_score"], 4
        ),
        "current_p50_sut_latency_ms": cur_detail["p50_sut_latency_ms"],
        "baseline_p50_sut_latency_ms": base_detail["p50_sut_latency_ms"],
        "delta_p50_sut_latency_ms": _opt_delta(
            cur_detail["p50_sut_latency_ms"], base_detail["p50_sut_latency_ms"], 0
        ),
        "current_p95_sut_latency_ms": cur_detail["p95_sut_latency_ms"],
        "baseline_p95_sut_latency_ms": base_detail["p95_sut_latency_ms"],
        "delta_p95_sut_latency_ms": _opt_delta(
            cur_detail["p95_sut_latency_ms"], base_detail["p95_sut_latency_ms"], 0
        ),
        "current_p50_ttft_ms": cur_detail["p50_ttft_ms"],
        "baseline_p50_ttft_ms": base_detail["p50_ttft_ms"],
        "delta_p50_ttft_ms": _opt_delta(
            cur_detail["p50_ttft_ms"], base_detail["p50_ttft_ms"], 0
        ),
    }

    cur_dim_map: Mapping[str, Any] = (
        cur_run.dimension_summary_json
        if isinstance(cur_run.dimension_summary_json, Mapping)
        else {}
    )
    base_dim_map: Mapping[str, Any] = (
        base_run.dimension_summary_json
        if isinstance(base_run.dimension_summary_json, Mapping)
        else {}
    )
    ordered_dim_codes = list(cur_dim_map.keys()) + [
        k for k in base_dim_map.keys() if k not in cur_dim_map
    ]

    dimension_deltas: list[dict[str, Any]] = []
    for d_code in ordered_dim_codes:
        c_d = cur_dim_map.get(d_code) if isinstance(cur_dim_map.get(d_code), Mapping) else {}
        b_d = base_dim_map.get(d_code) if isinstance(base_dim_map.get(d_code), Mapping) else {}
        c_avg = c_d.get("avg_score")
        b_avg = b_d.get("avg_score")
        c_pr = c_d.get("pass_rate")
        b_pr = b_d.get("pass_rate")
        dimension_deltas.append(
            {
                "dimension_code": d_code,
                "dimension_name": c_d.get("dimension_name") or b_d.get("dimension_name") or d_code,
                "evaluator_kind": c_d.get("evaluator_kind") or b_d.get("evaluator_kind"),
                "threshold": c_d.get("threshold", b_d.get("threshold")),
                "weight": c_d.get("weight", b_d.get("weight")),
                "is_blocker": bool(c_d.get("is_blocker", b_d.get("is_blocker", False))),
                "current_avg_score": c_avg,
                "baseline_avg_score": b_avg,
                "delta_avg_score": _opt_delta(c_avg, b_avg, 4),
                "current_pass_rate": c_pr,
                "baseline_pass_rate": b_pr,
                "delta_pass_rate": _opt_delta(c_pr, b_pr, 4),
            }
        )

    cur_cases = {
        rc.case_id: rc
        for rc in db.scalars(
            select(EvalRunCase).where(
                EvalRunCase.run_id == cur_run.id,
                EvalRunCase.is_deleted == 0,
            )
        ).all()
    }
    base_cases = {
        rc.case_id: rc
        for rc in db.scalars(
            select(EvalRunCase).where(
                EvalRunCase.run_id == base_run.id,
                EvalRunCase.is_deleted == 0,
            )
        ).all()
    }

    common_case_ids = sorted(set(cur_cases.keys()) & set(base_cases.keys()))
    cases_meta = (
        {
            c.id: c
            for c in db.scalars(
                select(EvalCase).where(EvalCase.id.in_(common_case_ids))
            ).all()
        }
        if common_case_ids
        else {}
    )

    cur_ledgers_by_rc: dict[int, dict[str, ProbabilityLedger]] = {}
    if cur_cases:
        for l_row in db.scalars(
            select(ProbabilityLedger).where(
                ProbabilityLedger.run_id == cur_run.id,
                ProbabilityLedger.is_deleted == 0,
            )
        ).all():
            cur_ledgers_by_rc.setdefault(l_row.run_case_id, {})[l_row.dimension_code] = l_row

    base_ledgers_by_rc: dict[int, dict[str, ProbabilityLedger]] = {}
    if base_cases:
        for l_row in db.scalars(
            select(ProbabilityLedger).where(
                ProbabilityLedger.run_id == base_run.id,
                ProbabilityLedger.is_deleted == 0,
            )
        ).all():
            base_ledgers_by_rc.setdefault(l_row.run_case_id, {})[l_row.dimension_code] = l_row

    is_bound_baseline = cur_run.baseline_run_id == base_run.id
    reg_counts: dict[str, int] = {t: 0 for t in SEMANTIC_REGRESSION_TAGS}

    all_diffs: list[dict[str, Any]] = []
    for cid in common_case_ids:
        c_rc = cur_cases[cid]
        b_rc = base_cases[cid]
        ev_c = cases_meta.get(cid)

        if is_bound_baseline and c_rc.regression_tag in reg_counts:
            eff_tag: str | None = c_rc.regression_tag
            eff_conf: float | None = (
                float(c_rc.regression_confidence)
                if c_rc.regression_confidence is not None
                else None
            )
        elif (
            c_rc.actual_output is not None
            and b_rc.actual_output is not None
            and c_rc.actual_output.strip() == b_rc.actual_output.strip()
        ):
            eff_tag = "SAME"
            eff_conf = 1.0
        else:
            eff_tag = None
            eff_conf = None

        if eff_tag in reg_counts:
            reg_counts[eff_tag] += 1

        c_score = float(c_rc.overall_score) if c_rc.overall_score is not None else None
        b_score = float(b_rc.overall_score) if b_rc.overall_score is not None else None
        d_score = _opt_delta(c_score, b_score, 4)

        c_passed = bool(c_rc.overall_passed) if c_rc.overall_passed is not None else None
        b_passed = bool(b_rc.overall_passed) if b_rc.overall_passed is not None else None
        gate_flipped = (c_passed != b_passed) if (c_passed is not None and b_passed is not None) else False

        c_lmap = cur_ledgers_by_rc.get(c_rc.id, {})
        b_lmap = base_ledgers_by_rc.get(b_rc.id, {})
        dim_diffs: dict[str, dict[str, Any]] = {}
        for dc in sorted(set(c_lmap.keys()) | set(b_lmap.keys())):
            cl = c_lmap.get(dc)
            bl = b_lmap.get(dc)
            cs = float(cl.raw_dimension_score) if cl and cl.raw_dimension_score is not None else None
            bs = float(bl.raw_dimension_score) if bl and bl.raw_dimension_score is not None else None
            dim_diffs[dc] = {
                "current_score": cs,
                "baseline_score": bs,
                "delta_score": _opt_delta(cs, bs, 4),
                "current_passed": bool(cl.passed) if cl and cl.passed is not None else None,
                "baseline_passed": bool(bl.passed) if bl and bl.passed is not None else None,
            }

        all_diffs.append(
            {
                "case_id": cid,
                "case_code": ev_c.case_code if ev_c else str(cid),
                "dataset_split": ev_c.dataset_split if ev_c else None,
                "input_text": ev_c.input_text if ev_c else "",
                "expected_output": ev_c.expected_output if ev_c else None,
                "regression_tag": eff_tag,
                "regression_confidence": eff_conf,
                "current_actual_output": c_rc.actual_output,
                "baseline_actual_output": b_rc.actual_output,
                "current_overall_score": c_score,
                "baseline_overall_score": b_score,
                "delta_overall_score": d_score,
                "current_passed": c_passed,
                "baseline_passed": b_passed,
                "gate_flipped": gate_flipped,
                "current_exec_status": c_rc.exec_status,
                "baseline_exec_status": b_rc.exec_status,
                "current_structured_json": c_rc.actual_structured_json,
                "baseline_structured_json": b_rc.actual_structured_json,
                "dimension_score_diffs": dim_diffs,
            }
        )

    # Order with FACT_DIFFERS strictly first, then ACTION_DIFFERS, SPECIFICITY_DIFFERS, SAME
    all_diffs.sort(
        key=lambda item: (
            _REGRESSION_SORT_PRIORITY.get(item["regression_tag"], 4),
            0 if (item["baseline_passed"] is True and item["current_passed"] is False) else 1,
            float(item["delta_overall_score"]) if item["delta_overall_score"] is not None else 0.0,
            int(item["case_id"]),
        )
    )

    filtered_diffs = all_diffs
    if regression_tag and regression_tag.strip():
        target_tag = regression_tag.strip().upper()
        filtered_diffs = [d for d in filtered_diffs if d["regression_tag"] == target_tag]
    if only_changed:
        filtered_diffs = [
            d for d in filtered_diffs if d["regression_tag"] != "SAME" or d["gate_flipped"]
        ]

    reg_summary = dict(reg_counts)
    reg_summary["total_compared"] = sum(reg_counts.values())

    return {
        "cross_model_warning": cross_model_warning,
        "cross_model_warning_message": cross_model_warning_message,
        "current_run": cur_detail,
        "baseline_run": base_detail,
        "kpi_deltas": kpi_deltas,
        "dimension_deltas": dimension_deltas,
        "regression_summary": reg_summary,
        "case_diffs": filtered_diffs,
    }
