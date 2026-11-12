from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common.errors import AppError
from app.db.models import DecisionProvider, Dimension, Suite, SutAdapter
from app.dimensions.schemas import (
    DimensionCreateRequest,
    SuiteCreateRequest,
    SuiteDimensionBinding,
    SuiteUpdateRequest,
)
from app.linter import lint_dimension

_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

VALID_CATEGORIES = frozenset(
    {
        "GROUNDING",
        "RELEVANCY",
        "ACCURACY",
        "ROUTING",
        "SYNTHESIS",
        "SAFETY",
        "PERFORMANCE",
    }
)
VALID_TURN_MODES = frozenset({"SINGLE_TURN", "MULTI_TURN", "BOTH"})
VALID_EVALUATOR_KINDS = frozenset({"SYSTEM_ONE", "DETERMINISTIC"})
VALID_DIMENSION_STATUSES = frozenset({"DRAFT", "ACTIVE", "DEPRECATED"})
VALID_EVAL_LEVELS = frozenset({"E2E", "COMPONENT"})
VALID_PASS_POLICIES = frozenset({"ALL_DIMENSIONS", "ALL_BLOCKERS_AND_WEIGHTED"})
VALID_SUITE_STATUSES = frozenset({"ACTIVE", "ARCHIVED"})
VALID_QUESTION_TYPES = frozenset({"noul", "score", "choice"})
VALID_DETERMINISTIC_RULES = frozenset(
    {
        "json_exact_or_in",
        "set_recall_precision",
        "numeric_fact_grounding",
        "latency_sla",
    }
)
VALID_NUMERIC_CHECK_TYPES = frozenset(
    {"phone", "amount", "percent", "percentage", "workdays"}
)
VALID_GROUNDING_SOURCES = frozenset({"references", "expected_output"})
VALID_LATENCY_FIELDS = frozenset({"sut_latency_ms", "ttft_ms"})
_LATENCY_BOUND_KEYS = (
    "max_ms",
    "max_latency_ms",
    "sla_ms",
    "limit_ms",
    "max_sut_latency_ms",
    "max_ttft_ms",
)


def _fmt_dt(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def serialize_dimension(row: Dimension) -> dict[str, Any]:
    return {
        "id": row.id,
        "uuid": row.uuid,
        "dimension_code": row.dimension_code,
        "version": int(row.version),
        "dimension_name": row.dimension_name,
        "category": row.category,
        "target_turn_mode": row.target_turn_mode,
        "evaluator_kind": row.evaluator_kind,
        "state_fields_json": row.state_fields_json,
        "questions_json": row.questions_json,
        "deterministic_rule_json": row.deterministic_rule_json,
        "default_threshold": float(row.default_threshold),
        "strict_mode": bool(row.strict_mode),
        "default_weight": float(row.default_weight),
        "lint_passed": bool(row.lint_passed),
        "lint_report_json": row.lint_report_json,
        "status": row.status,
        "description": row.description,
        "created_by": row.created_by,
        "create_time": _fmt_dt(row.create_time),
        "update_time": _fmt_dt(row.update_time),
    }


def list_dimensions(
    db: Session,
    *,
    category: str | None = None,
    status: str | None = None,
    evaluator_kind: str | None = None,
    latest_only: bool = False,
) -> list[dict[str, Any]]:
    stmt = select(Dimension).where(Dimension.is_deleted == 0)
    if category:
        stmt = stmt.where(Dimension.category == category)
    if status:
        stmt = stmt.where(Dimension.status == status)
    if evaluator_kind:
        stmt = stmt.where(Dimension.evaluator_kind == evaluator_kind)

    stmt = stmt.order_by(Dimension.dimension_code.asc(), Dimension.version.desc())
    rows = list(db.scalars(stmt).all())

    if latest_only:
        seen_codes: set[str] = set()
        deduped: list[Dimension] = []
        for row in rows:
            if row.dimension_code not in seen_codes:
                seen_codes.add(row.dimension_code)
                deduped.append(row)
        rows = deduped

    return [serialize_dimension(row) for row in rows]


def get_dimension_detail(db: Session, dimension_code: str) -> dict[str, Any]:
    stmt = (
        select(Dimension)
        .where(
            Dimension.dimension_code == dimension_code,
            Dimension.is_deleted == 0,
        )
        .order_by(Dimension.version.desc())
    )
    rows = list(db.scalars(stmt).all())
    if not rows:
        raise AppError(f"评价维度不存在: {dimension_code}", status_code=404)

    serialized = [serialize_dimension(r) for r in rows]
    return {
        "dimension_code": dimension_code,
        "latest_version": int(rows[0].version),
        "latest": serialized[0],
        "versions": serialized,
    }


def _validate_deterministic_rule(rule: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(rule, dict) or not rule:
        raise AppError("DETERMINISTIC 维度必须提供非空 deterministic_rule_json", status_code=400)

    rule_type = rule.get("rule_type")
    if not isinstance(rule_type, str) or rule_type not in VALID_DETERMINISTIC_RULES:
        raise AppError(
            f"不支持的确定性规则类型: {rule_type!r}，"
            f"允许值: {sorted(VALID_DETERMINISTIC_RULES)}",
            status_code=400,
        )

    if rule_type == "json_exact_or_in":
        actual_path = rule.get("actual_path")
        if not isinstance(actual_path, str) or not actual_path.strip():
            raise AppError("json_exact_or_in 规则必须指定非空 actual_path", status_code=400)
        expected_path = rule.get("expected_path")
        has_exp_path = isinstance(expected_path, str) and bool(expected_path.strip())
        has_exp_vals = isinstance(rule.get("expected_values"), list) and len(rule["expected_values"]) > 0
        has_exp_val = "expected_value" in rule and rule["expected_value"] is not None
        if not (has_exp_path or has_exp_vals or has_exp_val):
            raise AppError(
                "json_exact_or_in 规则必须指定 expected_path 或 expected_values",
                status_code=400,
            )

    elif rule_type == "set_recall_precision":
        for pkey in ("actual_path", "expected_path"):
            if pkey in rule:
                pval = rule[pkey]
                if not isinstance(pval, str) or not pval.strip():
                    raise AppError(
                        f"set_recall_precision 规则字段 {pkey} 必须为非空字符串",
                        status_code=400,
                    )
        if "metric" in rule and rule["metric"] is not None:
            metric = str(rule["metric"]).lower()
            if metric not in {"recall", "precision", "f1"}:
                raise AppError(
                    "set_recall_precision 的 metric 仅支持 recall / precision / f1",
                    status_code=400,
                )

    elif rule_type == "numeric_fact_grounding":
        if "check_types" in rule and rule["check_types"] is not None:
            ct = rule["check_types"]
            if (
                not isinstance(ct, list)
                or not ct
                or not all(isinstance(x, str) and x in VALID_NUMERIC_CHECK_TYPES for x in ct)
            ):
                raise AppError(
                    f"numeric_fact_grounding 的 check_types 非法，"
                    f"允许项: {sorted(VALID_NUMERIC_CHECK_TYPES)}",
                    status_code=400,
                )
        if "grounding_sources" in rule and rule["grounding_sources"] is not None:
            gs = rule["grounding_sources"]
            if (
                not isinstance(gs, list)
                or not gs
                or not all(isinstance(x, str) and x in VALID_GROUNDING_SOURCES for x in gs)
            ):
                raise AppError(
                    f"numeric_fact_grounding 的 grounding_sources 非法，"
                    f"允许项: {sorted(VALID_GROUNDING_SOURCES)}",
                    status_code=400,
                )

    elif rule_type == "latency_sla":
        for fkey in ("target_field", "metric", "field"):
            if fkey in rule and rule[fkey] is not None:
                if rule[fkey] not in VALID_LATENCY_FIELDS:
                    raise AppError(
                        f"latency_sla 的 {fkey} 仅支持 {sorted(VALID_LATENCY_FIELDS)}",
                        status_code=400,
                    )
        present_bounds = [k for k in _LATENCY_BOUND_KEYS if k in rule and rule[k] is not None]
        if not present_bounds:
            raise AppError(
                "latency_sla 规则必须提供至少一个正数耗时上限(如 max_ms)",
                status_code=400,
            )
        for bkey in present_bounds:
            bval = rule[bkey]
            if isinstance(bval, bool) or not isinstance(bval, (int, float)) or not math.isfinite(float(bval)) or float(bval) <= 0:
                raise AppError(
                    f"latency_sla 的 {bkey} 必须为大于 0 的数值",
                    status_code=400,
                )

    return dict(rule)


def _validate_system_one_questions(
    state_fields_raw: list[str] | None,
    questions_raw: list[dict[str, Any]] | None,
) -> tuple[list[str], list[dict[str, Any]]]:
    if not isinstance(state_fields_raw, list) or not state_fields_raw:
        raise AppError("SYSTEM_ONE 维度必须提供非空 state_fields_json 列表", status_code=400)

    cleaned_fields: list[str] = []
    for f in state_fields_raw:
        if not isinstance(f, str) or not f.strip():
            raise AppError("state_fields_json 包含空字段名", status_code=400)
        name = f.strip()
        if name not in cleaned_fields:
            cleaned_fields.append(name)

    if not isinstance(questions_raw, list) or not (1 <= len(questions_raw) <= 64):
        raise AppError("SYSTEM_ONE 维度 questions_json 题目数必须在 [1, 64] 区间内", status_code=400)

    seen_keys: set[str] = set()
    cleaned_questions: list[dict[str, Any]] = []
    for idx, q in enumerate(questions_raw):
        if not isinstance(q, dict):
            raise AppError(f"第 {idx + 1} 道原子题格式非法", status_code=400)
        q_key = str(q.get("key") or "").strip()
        if not _CODE_RE.match(q_key) or "__" in q_key:
            raise AppError(f"原子题 key 非法(不可含双下划线): {q_key!r}", status_code=400)
        if q_key in seen_keys:
            raise AppError(f"同一维度内原子题 key 重复: {q_key}", status_code=400)
        seen_keys.add(q_key)

        q_type = str(q.get("type") or "").strip().lower()
        if q_type not in VALID_QUESTION_TYPES:
            raise AppError(
                f"原子题 {q_key} 的 type 非法({q_type!r})，仅支持 noul / score / choice",
                status_code=400,
            )

        instructions = q.get("instructions")
        if not isinstance(instructions, str) or not instructions.strip():
            raise AppError(f"原子题 {q_key} 的 instructions 不能为空", status_code=400)

        weight = q.get("weight", 1.0)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(float(weight)) or float(weight) <= 0:
            raise AppError(f"原子题 {q_key} 的 weight 必须大于 0", status_code=400)

        item = dict(q)
        item["key"] = q_key
        item["type"] = q_type
        item["weight"] = float(weight)
        cleaned_questions.append(item)

    return cleaned_fields, cleaned_questions


def create_dimension_version(
    db: Session,
    payload: DimensionCreateRequest,
    *,
    created_by: str | None = None,
) -> dict[str, Any]:
    code = payload.dimension_code.strip()
    if not _CODE_RE.match(code) or "__" in code:
        raise AppError(f"dimension_code 格式不合法(不可含双下划线): {code!r}", status_code=400)

    name = payload.dimension_name.strip()
    if not name or len(name) > 128:
        raise AppError("dimension_name 不能为空且长度不超过 128", status_code=400)

    if payload.category not in VALID_CATEGORIES:
        raise AppError(f"不支持的评价维度分类: {payload.category!r}", status_code=400)
    if payload.target_turn_mode not in VALID_TURN_MODES:
        raise AppError(f"不支持的适用轮次: {payload.target_turn_mode!r}", status_code=400)
    if payload.evaluator_kind not in VALID_EVALUATOR_KINDS:
        raise AppError(f"不支持的评估机制: {payload.evaluator_kind!r}", status_code=400)
    if payload.status not in VALID_DIMENSION_STATUSES:
        raise AppError(f"不支持的维度状态: {payload.status!r}", status_code=400)

    if (
        not math.isfinite(float(payload.default_threshold))
        or not (0.0 <= float(payload.default_threshold) <= 1.0)
    ):
        raise AppError("default_threshold 必须位于 [0.0, 1.0] 区间内", status_code=400)
    if not math.isfinite(float(payload.default_weight)) or float(payload.default_weight) <= 0.0:
        raise AppError("default_weight 必须大于 0", status_code=400)

    state_fields_json: list[str] | None = None
    questions_json: list[dict[str, Any]] | None = None
    deterministic_rule_json: dict[str, Any] | None = None
    lint_passed_int = 1
    lint_report_dict: dict[str, Any] = {"passed": True, "issues": []}

    if payload.evaluator_kind == "SYSTEM_ONE":
        state_fields_json, questions_json = _validate_system_one_questions(
            payload.state_fields_json,
            payload.questions_json,
        )
        report = lint_dimension(
            state_fields=state_fields_json,
            questions=questions_json,
        )
        lint_passed_int = 1 if report.passed else 0
        lint_report_dict = report.model_dump()
        if payload.status == "ACTIVE" and not report.passed:
            raise AppError(
                "题干静态质检未通过(存在 ERROR)，禁止直接发布为 ACTIVE，请先修复或存为 DRAFT",
                status_code=400,
                data=lint_report_dict,
            )
    else:
        deterministic_rule_json = _validate_deterministic_rule(payload.deterministic_rule_json)

    max_ver = db.scalar(
        select(func.max(Dimension.version)).where(Dimension.dimension_code == code)
    )
    next_version = int(max_ver) + 1 if max_ver is not None else 1

    new_row = Dimension(
        dimension_code=code,
        version=next_version,
        dimension_name=name,
        category=payload.category,
        target_turn_mode=payload.target_turn_mode,
        evaluator_kind=payload.evaluator_kind,
        state_fields_json=state_fields_json,
        questions_json=questions_json,
        deterministic_rule_json=deterministic_rule_json,
        default_threshold=float(payload.default_threshold),
        strict_mode=1 if bool(payload.strict_mode) else 0,
        default_weight=float(payload.default_weight),
        lint_passed=lint_passed_int,
        lint_report_json=lint_report_dict,
        status=payload.status,
        description=payload.description,
        created_by=created_by,
        is_deleted=0,
    )
    db.add(new_row)
    db.commit()
    db.refresh(new_row)
    return serialize_dimension(new_row)


def serialize_suite(row: Suite) -> dict[str, Any]:
    normalized_dims = [
        {
            "dimension_code": str(item["dimension_code"]),
            "version": int(item["version"]),
            "weight": float(item["weight"]),
            "threshold": float(item["threshold"]),
            "is_blocker": bool(item["is_blocker"]),
        }
        for item in (row.dimensions_json or [])
    ]
    return {
        "id": row.id,
        "uuid": row.uuid,
        "suite_code": row.suite_code,
        "suite_name": row.suite_name,
        "eval_level": row.eval_level,
        "default_adapter_id": row.default_adapter_id,
        "default_provider_id": row.default_provider_id,
        "default_model_override": row.default_model_override,
        "dimensions_json": normalized_dims,
        "pass_policy": row.pass_policy,
        "suite_pass_threshold": float(row.suite_pass_threshold),
        "confidence_review_floor": float(row.confidence_review_floor),
        "enable_semantic_regression": bool(row.enable_semantic_regression),
        "status": row.status,
        "description": row.description,
        "created_by": row.created_by,
        "create_time": _fmt_dt(row.create_time),
        "update_time": _fmt_dt(row.update_time),
    }


def list_suites(
    db: Session,
    *,
    eval_level: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    stmt = select(Suite).where(Suite.is_deleted == 0)
    if eval_level:
        stmt = stmt.where(Suite.eval_level == eval_level)
    if status:
        stmt = stmt.where(Suite.status == status)
    stmt = stmt.order_by(Suite.id.asc())
    return [serialize_suite(row) for row in db.scalars(stmt).all()]


def get_suite_by_code(db: Session, suite_code: str) -> dict[str, Any]:
    row = db.scalar(
        select(Suite).where(
            Suite.suite_code == suite_code,
            Suite.is_deleted == 0,
        )
    )
    if row is None:
        raise AppError(f"评测套件不存在: {suite_code}", status_code=404)
    return serialize_suite(row)


def _validate_suite_fields(
    db: Session,
    *,
    suite_name: str,
    eval_level: str,
    default_adapter_id: int | None,
    default_provider_id: int | None,
    dimensions: list[SuiteDimensionBinding],
    pass_policy: str,
    suite_pass_threshold: float,
    confidence_review_floor: float,
    status: str,
) -> list[dict[str, Any]]:
    clean_name = suite_name.strip()
    if not clean_name or len(clean_name) > 128:
        raise AppError("suite_name 不能为空且长度不超过 128", status_code=400)

    if eval_level not in VALID_EVAL_LEVELS:
        raise AppError(f"不支持的评测层级: {eval_level!r}", status_code=400)
    if pass_policy not in VALID_PASS_POLICIES:
        raise AppError(f"不支持的门禁策略: {pass_policy!r}", status_code=400)
    if status not in VALID_SUITE_STATUSES:
        raise AppError(f"不支持的套件状态: {status!r}", status_code=400)

    if (
        not math.isfinite(float(suite_pass_threshold))
        or not (0.0 <= float(suite_pass_threshold) <= 1.0)
    ):
        raise AppError("suite_pass_threshold 必须位于 [0.0, 1.0] 区间内", status_code=400)
    if (
        not math.isfinite(float(confidence_review_floor))
        or not (0.0 <= float(confidence_review_floor) <= 1.0)
    ):
        raise AppError("confidence_review_floor 必须位于 [0.0, 1.0] 区间内", status_code=400)

    if default_adapter_id is not None:
        adapter = db.scalar(
            select(SutAdapter).where(
                SutAdapter.id == default_adapter_id,
                SutAdapter.is_deleted == 0,
            )
        )
        if adapter is None:
            raise AppError(f"默认被测适配器不存在: id={default_adapter_id}", status_code=400)
        if adapter.eval_level != eval_level:
            raise AppError(
                f"默认适配器层级({adapter.eval_level})与套件层级({eval_level})不匹配",
                status_code=400,
            )

    if default_provider_id is not None:
        provider = db.scalar(
            select(DecisionProvider).where(
                DecisionProvider.id == default_provider_id,
                DecisionProvider.is_deleted == 0,
            )
        )
        if provider is None:
            raise AppError(f"默认判别模型渠道不存在: id={default_provider_id}", status_code=400)

    if not dimensions:
        raise AppError("评测套件必须至少绑定一个评价维度版本", status_code=400)

    seen_dim_codes: set[str] = set()
    normalized_bindings: list[dict[str, Any]] = []
    for binding in dimensions:
        dim_code = binding.dimension_code.strip()
        if not dim_code:
            raise AppError("绑定维度编码不能为空", status_code=400)
        if dim_code in seen_dim_codes:
            raise AppError(f"同一评测套件内不允许重复绑定维度: {dim_code}", status_code=400)
        seen_dim_codes.add(dim_code)

        if binding.version < 1:
            raise AppError(f"维度 {dim_code} 版本号必须 >= 1", status_code=400)
        if not math.isfinite(float(binding.weight)) or float(binding.weight) <= 0.0:
            raise AppError(f"维度 {dim_code} 权重必须大于 0", status_code=400)
        if (
            not math.isfinite(float(binding.threshold))
            or not (0.0 <= float(binding.threshold) <= 1.0)
        ):
            raise AppError(f"维度 {dim_code} 阈值必须位于 [0.0, 1.0] 区间内", status_code=400)

        dim_row = db.scalar(
            select(Dimension).where(
                Dimension.dimension_code == dim_code,
                Dimension.version == binding.version,
                Dimension.is_deleted == 0,
            )
        )
        if dim_row is None:
            raise AppError(
                f"绑定的评价维度版本不存在: {dim_code}@v{binding.version}",
                status_code=400,
            )
        if dim_row.status != "ACTIVE":
            raise AppError(
                f"绑定的评价维度版本未处于 ACTIVE 状态: {dim_code}@v{binding.version} ({dim_row.status})",
                status_code=400,
            )

        normalized_bindings.append(
            {
                "dimension_code": dim_code,
                "version": int(binding.version),
                "weight": float(binding.weight),
                "threshold": float(binding.threshold),
                "is_blocker": bool(binding.is_blocker),
            }
        )

    return normalized_bindings


def create_suite(
    db: Session,
    payload: SuiteCreateRequest,
    *,
    created_by: str | None = None,
) -> dict[str, Any]:
    code = payload.suite_code.strip()
    if not _CODE_RE.match(code):
        raise AppError(f"suite_code 格式不合法: {code!r}", status_code=400)

    existing = db.scalar(select(Suite).where(Suite.suite_code == code))
    if existing is not None:
        raise AppError(f"评测套件编码已存在: {code}", status_code=400)

    normalized_dims = _validate_suite_fields(
        db,
        suite_name=payload.suite_name,
        eval_level=payload.eval_level,
        default_adapter_id=payload.default_adapter_id,
        default_provider_id=payload.default_provider_id,
        dimensions=payload.dimensions_json,
        pass_policy=payload.pass_policy,
        suite_pass_threshold=payload.suite_pass_threshold,
        confidence_review_floor=payload.confidence_review_floor,
        status=payload.status,
    )

    row = Suite(
        suite_code=code,
        suite_name=payload.suite_name.strip(),
        eval_level=payload.eval_level,
        default_adapter_id=payload.default_adapter_id,
        default_provider_id=payload.default_provider_id,
        default_model_override=payload.default_model_override,
        dimensions_json=normalized_dims,
        pass_policy=payload.pass_policy,
        suite_pass_threshold=float(payload.suite_pass_threshold),
        confidence_review_floor=float(payload.confidence_review_floor),
        enable_semantic_regression=1 if bool(payload.enable_semantic_regression) else 0,
        status=payload.status,
        description=payload.description,
        created_by=created_by,
        is_deleted=0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return serialize_suite(row)


def update_suite(
    db: Session,
    suite_code: str,
    payload: SuiteUpdateRequest,
) -> dict[str, Any]:
    row = db.scalar(
        select(Suite).where(
            Suite.suite_code == suite_code,
            Suite.is_deleted == 0,
        )
    )
    if row is None:
        raise AppError(f"评测套件不存在: {suite_code}", status_code=404)

    if payload.suite_code is not None and payload.suite_code.strip() != suite_code:
        raise AppError("评测套件编码 suite_code 不允许修改", status_code=400)

    normalized_dims = _validate_suite_fields(
        db,
        suite_name=payload.suite_name,
        eval_level=payload.eval_level,
        default_adapter_id=payload.default_adapter_id,
        default_provider_id=payload.default_provider_id,
        dimensions=payload.dimensions_json,
        pass_policy=payload.pass_policy,
        suite_pass_threshold=payload.suite_pass_threshold,
        confidence_review_floor=payload.confidence_review_floor,
        status=payload.status,
    )

    row.suite_name = payload.suite_name.strip()
    row.eval_level = payload.eval_level
    row.default_adapter_id = payload.default_adapter_id
    row.default_provider_id = payload.default_provider_id
    row.default_model_override = payload.default_model_override
    row.dimensions_json = normalized_dims
    row.pass_policy = payload.pass_policy
    row.suite_pass_threshold = float(payload.suite_pass_threshold)
    row.confidence_review_floor = float(payload.confidence_review_floor)
    row.enable_semantic_regression = 1 if bool(payload.enable_semantic_regression) else 0
    row.status = payload.status
    row.description = payload.description

    db.commit()
    db.refresh(row)
    return serialize_suite(row)
