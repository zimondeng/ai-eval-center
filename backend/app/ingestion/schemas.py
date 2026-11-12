from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

EvalLevelLiteral = Literal["E2E", "COMPONENT"]
DatasetStatusLiteral = Literal["DRAFT", "PUBLISHED", "ARCHIVED"]
DatasetSplitLiteral = Literal["DEV", "HOLDOUT"]
TurnModeLiteral = Literal["SINGLE_TURN", "MULTI_TURN"]
DifficultyLiteral = Literal["EASY", "NORMAL", "HARD", "ADVERSARIAL"]
ReviewStatusLiteral = Literal["DRAFT", "PENDING_REVIEW", "APPROVED", "DISABLED"]
SourceKindLiteral = Literal[
    "ONLINE_PULL",
    "FILE_IMPORT",
    "HARNESS_RECORD",
    "MANUAL",
    "SYNTHETIC",
]


class DatasetCreateRequest(BaseModel):
    dataset_code: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    version_tag: str = Field(default="v1.0", min_length=1, max_length=32)
    dataset_name: str = Field(..., min_length=1, max_length=128)
    eval_level: EvalLevelLiteral
    desensitize_policy_snapshot: str | None = Field(default=None, max_length=96)
    status: DatasetStatusLiteral = "DRAFT"
    description: str | None = Field(default=None, max_length=500)


class DatasetUpdateRequest(BaseModel):
    dataset_name: str | None = Field(default=None, min_length=1, max_length=128)
    version_tag: str | None = Field(default=None, min_length=1, max_length=32)
    eval_level: EvalLevelLiteral | None = None
    desensitize_policy_snapshot: str | None = Field(default=None, max_length=96)
    status: DatasetStatusLiteral | None = None
    description: str | None = Field(default=None, max_length=500)


class CaseImportItem(BaseModel):
    case_code: str | None = Field(default=None, max_length=64)
    dataset_split: DatasetSplitLiteral | None = None
    source_kind: SourceKindLiteral = "FILE_IMPORT"
    source_ref_json: dict[str, Any] | None = None
    turn_mode: TurnModeLiteral = "SINGLE_TURN"
    input_text: str | None = None
    query: str | None = None
    conversation_turns_json: list[dict[str, Any]] | None = None
    expected_output: str | None = None
    expected_outcome: str | None = None
    reference_contexts_json: list[str] | None = None
    expected_structured_json: dict[str, Any] | None = None
    sut_input_json: dict[str, Any] | None = None
    sut_context_json: dict[str, Any] | None = None
    tags_json: list[str] | None = None
    difficulty: DifficultyLiteral = "NORMAL"
    pii_masked: int | bool | None = None
    desensitize_summary_json: dict[str, Any] | None = None
    uncertainty_score: float | None = Field(default=None, ge=0.0, le=1.0)
    review_status: ReviewStatusLiteral = "PENDING_REVIEW"
    review_note: str | None = Field(default=None, max_length=500)


class DatasetImportRequest(BaseModel):
    cases: list[CaseImportItem] | None = None
    jsonl_text: str | None = None
    auto_desensitize: bool = False
    desensitize_policy_code: str = Field(default="gov_eval_corpus", min_length=1, max_length=64)
    default_pii_masked: int | bool | None = None
    dev_ratio: float = Field(default=0.5, ge=0.0, le=1.0)


class IngestFilter(BaseModel):
    start_time: str | None = None
    end_time: str | None = None
    mining_signals: list[str] | None = None
    limit: int = Field(default=100, ge=1, le=2000)


class MysqlIngestRequest(BaseModel):
    source_dsn_env: str = Field(..., min_length=1, max_length=96)
    preset_extractor: str = Field(
        default="gov_routing_conversation", min_length=1, max_length=64
    )
    filter: IngestFilter = Field(default_factory=IngestFilter)
    desensitize_policy_code: str = Field(default="gov_eval_corpus", min_length=1, max_length=64)
    dev_ratio: float = Field(default=0.5, ge=0.0, le=1.0)


class RecordComponentSnapshotsRequest(BaseModel):
    adapter_code: str | None = Field(default=None, max_length=64)
    sut_adapter_code: str | None = Field(default=None, max_length=64)
    adapter_id: int | None = None
    case_ids: list[int] | None = None
    split: DatasetSplitLiteral | None = None
    only_missing: bool = False


class CaseCreateRequest(BaseModel):
    case_code: str | None = Field(default=None, max_length=64)
    dataset_split: DatasetSplitLiteral | None = None
    source_kind: SourceKindLiteral = "MANUAL"
    source_ref_json: dict[str, Any] | None = None
    turn_mode: TurnModeLiteral = "SINGLE_TURN"
    input_text: str = Field(..., min_length=1)
    conversation_turns_json: list[dict[str, Any]] | None = None
    expected_output: str | None = None
    expected_outcome: str | None = None
    reference_contexts_json: list[str] | None = None
    expected_structured_json: dict[str, Any] | None = None
    sut_input_json: dict[str, Any] | None = None
    sut_context_json: dict[str, Any] | None = None
    tags_json: list[str] | None = None
    difficulty: DifficultyLiteral = "NORMAL"
    pii_masked: int | bool = 0
    desensitize_summary_json: dict[str, Any] | None = None
    uncertainty_score: float | None = Field(default=None, ge=0.0, le=1.0)
    review_status: ReviewStatusLiteral = "PENDING_REVIEW"
    review_note: str | None = Field(default=None, max_length=500)


class CaseCurateRequest(BaseModel):
    input_text: str | None = Field(default=None, min_length=1)
    conversation_turns_json: list[dict[str, Any]] | None = None
    expected_output: str | None = None
    expected_outcome: str | None = None
    reference_contexts_json: list[str] | None = None
    expected_structured_json: dict[str, Any] | None = None
    sut_input_json: dict[str, Any] | None = None
    sut_context_json: dict[str, Any] | None = None
    tags_json: list[str] | None = None
    dataset_split: DatasetSplitLiteral | None = None
    difficulty: DifficultyLiteral | None = None
    pii_masked: int | bool | None = None
    uncertainty_score: float | None = Field(default=None, ge=0.0, le=1.0)
    review_status: ReviewStatusLiteral | None = None
    review_note: str | None = Field(default=None, max_length=500)
