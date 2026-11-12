from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RunCreateRequest(BaseModel):
    run_name: str = Field(..., min_length=1, max_length=128)
    suite_code: str | None = Field(default=None, max_length=64)
    suite_id: int | None = None
    dataset_id: int
    dataset_split_filter: Literal["DEV", "HOLDOUT", "ALL"] = "HOLDOUT"
    sut_adapter_code: str | None = Field(default=None, max_length=64)
    sut_adapter_id: int | None = None
    provider_code: str | None = Field(default=None, max_length=64)
    provider_id: int | None = None
    model_override: str | None = Field(default=None, max_length=128)
    baseline_run_id: int | None = None
    baseline_run_no: str | None = Field(default=None, max_length=64)
    sut_fingerprint: dict[str, Any] = Field(default_factory=dict)
    sut_fingerprint_json: dict[str, Any] | None = None
    run_overrides: dict[str, Any] | None = None
    run_sync: bool = False


class DimensionOverrideInput(BaseModel):
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    weight: float | None = Field(default=None, gt=0.0)
    is_blocker: bool | None = None
    strict_mode: bool | None = None


class RecalculateRunRequest(BaseModel):
    pass_policy: Literal["ALL_DIMENSIONS", "ALL_BLOCKERS_AND_WEIGHTED"] | None = None
    suite_pass_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_review_floor: float | None = Field(default=None, ge=0.0, le=1.0)
    dimension_overrides: dict[str, DimensionOverrideInput] = Field(default_factory=dict)


class RecommendThresholdsRequest(BaseModel):
    metric: Literal["youden_j", "f1"] = "youden_j"
    labels: dict[str, bool | int] | None = None
