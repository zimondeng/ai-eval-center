from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DimensionLintRequest(BaseModel):
    state_fields_json: list[str] | None = None
    questions_json: list[dict[str, Any]] | None = None
    state_fields: list[str] | None = None
    questions: list[dict[str, Any]] | None = None
    max_options: int = Field(default=24, ge=2, le=64)

    def resolved_state_fields(self) -> list[str]:
        if self.state_fields_json is not None:
            return self.state_fields_json
        if self.state_fields is not None:
            return self.state_fields
        return []

    def resolved_questions(self) -> list[dict[str, Any]]:
        if self.questions_json is not None:
            return self.questions_json
        if self.questions is not None:
            return self.questions
        return []


class DimensionCreateRequest(BaseModel):
    dimension_code: str
    dimension_name: str
    category: str
    target_turn_mode: str = "SINGLE_TURN"
    evaluator_kind: str
    state_fields_json: list[str] | None = None
    questions_json: list[dict[str, Any]] | None = None
    deterministic_rule_json: dict[str, Any] | None = None
    default_threshold: float = 0.7
    strict_mode: bool | int = False
    default_weight: float = 1.0
    status: str = "ACTIVE"
    description: str | None = None


class SuiteDimensionBinding(BaseModel):
    dimension_code: str
    version: int
    weight: float = 1.0
    threshold: float = 0.75
    is_blocker: bool = False


class SuiteCreateRequest(BaseModel):
    suite_code: str
    suite_name: str
    eval_level: str
    default_adapter_id: int | None = None
    default_provider_id: int | None = None
    default_model_override: str | None = None
    dimensions_json: list[SuiteDimensionBinding]
    pass_policy: str = "ALL_BLOCKERS_AND_WEIGHTED"
    suite_pass_threshold: float = 0.75
    confidence_review_floor: float = 0.50
    enable_semantic_regression: bool | int = True
    status: str = "ACTIVE"
    description: str | None = None


class SuiteUpdateRequest(BaseModel):
    suite_code: str | None = None
    suite_name: str
    eval_level: str
    default_adapter_id: int | None = None
    default_provider_id: int | None = None
    default_model_override: str | None = None
    dimensions_json: list[SuiteDimensionBinding]
    pass_policy: str = "ALL_BLOCKERS_AND_WEIGHTED"
    suite_pass_threshold: float = 0.75
    confidence_review_floor: float = 0.50
    enable_semantic_regression: bool | int = True
    status: str = "ACTIVE"
    description: str | None = None
