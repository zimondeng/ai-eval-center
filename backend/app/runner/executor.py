from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.dispatcher import execute_sut_adapter
from app.adapters.models import SutAdapterConfig, SutExecutionError
from app.common.errors import AppError
from app.config import Settings, get_settings
from app.db.base import utcnow_naive
from app.db.models import (
    Dataset,
    DecisionProvider,
    Dimension,
    EvalCase,
    EvalRun,
    EvalRunCase,
    ProbabilityLedger,
    Suite,
    SutAdapter,
)
from app.db.session import build_session_factory
from app.kernel import (
    SEMANTIC_REGRESSION_TAGS,
    CaseEvaluationResult,
    DimensionLedgerEntry,
    SystemOneUpstreamError,
    evaluate_case_dimensions,
)
from app.runner.schemas import RunCreateRequest

_background_tasks: set[asyncio.Task[Any]] = set()
_cancel_events: dict[int, asyncio.Event] = {}


def _fmt_dt(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _percentile_int(sorted_values: Sequence[int], q: float) -> int | None:
    if not sorted_values:
        return None
    n = len(sorted_values)
    if n == 1:
        return int(sorted_values[0])
    pos = (n - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    val = sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)
    return int(round(val))


def _compute_histogram_10_bins(scores: Sequence[float]) -> list[int]:
    bins = [0] * 10
    for s in scores:
        idx = int(math.floor(max(0.0, min(1.0, float(s))) * 10.0))
        if idx >= 10:
            idx = 9
        bins[idx] += 1
    return bins


def _extract_run_overrides(sut_fingerprint: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(sut_fingerprint, Mapping):
        return None
    for key in ("run_overrides", "overrides", "config_overrides"):
        val = sut_fingerprint.get(key)
        if isinstance(val, Mapping):
            merged = dict(val)
            if "component" in sut_fingerprint and "component" not in merged:
                merged["component"] = sut_fingerprint["component"]
            return merged
    return None


def _generate_run_no(db: Session, now: datetime) -> str:
    date_part = now.strftime("%Y%m%d")
    prefix = f"RUN_{date_part}_"
    existing_count = int(
        db.scalar(select(func.count(EvalRun.id)).where(EvalRun.run_no.like(f"{prefix}%"))) or 0
    )
    seq = existing_count + 1
    for _ in range(20):
        candidate = f"{prefix}{seq:04d}"
        exists = db.scalar(select(EvalRun.id).where(EvalRun.run_no == candidate))
        if exists is None:
            return candidate
        seq += 1
    return f"{prefix}{seq:04d}_{uuid.uuid4().hex[:4].upper()}"


def _resolve_suite(db: Session, payload: RunCreateRequest) -> Suite:
    if payload.suite_id is not None:
        suite = db.scalar(
            select(Suite).where(Suite.id == payload.suite_id, Suite.is_deleted == 0)
        )
    elif payload.suite_code and payload.suite_code.strip():
        suite = db.scalar(
            select(Suite).where(
                Suite.suite_code == payload.suite_code.strip(),
                Suite.is_deleted == 0,
            )
        )
    else:
        raise AppError("必须提供 suite_code 或 suite_id", status_code=400, code=400)

    if suite is None:
        target = payload.suite_code or f"id={payload.suite_id}"
        raise AppError(f"评测套件不存在: {target}", status_code=404, code=404)
    if suite.status != "ACTIVE":
        raise AppError(f"评测套件未处于 ACTIVE 状态: {suite.suite_code}", status_code=400, code=400)
    return suite


def _resolve_adapter(db: Session, payload: RunCreateRequest, suite: Suite) -> SutAdapter:
    if payload.sut_adapter_code and payload.sut_adapter_code.strip():
        adapter = db.scalar(
            select(SutAdapter).where(
                SutAdapter.adapter_code == payload.sut_adapter_code.strip(),
                SutAdapter.is_deleted == 0,
            )
        )
    elif payload.sut_adapter_id is not None:
        adapter = db.scalar(
            select(SutAdapter).where(
                SutAdapter.id == payload.sut_adapter_id,
                SutAdapter.is_deleted == 0,
            )
        )
    elif suite.default_adapter_id is not None:
        adapter = db.scalar(
            select(SutAdapter).where(
                SutAdapter.id == suite.default_adapter_id,
                SutAdapter.is_deleted == 0,
            )
        )
    else:
        raise AppError("未指定被测适配器且评测套件无默认适配器", status_code=400, code=400)

    if adapter is None:
        raise AppError("指定的被测适配器不存在", status_code=404, code=404)
    if adapter.status != "ACTIVE":
        raise AppError(f"被测适配器未启用: {adapter.adapter_code}", status_code=400, code=400)
    if adapter.eval_level != suite.eval_level:
        raise AppError(
            f"被测适配器层级({adapter.eval_level})与套件层级({suite.eval_level})不匹配",
            status_code=400,
            code=400,
        )
    return adapter


def _resolve_provider(db: Session, payload: RunCreateRequest, suite: Suite) -> DecisionProvider:
    if payload.provider_code and payload.provider_code.strip():
        provider = db.scalar(
            select(DecisionProvider).where(
                DecisionProvider.provider_code == payload.provider_code.strip(),
                DecisionProvider.is_deleted == 0,
            )
        )
    elif payload.provider_id is not None:
        provider = db.scalar(
            select(DecisionProvider).where(
                DecisionProvider.id == payload.provider_id,
                DecisionProvider.is_deleted == 0,
            )
        )
    elif suite.default_provider_id is not None:
        provider = db.scalar(
            select(DecisionProvider).where(
                DecisionProvider.id == suite.default_provider_id,
                DecisionProvider.is_deleted == 0,
            )
        )
    else:
        provider = db.scalar(
            select(DecisionProvider)
            .where(
                DecisionProvider.is_default == 1,
                DecisionProvider.status == "ACTIVE",
                DecisionProvider.is_deleted == 0,
            )
            .order_by(DecisionProvider.id.asc())
        )

    if provider is None:
        raise AppError("未找到可用的判别模型渠道", status_code=404, code=404)
    if provider.status != "ACTIVE":
        raise AppError(f"判别模型渠道未启用: {provider.provider_code}", status_code=400, code=400)
    return provider


def _resolve_baseline_run(db: Session, payload: RunCreateRequest) -> EvalRun | None:
    if payload.baseline_run_id is not None:
        base = db.scalar(
            select(EvalRun).where(
                EvalRun.id == payload.baseline_run_id,
                EvalRun.is_deleted == 0,
            )
        )
        if base is None:
            raise AppError(
                f"基线实验批次不存在: id={payload.baseline_run_id}",
                status_code=404,
                code=404,
            )
        return base
    if payload.baseline_run_no and payload.baseline_run_no.strip():
        base = db.scalar(
            select(EvalRun).where(
                EvalRun.run_no == payload.baseline_run_no.strip(),
                EvalRun.is_deleted == 0,
            )
        )
        if base is None:
            raise AppError(
                f"基线实验批次不存在: {payload.baseline_run_no}",
                status_code=404,
                code=404,
            )
        return base
    return None


def _load_matching_approved_cases(
    db: Session,
    *,
    dataset_id: int,
    dataset_split_filter: str,
) -> list[EvalCase]:
    filters = [
        EvalCase.dataset_id == dataset_id,
        EvalCase.review_status == "APPROVED",
        EvalCase.is_deleted == 0,
    ]
    if dataset_split_filter in ("DEV", "HOLDOUT"):
        filters.append(EvalCase.dataset_split == dataset_split_filter)
    stmt = select(EvalCase).where(*filters).order_by(EvalCase.id.asc())
    return list(db.scalars(stmt).all())


def _compute_unbatched_questions_total(
    dim_summary: Mapping[str, Any] | None,
    reg_summary: Mapping[str, Any] | None,
) -> int:
    total = 0
    if isinstance(dim_summary, Mapping):
        for v in dim_summary.values():
            if isinstance(v, Mapping):
                total += int(v.get("unbatched_question_count", 0) or 0)
    if isinstance(reg_summary, Mapping):
        total += int(reg_summary.get("total_compared", 0) or 0)
    return total


def serialize_run_list_item(
    run: EvalRun,
    *,
    suite: Suite | None = None,
    dataset: Dataset | None = None,
    adapter: SutAdapter | None = None,
    provider: DecisionProvider | None = None,
    baseline_run: EvalRun | None = None,
) -> dict[str, Any]:
    unbatched_q = _compute_unbatched_questions_total(
        run.dimension_summary_json if isinstance(run.dimension_summary_json, Mapping) else None,
        run.regression_summary_json,
    )
    decision_calls = int(run.total_decision_calls or 0)
    saved_calls = max(0, unbatched_q - decision_calls)
    saving_rate = round(saved_calls / unbatched_q, 4) if unbatched_q > 0 else 0.0
    compression_ratio = round(unbatched_q / decision_calls, 2) if decision_calls > 0 else 1.0

    cross_model_warning = (
        bool(baseline_run.pinned_model_name != run.pinned_model_name)
        if baseline_run is not None
        else False
    )

    return {
        "id": run.id,
        "uuid": run.uuid,
        "run_no": run.run_no,
        "run_name": run.run_name,
        "suite_id": run.suite_id,
        "suite_code": suite.suite_code if suite is not None else None,
        "suite_name": suite.suite_name if suite is not None else None,
        "eval_level": suite.eval_level if suite is not None else None,
        "dataset_id": run.dataset_id,
        "dataset_code": dataset.dataset_code if dataset is not None else None,
        "dataset_name": dataset.dataset_name if dataset is not None else None,
        "dataset_version_tag": dataset.version_tag if dataset is not None else None,
        "dataset_split_filter": run.dataset_split_filter,
        "sut_adapter_id": run.sut_adapter_id,
        "adapter_code": adapter.adapter_code if adapter is not None else None,
        "adapter_name": adapter.adapter_name if adapter is not None else None,
        "adapter_type": adapter.adapter_type if adapter is not None else None,
        "sut_fingerprint_json": run.sut_fingerprint_json,
        "provider_id": run.provider_id,
        "provider_code": provider.provider_code if provider is not None else None,
        "provider_name": provider.provider_name if provider is not None else None,
        "pinned_provider_kind": run.pinned_provider_kind,
        "pinned_endpoint_url": run.pinned_endpoint_url,
        "pinned_model_name": run.pinned_model_name,
        "baseline_run_id": run.baseline_run_id,
        "baseline_run_no": baseline_run.run_no if baseline_run is not None else None,
        "baseline_pinned_model_name": (
            baseline_run.pinned_model_name if baseline_run is not None else None
        ),
        "cross_model_warning": cross_model_warning,
        "status": run.status,
        "total_cases": int(run.total_cases or 0),
        "completed_cases": int(run.completed_cases or 0),
        "passed_cases": int(run.passed_cases or 0),
        "failed_cases": int(run.failed_cases or 0),
        "error_cases": int(run.error_cases or 0),
        "uncertain_cases": int(run.uncertain_cases or 0),
        "pass_rate": float(run.pass_rate) if run.pass_rate is not None else None,
        "weighted_avg_score": (
            float(run.weighted_avg_score) if run.weighted_avg_score is not None else None
        ),
        "p50_sut_latency_ms": run.p50_sut_latency_ms,
        "p95_sut_latency_ms": run.p95_sut_latency_ms,
        "p50_ttft_ms": run.p50_ttft_ms,
        "total_decision_calls": decision_calls,
        "unbatched_question_count": unbatched_q,
        "saved_decision_calls": saved_calls,
        "batch_saving_rate": saving_rate,
        "batch_compression_ratio": compression_ratio,
        "total_input_tokens": int(run.total_input_tokens or 0),
        "total_eval_cost": float(run.total_eval_cost or 0.0),
        "dimension_summary_json": run.dimension_summary_json,
        "regression_summary_json": run.regression_summary_json,
        "error_message": run.error_message,
        "started_at": _fmt_dt(run.started_at),
        "finished_at": _fmt_dt(run.finished_at),
        "triggered_by": run.triggered_by,
        "create_time": _fmt_dt(run.create_time),
        "update_time": _fmt_dt(run.update_time),
    }


def serialize_run_detail(
    db: Session,
    run: EvalRun,
) -> dict[str, Any]:
    suite = db.scalar(select(Suite).where(Suite.id == run.suite_id))
    dataset = db.scalar(select(Dataset).where(Dataset.id == run.dataset_id))
    adapter = db.scalar(select(SutAdapter).where(SutAdapter.id == run.sut_adapter_id))
    provider = db.scalar(select(DecisionProvider).where(DecisionProvider.id == run.provider_id))
    baseline_run = (
        db.scalar(select(EvalRun).where(EvalRun.id == run.baseline_run_id))
        if run.baseline_run_id is not None
        else None
    )
    base_dict = serialize_run_list_item(
        run,
        suite=suite,
        dataset=dataset,
        adapter=adapter,
        provider=provider,
        baseline_run=baseline_run,
    )
    if suite is not None:
        base_dict["pass_policy"] = suite.pass_policy
        base_dict["suite_pass_threshold"] = float(suite.suite_pass_threshold)
        base_dict["confidence_review_floor"] = float(suite.confidence_review_floor)
        base_dict["enable_semantic_regression"] = bool(suite.enable_semantic_regression)
        base_dict["suite_dimensions"] = list(suite.dimensions_json or [])
    else:
        base_dict["pass_policy"] = "ALL_BLOCKERS_AND_WEIGHTED"
        base_dict["suite_pass_threshold"] = 0.75
        base_dict["confidence_review_floor"] = 0.50
        base_dict["enable_semantic_regression"] = False
        base_dict["suite_dimensions"] = []
    return base_dict


def get_run_by_no_or_404(db: Session, run_no: str) -> EvalRun:
    row = db.scalar(
        select(EvalRun).where(
            EvalRun.run_no == run_no.strip(),
            EvalRun.is_deleted == 0,
        )
    )
    if row is None:
        raise AppError(f"实验批次不存在: {run_no}", status_code=404, code=404)
    return row


@dataclass(slots=True)
class _CaseExecutionOutcome:
    case_id: int
    exec_status: str
    actual_output: str | None = None
    actual_references: list[str] | None = None
    actual_structured: dict[str, Any] | None = None
    sut_trace: Any = None
    sut_latency_ms: int | None = None
    ttft_ms: int | None = None
    eval_result: CaseEvaluationResult | None = None
    error_detail: str | None = None


def _build_dimension_and_regression_summaries(
    *,
    suite: Suite,
    dimensions_map: Mapping[tuple[str, int], Dimension],
    outcomes: Sequence[_CaseExecutionOutcome],
    baseline_run: EvalRun | None,
    enable_semantic_regression: bool,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    ledgers_by_dim: dict[str, list[DimensionLedgerEntry]] = {}
    for out in outcomes:
        if out.exec_status == "SUCCESS" and out.eval_result is not None:
            for entry in out.eval_result.ledger_entries:
                ledgers_by_dim.setdefault(entry.dimension_code, []).append(entry)

    baseline_dim_summary: Mapping[str, Any] = (
        baseline_run.dimension_summary_json
        if baseline_run is not None and isinstance(baseline_run.dimension_summary_json, Mapping)
        else {}
    )

    dim_summary: dict[str, dict[str, Any]] = {}
    for binding in suite.dimensions_json or []:
        code = str(binding["dimension_code"])
        ver = int(binding["version"])
        dim_obj = dimensions_map.get((code, ver))
        dim_id = int(dim_obj.id) if dim_obj is not None else 0
        dim_name = dim_obj.dimension_name if dim_obj is not None else code
        eval_kind = dim_obj.evaluator_kind if dim_obj is not None else "SYSTEM_ONE"
        q_count_per_case = (
            len(dim_obj.questions_json or [])
            if dim_obj is not None and eval_kind == "SYSTEM_ONE"
            else 0
        )

        entries = ledgers_by_dim.get(code, [])
        eval_cnt = len(entries)
        passed_cnt = sum(1 for e in entries if e.passed)
        valid_scores = [
            float(e.raw_dimension_score)
            for e in entries
            if e.raw_dimension_score is not None
        ]
        avg_score = round(sum(valid_scores) / len(valid_scores), 4) if valid_scores else None
        pass_rate = round(passed_cnt / eval_cnt, 4) if eval_cnt > 0 else 0.0
        hist = _compute_histogram_10_bins(valid_scores)
        unbatched_q = q_count_per_case * eval_cnt

        base_entry = baseline_dim_summary.get(code)
        base_avg = (
            float(base_entry["avg_score"])
            if isinstance(base_entry, Mapping) and base_entry.get("avg_score") is not None
            else None
        )
        base_pr = (
            float(base_entry["pass_rate"])
            if isinstance(base_entry, Mapping) and base_entry.get("pass_rate") is not None
            else None
        )
        delta_avg = (
            round(avg_score - base_avg, 4)
            if avg_score is not None and base_avg is not None
            else None
        )
        delta_pr = round(pass_rate - base_pr, 4) if base_pr is not None else None

        dim_summary[code] = {
            "dimension_id": dim_id,
            "dimension_code": code,
            "dimension_name": dim_name,
            "version": ver,
            "evaluator_kind": eval_kind,
            "avg_score": avg_score,
            "pass_rate": pass_rate,
            "passed_cases": passed_cnt,
            "evaluated_cases": eval_cnt,
            "threshold": float(binding.get("threshold", 0.75)),
            "weight": float(binding.get("weight", 1.0)),
            "is_blocker": bool(binding.get("is_blocker", False)),
            "histogram_counts": hist,
            "unbatched_question_count": unbatched_q,
            "baseline_avg_score": base_avg,
            "delta_avg_score": delta_avg,
            "baseline_pass_rate": base_pr,
            "delta_pass_rate": delta_pr,
        }

    reg_summary: dict[str, Any] | None = None
    if baseline_run is not None and enable_semantic_regression:
        counts: dict[str, int] = {tag: 0 for tag in SEMANTIC_REGRESSION_TAGS}
        for out in outcomes:
            if out.exec_status == "SUCCESS" and out.eval_result is not None:
                rtag = out.eval_result.regression_tag
                if rtag in counts:
                    counts[rtag] += 1
        counts["total_compared"] = sum(counts[t] for t in SEMANTIC_REGRESSION_TAGS)
        reg_summary = counts

    return dim_summary, reg_summary


async def execute_eval_run(
    run_id: int,
    *,
    session_factory: sessionmaker[Session],
    settings: Settings | None = None,
    sut_transport: httpx.AsyncBaseTransport | None = None,
    judge_transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    cfg = settings or get_settings()
    cancel_event = _cancel_events.setdefault(run_id, asyncio.Event())

    try:
        with session_factory() as db:
            run = db.scalar(select(EvalRun).where(EvalRun.id == run_id, EvalRun.is_deleted == 0))
            if run is None or run.status == "CANCELLED":
                return
            suite = db.scalar(select(Suite).where(Suite.id == run.suite_id))
            adapter = db.scalar(select(SutAdapter).where(SutAdapter.id == run.sut_adapter_id))
            provider = db.scalar(
                select(DecisionProvider).where(DecisionProvider.id == run.provider_id)
            )
            if suite is None or adapter is None or provider is None:
                run.status = "FAILED"
                run.error_message = "套件、适配器或判别渠道配置缺失"
                run.finished_at = utcnow_naive()
                db.commit()
                return

            dim_map: dict[tuple[str, int], Dimension] = {}
            for b in suite.dimensions_json or []:
                d_code = str(b["dimension_code"])
                d_ver = int(b["version"])
                d_row = db.scalar(
                    select(Dimension).where(
                        Dimension.dimension_code == d_code,
                        Dimension.version == d_ver,
                        Dimension.is_deleted == 0,
                    )
                )
                if d_row is None:
                    run.status = "FAILED"
                    run.error_message = f"套件绑定的评价维度不存在: {d_code}@v{d_ver}"
                    run.finished_at = utcnow_naive()
                    db.commit()
                    return
                dim_map[(d_code, d_ver)] = d_row

            cases = _load_matching_approved_cases(
                db,
                dataset_id=run.dataset_id,
                dataset_split_filter=run.dataset_split_filter,
            )

            baseline_run: EvalRun | None = None
            baseline_outputs_by_case_id: dict[int, str] = {}
            if run.baseline_run_id is not None:
                baseline_run = db.scalar(
                    select(EvalRun).where(
                        EvalRun.id == run.baseline_run_id,
                        EvalRun.is_deleted == 0,
                    )
                )
                if baseline_run is not None and bool(suite.enable_semantic_regression):
                    b_rows = db.execute(
                        select(EvalRunCase.case_id, EvalRunCase.actual_output).where(
                            EvalRunCase.run_id == baseline_run.id,
                            EvalRunCase.exec_status == "SUCCESS",
                            EvalRunCase.is_deleted == 0,
                        )
                    ).all()
                    for b_case_id, b_out in b_rows:
                        if b_out is not None:
                            baseline_outputs_by_case_id[int(b_case_id)] = str(b_out)

            run.status = "RUNNING"
            run.total_cases = len(cases)
            run.started_at = utcnow_naive()
            db.commit()

            pinned_endpoint_url = run.pinned_endpoint_url
            pinned_model_name = run.pinned_model_name
            sut_fingerprint = dict(run.sut_fingerprint_json or {})
            cost_per_1m = float(provider.cost_per_1m_input_tokens or 0.0)
            adapter_cfg = SutAdapterConfig.from_adapter(adapter)
            concurrency = max(
                1,
                min(
                    int(adapter.max_concurrency or 4),
                    int(provider.max_concurrency or 8),
                ),
            )

        run_overrides = _extract_run_overrides(sut_fingerprint)
        enable_reg = bool(suite.enable_semantic_regression) and baseline_run is not None
        sem = asyncio.Semaphore(concurrency)
        db_lock = asyncio.Lock()
        outcomes: list[_CaseExecutionOutcome] = []

        async def _process_one_case(eval_case: EvalCase) -> None:
            if cancel_event.is_set():
                return
            async with sem:
                if cancel_event.is_set():
                    return

                try:
                    sut_res = await execute_sut_adapter(
                        adapter_cfg,
                        eval_case,
                        run_overrides=run_overrides,
                        transport=sut_transport,
                    )
                except SutExecutionError as exc:
                    outcome = _CaseExecutionOutcome(
                        case_id=eval_case.id,
                        exec_status="SUT_ERROR",
                        error_detail=exc.message[:1000],
                    )
                except Exception as exc:
                    outcome = _CaseExecutionOutcome(
                        case_id=eval_case.id,
                        exec_status="SUT_ERROR",
                        error_detail=f"被测系统执行异常: {exc}"[:1000],
                    )
                else:
                    baseline_out = (
                        baseline_outputs_by_case_id.get(eval_case.id)
                        if enable_reg
                        else None
                    )
                    try:
                        eval_res = await evaluate_case_dimensions(
                            case=eval_case,
                            dimensions=dim_map,
                            suite=suite,
                            sut_output=sut_res,
                            baseline_output=baseline_out,
                            enable_semantic_regression=enable_reg,
                            provider=provider,
                            pinned_endpoint_url=pinned_endpoint_url,
                            pinned_model_name=pinned_model_name,
                            settings=cfg,
                            transport=judge_transport,
                        )
                        outcome = _CaseExecutionOutcome(
                            case_id=eval_case.id,
                            exec_status="SUCCESS",
                            actual_output=sut_res.actual_output,
                            actual_references=list(sut_res.actual_references),
                            actual_structured=dict(sut_res.actual_structured),
                            sut_trace=sut_res.sut_trace,
                            sut_latency_ms=sut_res.sut_latency_ms,
                            ttft_ms=sut_res.ttft_ms,
                            eval_result=eval_res,
                        )
                    except (SystemOneUpstreamError, AppError, ValueError) as exc:
                        outcome = _CaseExecutionOutcome(
                            case_id=eval_case.id,
                            exec_status="JUDGE_ERROR",
                            actual_output=sut_res.actual_output,
                            actual_references=list(sut_res.actual_references),
                            actual_structured=dict(sut_res.actual_structured),
                            sut_trace=sut_res.sut_trace,
                            sut_latency_ms=sut_res.sut_latency_ms,
                            ttft_ms=sut_res.ttft_ms,
                            error_detail=str(exc)[:1000],
                        )
                    except Exception as exc:
                        outcome = _CaseExecutionOutcome(
                            case_id=eval_case.id,
                            exec_status="JUDGE_ERROR",
                            actual_output=sut_res.actual_output,
                            actual_references=list(sut_res.actual_references),
                            actual_structured=dict(sut_res.actual_structured),
                            sut_trace=sut_res.sut_trace,
                            sut_latency_ms=sut_res.sut_latency_ms,
                            ttft_ms=sut_res.ttft_ms,
                            error_detail=f"判别内核异常: {exc}"[:1000],
                        )

                async with db_lock:
                    if cancel_event.is_set():
                        return
                    with session_factory() as step_db:
                        cur_run = step_db.scalar(select(EvalRun).where(EvalRun.id == run_id))
                        if cur_run is None or cur_run.status == "CANCELLED":
                            cancel_event.set()
                            return

                        ev_res = outcome.eval_result
                        rc_row = EvalRunCase(
                            run_id=run_id,
                            case_id=outcome.case_id,
                            exec_status=outcome.exec_status,
                            actual_output=outcome.actual_output,
                            actual_references_json=outcome.actual_references,
                            actual_structured_json=outcome.actual_structured,
                            sut_trace_json=outcome.sut_trace,
                            sut_latency_ms=outcome.sut_latency_ms,
                            ttft_ms=outcome.ttft_ms,
                            overall_score=ev_res.overall_score if ev_res is not None else None,
                            overall_passed=(
                                (1 if ev_res.overall_passed else 0)
                                if ev_res is not None
                                else None
                            ),
                            min_confidence=ev_res.min_confidence if ev_res is not None else None,
                            needs_human_review=(
                                (1 if ev_res.needs_human_review else 0)
                                if ev_res is not None
                                else 0
                            ),
                            regression_tag=ev_res.regression_tag if ev_res is not None else None,
                            regression_confidence=(
                                ev_res.regression_confidence if ev_res is not None else None
                            ),
                            error_detail=outcome.error_detail,
                        )
                        step_db.add(rc_row)
                        step_db.flush()

                        if ev_res is not None:
                            for ledger in ev_res.ledger_entries:
                                step_db.add(
                                    ProbabilityLedger(
                                        **ledger.to_orm_kwargs(
                                            run_id=run_id,
                                            run_case_id=rc_row.id,
                                        )
                                    )
                                )
                            if ev_res.min_confidence is not None:
                                case_row = step_db.get(EvalCase, outcome.case_id)
                                if case_row is not None and case_row.is_deleted == 0:
                                    case_row.uncertainty_score = round(
                                        max(0.0, min(1.0, 1.0 - float(ev_res.min_confidence))),
                                        4,
                                    )
                                    case_row.update_time = utcnow_naive()

                        cur_run.completed_cases = int(cur_run.completed_cases or 0) + 1
                        if outcome.exec_status == "SUCCESS" and ev_res is not None:
                            if ev_res.overall_passed:
                                cur_run.passed_cases = int(cur_run.passed_cases or 0) + 1
                            else:
                                cur_run.failed_cases = int(cur_run.failed_cases or 0) + 1
                            if ev_res.needs_human_review:
                                cur_run.uncertain_cases = int(cur_run.uncertain_cases or 0) + 1
                            cur_run.total_decision_calls = (
                                int(cur_run.total_decision_calls or 0) + ev_res.decision_calls
                            )
                            cur_run.total_input_tokens = (
                                int(cur_run.total_input_tokens or 0) + ev_res.total_input_tokens
                            )
                        else:
                            cur_run.error_cases = int(cur_run.error_cases or 0) + 1

                        step_db.commit()
                        outcomes.append(outcome)

        await asyncio.gather(*(_process_one_case(c) for c in cases))

        outcomes.sort(key=lambda o: o.case_id)
        success_outcomes = [
            o for o in outcomes if o.exec_status == "SUCCESS" and o.eval_result is not None
        ]
        passed_cnt = sum(
            1 for o in success_outcomes if o.eval_result and o.eval_result.overall_passed
        )
        failed_cnt = len(success_outcomes) - passed_cnt
        error_cnt = sum(1 for o in outcomes if o.exec_status != "SUCCESS")
        uncertain_cnt = sum(
            1 for o in success_outcomes if o.eval_result and o.eval_result.needs_human_review
        )
        decision_calls = sum(
            o.eval_result.decision_calls for o in success_outcomes if o.eval_result
        )
        input_tokens = sum(
            o.eval_result.total_input_tokens for o in success_outcomes if o.eval_result
        )
        total_cost = round((input_tokens / 1_000_000.0) * cost_per_1m, 6)

        if success_outcomes:
            pass_rate: float | None = round(passed_cnt / len(success_outcomes), 4)
            weighted_avg: float | None = round(
                sum(o.eval_result.overall_score for o in success_outcomes if o.eval_result)
                / len(success_outcomes),
                4,
            )
        else:
            pass_rate = 0.0
            weighted_avg = 0.0

        sut_latencies = sorted(
            int(o.sut_latency_ms) for o in outcomes if o.sut_latency_ms is not None
        )
        ttfts = sorted(int(o.ttft_ms) for o in outcomes if o.ttft_ms is not None)

        dim_summary, reg_summary = _build_dimension_and_regression_summaries(
            suite=suite,
            dimensions_map=dim_map,
            outcomes=outcomes,
            baseline_run=baseline_run,
            enable_semantic_regression=enable_reg,
        )

        with session_factory() as final_db:
            final_run = final_db.scalar(select(EvalRun).where(EvalRun.id == run_id))
            if final_run is None:
                return
            final_run.completed_cases = len(outcomes)
            final_run.passed_cases = passed_cnt
            final_run.failed_cases = failed_cnt
            final_run.error_cases = error_cnt
            final_run.uncertain_cases = uncertain_cnt
            final_run.pass_rate = pass_rate
            final_run.weighted_avg_score = weighted_avg
            final_run.p50_sut_latency_ms = _percentile_int(sut_latencies, 0.50)
            final_run.p95_sut_latency_ms = _percentile_int(sut_latencies, 0.95)
            final_run.p50_ttft_ms = _percentile_int(ttfts, 0.50)
            final_run.total_decision_calls = decision_calls
            final_run.total_input_tokens = input_tokens
            final_run.total_eval_cost = total_cost
            final_run.dimension_summary_json = dim_summary
            final_run.regression_summary_json = reg_summary
            final_run.finished_at = utcnow_naive()
            if final_run.status != "CANCELLED" and not cancel_event.is_set():
                final_run.status = "COMPLETED"
            else:
                final_run.status = "CANCELLED"
            final_db.commit()
    except Exception as exc:
        with session_factory() as err_db:
            err_run = err_db.scalar(select(EvalRun).where(EvalRun.id == run_id))
            if err_run is not None and err_run.status not in ("COMPLETED", "CANCELLED"):
                err_run.status = "FAILED"
                err_run.error_message = str(exc)[:1000]
                err_run.finished_at = utcnow_naive()
                err_db.commit()
    finally:
        _cancel_events.pop(run_id, None)


async def create_and_dispatch_run(
    db: Session,
    payload: RunCreateRequest,
    *,
    triggered_by: str | None = None,
    settings: Settings | None = None,
    sut_transport: httpx.AsyncBaseTransport | None = None,
    judge_transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    suite = _resolve_suite(db, payload)
    dataset = db.scalar(
        select(Dataset).where(
            Dataset.id == payload.dataset_id,
            Dataset.is_deleted == 0,
        )
    )
    if dataset is None:
        raise AppError(f"测试集不存在: id={payload.dataset_id}", status_code=404, code=404)
    if dataset.eval_level != suite.eval_level:
        raise AppError(
            f"测试集层级({dataset.eval_level})与套件层级({suite.eval_level})不匹配",
            status_code=400,
            code=400,
        )

    adapter = _resolve_adapter(db, payload, suite)
    provider = _resolve_provider(db, payload, suite)
    baseline_run = _resolve_baseline_run(db, payload)

    split_filter = payload.dataset_split_filter.strip().upper()
    approved_cases = _load_matching_approved_cases(
        db,
        dataset_id=dataset.id,
        dataset_split_filter=split_filter,
    )
    if not approved_cases:
        raise AppError(
            f"所选测试集分集({split_filter})下暂无已审核生效(APPROVED)的用例，无法发起实验批次",
            status_code=400,
            code=400,
        )

    pinned_model = (
        (payload.model_override or "").strip()
        or (suite.default_model_override or "").strip()
        or provider.default_model.strip()
    )

    fingerprint = dict(payload.sut_fingerprint_json or payload.sut_fingerprint or {})
    if payload.run_overrides is not None:
        cfg_ov = dict(fingerprint.get("config_overrides") or {})
        cfg_ov.update(payload.run_overrides)
        fingerprint["config_overrides"] = cfg_ov

    now = utcnow_naive()
    run_no = _generate_run_no(db, now)

    run_row = EvalRun(
        run_no=run_no,
        run_name=payload.run_name.strip(),
        suite_id=suite.id,
        dataset_id=dataset.id,
        dataset_split_filter=split_filter,
        sut_adapter_id=adapter.id,
        sut_fingerprint_json=fingerprint,
        provider_id=provider.id,
        pinned_provider_kind=provider.provider_kind,
        pinned_endpoint_url=provider.endpoint_url,
        pinned_model_name=pinned_model,
        baseline_run_id=baseline_run.id if baseline_run is not None else None,
        status="PENDING",
        total_cases=len(approved_cases),
        completed_cases=0,
        passed_cases=0,
        failed_cases=0,
        error_cases=0,
        uncertain_cases=0,
        triggered_by=triggered_by,
        create_time=now,
        update_time=now,
    )
    db.add(run_row)
    db.commit()
    db.refresh(run_row)

    bind = db.get_bind()
    assert isinstance(bind, Engine)
    session_factory = build_session_factory(bind)

    if payload.run_sync:
        await execute_eval_run(
            run_row.id,
            session_factory=session_factory,
            settings=settings,
            sut_transport=sut_transport,
            judge_transport=judge_transport,
        )
        db.expire_all()
        refreshed = get_run_by_no_or_404(db, run_no)
        return serialize_run_detail(db, refreshed)

    _cancel_events[run_row.id] = asyncio.Event()
    task = asyncio.create_task(
        execute_eval_run(
            run_row.id,
            session_factory=session_factory,
            settings=settings,
            sut_transport=sut_transport,
            judge_transport=judge_transport,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return serialize_run_detail(db, run_row)


def cancel_eval_run(db: Session, run_no: str) -> dict[str, Any]:
    run = get_run_by_no_or_404(db, run_no)
    if run.status in ("COMPLETED", "FAILED", "CANCELLED"):
        return serialize_run_detail(db, run)
    ev = _cancel_events.get(run.id)
    if ev is not None:
        ev.set()
    run.status = "CANCELLED"
    run.finished_at = utcnow_naive()
    db.commit()
    db.refresh(run)
    return serialize_run_detail(db, run)


def list_eval_runs(
    db: Session,
    *,
    suite_code: str | None = None,
    suite_id: int | None = None,
    dataset_id: int | None = None,
    dataset_split_filter: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    filters = [EvalRun.is_deleted == 0]
    if suite_id is not None:
        filters.append(EvalRun.suite_id == suite_id)
    elif suite_code and suite_code.strip():
        suite_row = db.scalar(
            select(Suite.id).where(
                Suite.suite_code == suite_code.strip(),
                Suite.is_deleted == 0,
            )
        )
        if suite_row is None:
            return {"items": [], "total": 0, "page": page, "page_size": page_size}
        filters.append(EvalRun.suite_id == int(suite_row))

    if dataset_id is not None:
        filters.append(EvalRun.dataset_id == dataset_id)
    if dataset_split_filter and dataset_split_filter.strip():
        filters.append(EvalRun.dataset_split_filter == dataset_split_filter.strip().upper())
    if status and status.strip():
        filters.append(EvalRun.status == status.strip().upper())

    total = int(db.scalar(select(func.count(EvalRun.id)).where(*filters)) or 0)
    offset_val = max(0, (page - 1) * page_size)
    rows = list(
        db.scalars(
            select(EvalRun)
            .where(*filters)
            .order_by(EvalRun.id.desc())
            .offset(offset_val)
            .limit(page_size)
        ).all()
    )

    if not rows:
        return {"items": [], "total": total, "page": page, "page_size": page_size}

    suite_ids = {r.suite_id for r in rows}
    dataset_ids = {r.dataset_id for r in rows}
    adapter_ids = {r.sut_adapter_id for r in rows}
    provider_ids = {r.provider_id for r in rows}
    baseline_ids = {r.baseline_run_id for r in rows if r.baseline_run_id is not None}

    suites_by_id = {
        s.id: s for s in db.scalars(select(Suite).where(Suite.id.in_(suite_ids))).all()
    }
    datasets_by_id = {
        d.id: d for d in db.scalars(select(Dataset).where(Dataset.id.in_(dataset_ids))).all()
    }
    adapters_by_id = {
        a.id: a for a in db.scalars(select(SutAdapter).where(SutAdapter.id.in_(adapter_ids))).all()
    }
    providers_by_id = {
        p.id: p
        for p in db.scalars(
            select(DecisionProvider).where(DecisionProvider.id.in_(provider_ids))
        ).all()
    }
    baselines_by_id = (
        {
            b.id: b
            for b in db.scalars(select(EvalRun).where(EvalRun.id.in_(baseline_ids))).all()
        }
        if baseline_ids
        else {}
    )

    items = [
        serialize_run_list_item(
            r,
            suite=suites_by_id.get(r.suite_id),
            dataset=datasets_by_id.get(r.dataset_id),
            adapter=adapters_by_id.get(r.sut_adapter_id),
            provider=providers_by_id.get(r.provider_id),
            baseline_run=baselines_by_id.get(r.baseline_run_id) if r.baseline_run_id else None,
        )
        for r in rows
    ]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
