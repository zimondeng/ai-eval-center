from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common.auth_deps import CurrentPrincipal, require_console_user
from app.common.envelope import ok_response
from app.db.session import get_db
from app.dimensions.schemas import DimensionCreateRequest, DimensionLintRequest
from app.dimensions.service import (
    create_dimension_version,
    get_dimension_detail,
    list_dimensions,
)
from app.linter import lint_dimension

router = APIRouter(tags=["dimensions"], dependencies=[Depends(require_console_user)])


@router.post("/dimensions/lint")
def post_lint_dimension(payload: DimensionLintRequest) -> dict[str, Any]:
    report = lint_dimension(
        state_fields=payload.resolved_state_fields(),
        questions=payload.resolved_questions(),
        max_options=payload.max_options,
    )
    return ok_response(report.model_dump())


@router.get("/dimensions")
def get_dimensions(
    category: str | None = None,
    status: str | None = None,
    evaluator_kind: str | None = None,
    latest_only: bool = False,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    items = list_dimensions(
        db,
        category=category,
        status=status,
        evaluator_kind=evaluator_kind,
        latest_only=latest_only,
    )
    return ok_response(items)


@router.get("/dimensions/{dimension_code}")
def get_dimension_by_code(
    dimension_code: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    detail = get_dimension_detail(db, dimension_code)
    return ok_response(detail)


@router.post("/dimensions")
def post_create_dimension(
    payload: DimensionCreateRequest,
    principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    created = create_dimension_version(
        db,
        payload,
        created_by=principal.identifier,
    )
    return ok_response(created)
