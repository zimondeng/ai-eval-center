from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.db.models import DecisionProvider
from app.kernel.batch_compiler import (
    SEMANTIC_REGRESSION_QUESTION_KEY,
    StateBatchCompiler,
    build_semantic_regression_question,
    build_semantic_regression_state,
    parse_semantic_regression_answer,
)
from app.kernel.deterministic import evaluate_deterministic_dimension
from app.kernel.scoring import (
    _read_field,
    evaluate_suite_gate,
    synthesize_dimension_result,
)
from app.kernel.system_one_client import SystemOneBatchResult, SystemOneClient


@dataclass(slots=True)
class _BoundDimensionSpec:
    dimension: Any
    dimension_id: int
    dimension_code: str
    version: int
    evaluator_kind: str
    threshold: float
    weight: float
    is_blocker: bool
    strict_mode: bool


def _resolve_bound_dimensions(
    dimensions: Sequence[Any] | Mapping[tuple[str, int], Any],
    suite: Any | None = None,
) -> list[_BoundDimensionSpec]:
    dim_lookup: dict[tuple[str, int], Any] = {}
    ordered_raw: list[Any] = []

    if isinstance(dimensions, Mapping):
        for k, d in dimensions.items():
            if isinstance(k, tuple) and len(k) == 2:
                dim_lookup[(str(k[0]), int(k[1]))] = d
            ordered_raw.append(d)
    else:
        for d in dimensions:
            code = str(_read_field(d, "dimension_code")).strip()
            ver = int(_read_field(d, "version", "dimension_version", default=1))
            dim_lookup[(code, ver)] = d
            ordered_raw.append(d)

    suite_bindings = (
        _read_field(suite, "dimensions_json", default=None)
        if suite is not None
        else None
    )

    resolved: list[_BoundDimensionSpec] = []
    if isinstance(suite_bindings, Sequence) and not isinstance(suite_bindings, (str, bytes)):
        for b in suite_bindings:
            code = str(_read_field(b, "dimension_code")).strip()
            ver = int(_read_field(b, "version", default=1))
            dim_obj = dim_lookup.get((code, ver))
            if dim_obj is None:
                raise ValueError(f"未找到套件绑定的评价维度定义: {code}@v{ver}")
            dim_id = int(_read_field(dim_obj, "id", "dimension_id", default=0) or 0)
            eval_kind = str(_read_field(dim_obj, "evaluator_kind", default="SYSTEM_ONE")).upper()
            threshold = float(
                _read_field(
                    b,
                    "threshold",
                    default=_read_field(dim_obj, "default_threshold", default=0.75),
                )
            )
            weight = float(
                _read_field(
                    b,
                    "weight",
                    default=_read_field(dim_obj, "default_weight", default=1.0),
                )
            )
            is_blocker = bool(_read_field(b, "is_blocker", default=False))
            strict_mode = bool(_read_field(dim_obj, "strict_mode", default=False))
            resolved.append(
                _BoundDimensionSpec(
                    dimension=dim_obj,
                    dimension_id=dim_id,
                    dimension_code=code,
                    version=ver,
                    evaluator_kind=eval_kind,
                    threshold=threshold,
                    weight=weight,
                    is_blocker=is_blocker,
                    strict_mode=strict_mode,
                )
            )
        return resolved

    for d in ordered_raw:
        code = str(_read_field(d, "dimension_code")).strip()
        ver = int(_read_field(d, "version", "dimension_version", default=1))
        dim_id = int(_read_field(d, "id", "dimension_id", default=0) or 0)
        eval_kind = str(_read_field(d, "evaluator_kind", default="SYSTEM_ONE")).upper()
        threshold = float(
            _read_field(
                d,
                "applied_threshold",
                "threshold",
                "default_threshold",
                default=0.75,
            )
        )
        weight = float(
            _read_field(
                d,
                "applied_weight",
                "weight",
                "default_weight",
                default=1.0,
            )
        )
        is_blocker = bool(_read_field(d, "is_blocker", default=False))
        strict_mode = bool(_read_field(d, "strict_mode", default=False))
        resolved.append(
            _BoundDimensionSpec(
                dimension=d,
                dimension_id=dim_id,
                dimension_code=code,
                version=ver,
                evaluator_kind=eval_kind,
                threshold=threshold,
                weight=weight,
                is_blocker=is_blocker,
                strict_mode=strict_mode,
            )
        )
    return resolved


@dataclass(slots=True)
class DimensionLedgerEntry:
    dimension_id: int
    dimension_code: str
    dimension_version: int
    evaluator_kind: str
    raw_dimension_score: float | None
    applied_threshold: float
    applied_weight: float
    is_blocker: bool
    passed: bool
    min_question_confidence: float | None
    deterministic_reason: str
    raw_answers_json: dict[str, Any]
    waf_fallback_used: bool = False
    upstream_request_id: str | None = None
    eval_latency_ms: int = 0
    input_tokens: int = 0

    def to_orm_kwargs(self, *, run_id: int, run_case_id: int) -> dict[str, Any]:
        return {
            "run_id": int(run_id),
            "run_case_id": int(run_case_id),
            "dimension_id": int(self.dimension_id),
            "dimension_code": self.dimension_code,
            "dimension_version": int(self.dimension_version),
            "evaluator_kind": self.evaluator_kind,
            "raw_dimension_score": self.raw_dimension_score,
            "applied_threshold": float(self.applied_threshold),
            "applied_weight": float(self.applied_weight),
            "is_blocker": 1 if self.is_blocker else 0,
            "passed": 1 if self.passed else 0,
            "min_question_confidence": self.min_question_confidence,
            "deterministic_reason": self.deterministic_reason,
            "raw_answers_json": self.raw_answers_json,
            "waf_fallback_used": 1 if self.waf_fallback_used else 0,
            "upstream_request_id": self.upstream_request_id,
            "eval_latency_ms": int(self.eval_latency_ms),
            "input_tokens": int(self.input_tokens),
        }


@dataclass(slots=True)
class CaseEvaluationResult:
    overall_score: float
    overall_passed: bool
    min_confidence: float | None
    needs_human_review: bool
    ledger_entries: list[DimensionLedgerEntry]
    decision_calls: int
    total_input_tokens: int
    total_output_tokens: int
    waf_fallback_used: bool
    regression_tag: str | None = None
    regression_confidence: float | None = None
    regression_probabilities: dict[str, float] | None = None


async def evaluate_case_dimensions(
    *,
    case: Any,
    dimensions: Sequence[Any] | Mapping[tuple[str, int], Any],
    suite: Any | None = None,
    sut_output: Any | None = None,
    actual_output: str | None = None,
    actual_references: Sequence[str] | None = None,
    actual_structured_json: Mapping[str, Any] | None = None,
    sut_latency_ms: int | float | None = None,
    ttft_ms: int | float | None = None,
    pass_policy: str | None = None,
    suite_pass_threshold: float | None = None,
    confidence_review_floor: float | None = None,
    baseline_output: str | None = None,
    enable_semantic_regression: bool | None = None,
    client: SystemOneClient | None = None,
    provider: DecisionProvider | None = None,
    pinned_endpoint_url: str | None = None,
    pinned_model_name: str | None = None,
    max_questions_per_call: int | None = None,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CaseEvaluationResult:
    """单条用例全维度判别编排器：融合同 state 合批 System One 请求、确定性规则、套件门禁与四分类语义回归。"""
    bound_specs = _resolve_bound_dimensions(dimensions, suite=suite)

    resolved_actual_output = (
        actual_output
        if actual_output is not None
        else _read_field(sut_output, "actual_output", default=None)
    )
    resolved_actual_refs = (
        actual_references
        if actual_references is not None
        else _read_field(
            sut_output, "actual_references", "actual_references_json", "references", default=None
        )
    )
    resolved_actual_structured = (
        actual_structured_json
        if actual_structured_json is not None
        else _read_field(
            sut_output, "actual_structured_json", "actual_structured", default=None
        )
    )
    resolved_sut_latency = (
        sut_latency_ms
        if sut_latency_ms is not None
        else _read_field(sut_output, "sut_latency_ms", default=None)
    )
    resolved_ttft = (
        ttft_ms
        if ttft_ms is not None
        else _read_field(sut_output, "ttft_ms", default=None)
    )

    case_refs = _read_field(case, "reference_contexts_json", "references", default=None)
    effective_refs: list[str]
    if resolved_actual_refs is not None and len(resolved_actual_refs) > 0:
        effective_refs = [str(r) for r in resolved_actual_refs]
    elif isinstance(case_refs, Sequence) and not isinstance(case_refs, (str, bytes)):
        effective_refs = [str(r) for r in case_refs]
    else:
        effective_refs = []

    expected_output_val = _read_field(case, "expected_output", default=None)
    expected_structured_val = _read_field(
        case, "expected_structured_json", "expected_structured", default=None
    )

    eff_policy = (
        pass_policy
        if pass_policy is not None
        else str(
            _read_field(suite, "pass_policy", default="ALL_BLOCKERS_AND_WEIGHTED")
            if suite is not None
            else "ALL_BLOCKERS_AND_WEIGHTED"
        )
    )
    eff_suite_threshold = float(
        suite_pass_threshold
        if suite_pass_threshold is not None
        else (
            _read_field(suite, "suite_pass_threshold", default=0.75)
            if suite is not None
            else 0.75
        )
    )
    eff_review_floor = float(
        confidence_review_floor
        if confidence_review_floor is not None
        else (
            _read_field(suite, "confidence_review_floor", default=0.50)
            if suite is not None
            else 0.50
        )
    )
    eff_enable_reg = (
        bool(enable_semantic_regression)
        if enable_semantic_regression is not None
        else bool(
            _read_field(suite, "enable_semantic_regression", default=False)
            if suite is not None
            else False
        )
    )

    has_s1_dims = any(s.evaluator_kind == "SYSTEM_ONE" for s in bound_specs)
    needs_reg_call = eff_enable_reg and baseline_output is not None

    s1_client = client
    if s1_client is None and (has_s1_dims or needs_reg_call):
        if provider is None:
            raise ValueError("存在 SYSTEM_ONE 维度或语义回归检查时必须提供 client 或 provider")
        s1_client = SystemOneClient(
            provider,
            pinned_endpoint_url=pinned_endpoint_url,
            pinned_model_name=pinned_model_name,
            settings=settings,
            transport=transport,
        )

    eff_max_q = (
        int(max_questions_per_call)
        if max_questions_per_call is not None
        else int(
            (s1_client.provider.max_questions_per_call if s1_client is not None else 64) or 64
        )
    )

    decision_calls = 0
    total_input_tokens = 0
    total_output_tokens = 0
    any_waf_fallback = False

    unpacked_s1_answers: dict[tuple[str, int], dict[str, Any]] = {}
    dim_call_meta: dict[tuple[str, int], dict[str, Any]] = {}

    if has_s1_dims:
        assert s1_client is not None
        compiler = StateBatchCompiler(max_questions_per_call=eff_max_q)
        plan = compiler.compile(
            dimensions=[s.dimension for s in bound_specs if s.evaluator_kind == "SYSTEM_ONE"],
            case=case,
            actual_output=resolved_actual_output,
            actual_references=resolved_actual_refs,
        )
        batch_results: list[SystemOneBatchResult] = []
        for batch in plan.batches:
            b_res = await s1_client.execute_batch(batch)
            batch_results.append(b_res)
            decision_calls += 1
            total_input_tokens += b_res.input_tokens
            total_output_tokens += b_res.output_tokens
            if b_res.waf_fallback_used:
                any_waf_fallback = True

            total_q = max(1, len(batch.question_refs))
            counts_by_dim: dict[tuple[str, int], int] = {}
            for q_ref in batch.question_refs.values():
                dk = (q_ref.dimension_code, q_ref.dimension_version)
                counts_by_dim[dk] = counts_by_dim.get(dk, 0) + 1

            for dk, q_cnt in counts_by_dim.items():
                ratio = q_cnt / float(total_q)
                meta = dim_call_meta.setdefault(
                    dk,
                    {
                        "waf_fallback_used": False,
                        "upstream_request_id": None,
                        "eval_latency_ms": 0,
                        "input_tokens": 0,
                    },
                )
                if b_res.waf_fallback_used:
                    meta["waf_fallback_used"] = True
                if meta["upstream_request_id"] is None and b_res.upstream_request_id:
                    meta["upstream_request_id"] = b_res.upstream_request_id
                meta["eval_latency_ms"] += int(round(b_res.latency_ms * ratio))
                meta["input_tokens"] += int(round(b_res.input_tokens * ratio))

        unpacked_s1_answers = plan.unpack_answers([r.answers for r in batch_results])

    ledger_entries: list[DimensionLedgerEntry] = []
    for spec in bound_specs:
        dk = (spec.dimension_code, spec.version)
        if spec.evaluator_kind == "SYSTEM_ONE":
            q_specs = (
                _read_field(spec.dimension, "questions_json", "questions", default=None) or []
            )
            dim_ans = unpacked_s1_answers.get(dk, {})
            synth = synthesize_dimension_result(
                questions_spec=q_specs,
                answers=dim_ans,
                threshold=spec.threshold,
                strict_mode=spec.strict_mode,
            )
            meta = dim_call_meta.get(dk, {})
            ledger_entries.append(
                DimensionLedgerEntry(
                    dimension_id=spec.dimension_id,
                    dimension_code=spec.dimension_code,
                    dimension_version=spec.version,
                    evaluator_kind="SYSTEM_ONE",
                    raw_dimension_score=synth.raw_dimension_score,
                    applied_threshold=spec.threshold,
                    applied_weight=spec.weight,
                    is_blocker=spec.is_blocker,
                    passed=synth.passed,
                    min_question_confidence=synth.min_question_confidence,
                    deterministic_reason=synth.deterministic_reason,
                    raw_answers_json=synth.raw_answers_json,
                    waf_fallback_used=bool(meta.get("waf_fallback_used", False)),
                    upstream_request_id=meta.get("upstream_request_id"),
                    eval_latency_ms=int(meta.get("eval_latency_ms", 0)),
                    input_tokens=int(meta.get("input_tokens", 0)),
                )
            )
        elif spec.evaluator_kind == "DETERMINISTIC":
            rule_cfg = _read_field(
                spec.dimension, "deterministic_rule_json", "deterministic_rule", default=None
            )
            if not isinstance(rule_cfg, Mapping):
                raise ValueError(
                    f"DETERMINISTIC 维度 {spec.dimension_code}@v{spec.version} 缺少规则配置"
                )
            det_res = evaluate_deterministic_dimension(
                rule_cfg,
                threshold=spec.threshold,
                actual_output=resolved_actual_output,
                expected_output=expected_output_val,
                references=effective_refs,
                actual_structured_json=resolved_actual_structured,
                expected_structured_json=expected_structured_val,
                sut_latency_ms=resolved_sut_latency,
                ttft_ms=resolved_ttft,
            )
            ledger_entries.append(
                DimensionLedgerEntry(
                    dimension_id=spec.dimension_id,
                    dimension_code=spec.dimension_code,
                    dimension_version=spec.version,
                    evaluator_kind="DETERMINISTIC",
                    raw_dimension_score=det_res.raw_dimension_score,
                    applied_threshold=spec.threshold,
                    applied_weight=spec.weight,
                    is_blocker=spec.is_blocker,
                    passed=det_res.passed,
                    min_question_confidence=None,
                    deterministic_reason=det_res.deterministic_reason,
                    raw_answers_json=det_res.raw_answers_json,
                    waf_fallback_used=False,
                    upstream_request_id=None,
                    eval_latency_ms=0,
                    input_tokens=0,
                )
            )
        else:
            raise ValueError(f"不支持的维度评估机制: {spec.evaluator_kind!r}")

    gate = evaluate_suite_gate(
        ledger_entries,
        pass_policy=eff_policy,
        suite_pass_threshold=eff_suite_threshold,
        confidence_review_floor=eff_review_floor,
    )

    reg_tag: str | None = None
    reg_conf: float | None = None
    reg_probs: dict[str, float] | None = None

    if needs_reg_call:
        assert s1_client is not None and baseline_output is not None
        input_txt = str(_read_field(case, "input_text", "input", default="") or "")
        reg_state = build_semantic_regression_state(
            input_text=input_txt,
            baseline_output=baseline_output,
            current_output=resolved_actual_output or "",
        )
        reg_batch_res = await s1_client.decide(
            state=reg_state,
            questions={SEMANTIC_REGRESSION_QUESTION_KEY: build_semantic_regression_question()},
        )
        decision_calls += 1
        total_input_tokens += reg_batch_res.input_tokens
        total_output_tokens += reg_batch_res.output_tokens
        if reg_batch_res.waf_fallback_used:
            any_waf_fallback = True
        reg_tag, reg_conf, reg_probs = parse_semantic_regression_answer(
            reg_batch_res.answers[SEMANTIC_REGRESSION_QUESTION_KEY]
        )

    return CaseEvaluationResult(
        overall_score=gate.overall_score,
        overall_passed=gate.overall_passed,
        min_confidence=gate.min_confidence,
        needs_human_review=gate.needs_human_review,
        ledger_entries=ledger_entries,
        decision_calls=decision_calls,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        waf_fallback_used=any_waf_fallback,
        regression_tag=reg_tag,
        regression_confidence=reg_conf,
        regression_probabilities=reg_probs,
    )
