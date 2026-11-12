from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CHAR, JSON, BigInteger, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    Base,
    DateTimeMs,
    FKBigInt,
    MediumText,
    PKBigInt,
    TinyInt1,
    new_uuid_hex,
    utcnow_naive,
)


class AdminUser(Base):
    __tablename__ = "ev_admin_user"
    __table_args__ = (
        UniqueConstraint("uuid", name="uk_ev_user_uuid"),
        UniqueConstraint("username", name="uk_ev_username"),
    )

    id: Mapped[int] = mapped_column(PKBigInt, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(CHAR(32), nullable=False, default=new_uuid_hex)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="ADMIN")
    is_enabled: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=1)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTimeMs, nullable=True)
    is_deleted: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTimeMs, nullable=False, default=utcnow_naive)
    update_time: Mapped[datetime] = mapped_column(
        DateTimeMs, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )


class ApiKey(Base):
    __tablename__ = "ev_api_key"
    __table_args__ = (
        UniqueConstraint("uuid", name="uk_ev_apikey_uuid"),
        UniqueConstraint("key_sha256", name="uk_ev_apikey_sha256"),
    )

    id: Mapped[int] = mapped_column(PKBigInt, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(CHAR(32), nullable=False, default=new_uuid_hex)
    key_name: Mapped[str] = mapped_column(String(128), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    key_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    rate_limit_rpm: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    expires_at: Mapped[datetime | None] = mapped_column(DateTimeMs, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTimeMs, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_deleted: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTimeMs, nullable=False, default=utcnow_naive)
    update_time: Mapped[datetime] = mapped_column(
        DateTimeMs, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )


class DecisionProvider(Base):
    __tablename__ = "ev_decision_provider"
    __table_args__ = (
        UniqueConstraint("uuid", name="uk_ev_provider_uuid"),
        UniqueConstraint("provider_code", name="uk_ev_provider_code"),
        Index("idx_ev_provider_status", "status", "is_default", "is_deleted"),
    )

    id: Mapped[int] = mapped_column(PKBigInt, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(CHAR(32), nullable=False, default=new_uuid_hex)
    provider_code: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint_url: Mapped[str] = mapped_column(String(255), nullable=False)
    models_list_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    default_model: Mapped[str] = mapped_column(String(128), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(24), nullable=False, default="BEARER")
    api_key_cipher: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_env_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    api_key_masked: Mapped[str | None] = mapped_column(String(32), nullable=True)
    network_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="PROXY_FALLBACK")
    custom_proxy_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=90)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    max_questions_per_call: Mapped[int] = mapped_column(Integer, nullable=False, default=64)
    max_options_per_question: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    waf_strip_code_retry: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=1)
    cost_per_1m_input_tokens: Mapped[float] = mapped_column(
        Numeric(10, 4, asdecimal=False), nullable=False, default=0.0
    )
    extra_options_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    is_default: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    last_health_ok: Mapped[int | None] = mapped_column(TinyInt1, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTimeMs, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_deleted: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTimeMs, nullable=False, default=utcnow_naive)
    update_time: Mapped[datetime] = mapped_column(
        DateTimeMs, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )


class SutAdapter(Base):
    __tablename__ = "ev_sut_adapter"
    __table_args__ = (
        UniqueConstraint("uuid", name="uk_ev_adapter_uuid"),
        UniqueConstraint("adapter_code", name="uk_ev_adapter_code"),
        Index("idx_ev_adapter_level", "eval_level", "status", "is_deleted"),
    )

    id: Mapped[int] = mapped_column(PKBigInt, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(CHAR(32), nullable=False, default=new_uuid_hex)
    adapter_code: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_name: Mapped[str] = mapped_column(String(128), nullable=False)
    eval_level: Mapped[str] = mapped_column(String(16), nullable=False)
    adapter_type: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint_url: Mapped[str] = mapped_column(String(255), nullable=False)
    record_endpoint_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_config_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    request_mapping_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    response_mapping_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_deleted: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTimeMs, nullable=False, default=utcnow_naive)
    update_time: Mapped[datetime] = mapped_column(
        DateTimeMs, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )


class Dimension(Base):
    __tablename__ = "ev_dimension"
    __table_args__ = (
        UniqueConstraint("uuid", name="uk_ev_dim_uuid"),
        UniqueConstraint("dimension_code", "version", name="uk_ev_dim_code_ver"),
        Index("idx_ev_dim_cat_status", "category", "status", "is_deleted"),
    )

    id: Mapped[int] = mapped_column(PKBigInt, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(CHAR(32), nullable=False, default=new_uuid_hex)
    dimension_code: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    dimension_name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    target_turn_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="SINGLE_TURN"
    )
    evaluator_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    state_fields_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    questions_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    deterministic_rule_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    default_threshold: Mapped[float] = mapped_column(
        Numeric(5, 4, asdecimal=False), nullable=False, default=0.7
    )
    strict_mode: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=0)
    default_weight: Mapped[float] = mapped_column(
        Numeric(6, 3, asdecimal=False), nullable=False, default=1.0
    )
    lint_passed: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=1)
    lint_report_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_deleted: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTimeMs, nullable=False, default=utcnow_naive)
    update_time: Mapped[datetime] = mapped_column(
        DateTimeMs, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )


class Suite(Base):
    __tablename__ = "ev_suite"
    __table_args__ = (
        UniqueConstraint("uuid", name="uk_ev_suite_uuid"),
        UniqueConstraint("suite_code", name="uk_ev_suite_code"),
    )

    id: Mapped[int] = mapped_column(PKBigInt, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(CHAR(32), nullable=False, default=new_uuid_hex)
    suite_code: Mapped[str] = mapped_column(String(64), nullable=False)
    suite_name: Mapped[str] = mapped_column(String(128), nullable=False)
    eval_level: Mapped[str] = mapped_column(String(16), nullable=False)
    default_adapter_id: Mapped[int | None] = mapped_column(FKBigInt, nullable=True)
    default_provider_id: Mapped[int | None] = mapped_column(FKBigInt, nullable=True)
    default_model_override: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dimensions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    pass_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ALL_BLOCKERS_AND_WEIGHTED"
    )
    suite_pass_threshold: Mapped[float] = mapped_column(
        Numeric(5, 4, asdecimal=False), nullable=False, default=0.75
    )
    confidence_review_floor: Mapped[float] = mapped_column(
        Numeric(5, 4, asdecimal=False), nullable=False, default=0.5
    )
    enable_semantic_regression: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_deleted: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTimeMs, nullable=False, default=utcnow_naive)
    update_time: Mapped[datetime] = mapped_column(
        DateTimeMs, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )


class Dataset(Base):
    __tablename__ = "ev_dataset"
    __table_args__ = (
        UniqueConstraint("uuid", name="uk_ev_ds_uuid"),
        UniqueConstraint("dataset_code", "version_tag", name="uk_ev_ds_code_ver"),
        Index("idx_ev_ds_level_status", "eval_level", "status", "is_deleted"),
    )

    id: Mapped[int] = mapped_column(PKBigInt, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(CHAR(32), nullable=False, default=new_uuid_hex)
    dataset_code: Mapped[str] = mapped_column(String(64), nullable=False)
    version_tag: Mapped[str] = mapped_column(String(32), nullable=False, default="v1.0")
    dataset_name: Mapped[str] = mapped_column(String(128), nullable=False)
    eval_level: Mapped[str] = mapped_column(String(16), nullable=False)
    desensitize_policy_snapshot: Mapped[str | None] = mapped_column(String(96), nullable=True)
    dev_case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    holdout_case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_deleted: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTimeMs, nullable=False, default=utcnow_naive)
    update_time: Mapped[datetime] = mapped_column(
        DateTimeMs, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )


class EvalCase(Base):
    __tablename__ = "ev_case"
    __table_args__ = (
        UniqueConstraint("uuid", name="uk_ev_case_uuid"),
        UniqueConstraint("dataset_id", "case_code", name="uk_ev_ds_case_code"),
        Index(
            "idx_ev_case_split_review",
            "dataset_id",
            "dataset_split",
            "review_status",
            "is_deleted",
        ),
        Index("idx_ev_case_uncertainty", "dataset_id", "review_status", "uncertainty_score"),
    )

    id: Mapped[int] = mapped_column(PKBigInt, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(CHAR(32), nullable=False, default=new_uuid_hex)
    dataset_id: Mapped[int] = mapped_column(FKBigInt, nullable=False)
    case_code: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_split: Mapped[str] = mapped_column(String(16), nullable=False, default="HOLDOUT")
    source_kind: Mapped[str] = mapped_column(String(24), nullable=False, default="MANUAL")
    source_ref_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    turn_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="SINGLE_TURN")
    input_text: Mapped[str] = mapped_column(MediumText, nullable=False)
    conversation_turns_json: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    expected_output: Mapped[str | None] = mapped_column(MediumText, nullable=True)
    expected_outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_contexts_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    expected_structured_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    sut_input_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    sut_context_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    tags_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False, default="NORMAL")
    pii_masked: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=0)
    desensitize_summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    uncertainty_score: Mapped[float | None] = mapped_column(
        Numeric(5, 4, asdecimal=False), nullable=True
    )
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING_REVIEW"
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTimeMs, nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_deleted: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTimeMs, nullable=False, default=utcnow_naive)
    update_time: Mapped[datetime] = mapped_column(
        DateTimeMs, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )


class EvalRun(Base):
    __tablename__ = "ev_run"
    __table_args__ = (
        UniqueConstraint("uuid", name="uk_ev_run_uuid"),
        UniqueConstraint("run_no", name="uk_ev_run_no"),
        Index("idx_ev_run_suite_ds", "suite_id", "dataset_id", "status"),
        Index("idx_ev_run_time", "create_time"),
    )

    id: Mapped[int] = mapped_column(PKBigInt, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(CHAR(32), nullable=False, default=new_uuid_hex)
    run_no: Mapped[str] = mapped_column(String(64), nullable=False)
    run_name: Mapped[str] = mapped_column(String(128), nullable=False)
    suite_id: Mapped[int] = mapped_column(FKBigInt, nullable=False)
    dataset_id: Mapped[int] = mapped_column(FKBigInt, nullable=False)
    dataset_split_filter: Mapped[str] = mapped_column(
        String(16), nullable=False, default="HOLDOUT"
    )
    sut_adapter_id: Mapped[int] = mapped_column(FKBigInt, nullable=False)
    sut_fingerprint_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    provider_id: Mapped[int] = mapped_column(FKBigInt, nullable=False)
    pinned_provider_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    pinned_endpoint_url: Mapped[str] = mapped_column(String(255), nullable=False)
    pinned_model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    baseline_run_id: Mapped[int | None] = mapped_column(FKBigInt, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    total_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uncertain_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pass_rate: Mapped[float | None] = mapped_column(
        Numeric(5, 4, asdecimal=False), nullable=True
    )
    weighted_avg_score: Mapped[float | None] = mapped_column(
        Numeric(5, 4, asdecimal=False), nullable=True
    )
    p50_sut_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    p95_sut_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    p50_ttft_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_decision_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_eval_cost: Mapped[float] = mapped_column(
        Numeric(12, 6, asdecimal=False), nullable=False, default=0.0
    )
    dimension_summary_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(
        JSON, nullable=True
    )
    regression_summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTimeMs, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTimeMs, nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_deleted: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTimeMs, nullable=False, default=utcnow_naive)
    update_time: Mapped[datetime] = mapped_column(
        DateTimeMs, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )


class EvalRunCase(Base):
    __tablename__ = "ev_run_case"
    __table_args__ = (
        UniqueConstraint("uuid", name="uk_ev_runcase_uuid"),
        UniqueConstraint("run_id", "case_id", name="uk_ev_runcase_pair"),
        Index(
            "idx_ev_runcase_filter",
            "run_id",
            "overall_passed",
            "needs_human_review",
            "regression_tag",
        ),
    )

    id: Mapped[int] = mapped_column(PKBigInt, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(CHAR(32), nullable=False, default=new_uuid_hex)
    run_id: Mapped[int] = mapped_column(FKBigInt, nullable=False)
    case_id: Mapped[int] = mapped_column(FKBigInt, nullable=False)
    exec_status: Mapped[str] = mapped_column(String(20), nullable=False, default="SUCCESS")
    actual_output: Mapped[str | None] = mapped_column(MediumText, nullable=True)
    actual_references_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    actual_structured_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    sut_trace_json: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    sut_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ttft_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    overall_score: Mapped[float | None] = mapped_column(
        Numeric(5, 4, asdecimal=False), nullable=True
    )
    overall_passed: Mapped[int | None] = mapped_column(TinyInt1, nullable=True)
    min_confidence: Mapped[float | None] = mapped_column(
        Numeric(5, 4, asdecimal=False), nullable=True
    )
    needs_human_review: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=0)
    regression_tag: Mapped[str | None] = mapped_column(String(32), nullable=True)
    regression_confidence: Mapped[float | None] = mapped_column(
        Numeric(5, 4, asdecimal=False), nullable=True
    )
    error_detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_deleted: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTimeMs, nullable=False, default=utcnow_naive)
    update_time: Mapped[datetime] = mapped_column(
        DateTimeMs, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )


class ProbabilityLedger(Base):
    __tablename__ = "ev_probability_ledger"
    __table_args__ = (
        UniqueConstraint("uuid", name="uk_ev_ledger_uuid"),
        UniqueConstraint("run_case_id", "dimension_id", name="uk_ev_ledger_case_dim"),
        Index("idx_ev_ledger_run_dim", "run_id", "dimension_code", "passed"),
    )

    id: Mapped[int] = mapped_column(PKBigInt, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(CHAR(32), nullable=False, default=new_uuid_hex)
    run_id: Mapped[int] = mapped_column(FKBigInt, nullable=False)
    run_case_id: Mapped[int] = mapped_column(FKBigInt, nullable=False)
    dimension_id: Mapped[int] = mapped_column(FKBigInt, nullable=False)
    dimension_code: Mapped[str] = mapped_column(String(64), nullable=False)
    dimension_version: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluator_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    raw_dimension_score: Mapped[float | None] = mapped_column(
        Numeric(6, 5, asdecimal=False), nullable=True
    )
    applied_threshold: Mapped[float] = mapped_column(
        Numeric(5, 4, asdecimal=False), nullable=False
    )
    applied_weight: Mapped[float] = mapped_column(
        Numeric(6, 3, asdecimal=False), nullable=False, default=1.0
    )
    is_blocker: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=0)
    passed: Mapped[int | None] = mapped_column(TinyInt1, nullable=True)
    min_question_confidence: Mapped[float | None] = mapped_column(
        Numeric(5, 4, asdecimal=False), nullable=True
    )
    deterministic_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_answers_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    waf_fallback_used: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=0)
    upstream_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    eval_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_deleted: Mapped[int] = mapped_column(TinyInt1, nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTimeMs, nullable=False, default=utcnow_naive)
    update_time: Mapped[datetime] = mapped_column(
        DateTimeMs, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )
