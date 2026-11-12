from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common.auth_deps import CurrentPrincipal, require_console_user
from app.common.envelope import ok_response
from app.db.session import get_db
from app.dimensions.schemas import SuiteCreateRequest, SuiteUpdateRequest
from app.dimensions.service import (
    create_suite,
    get_suite_by_code,
    list_suites,
    update_suite,
)

router = APIRouter(tags=["suites"], dependencies=[Depends(require_console_user)])


@router.get("/suites")
def get_suites(
    eval_level: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(list_suites(db, eval_level=eval_level, status=status))


@router.get("/suites/{suite_code}")
def get_suite_detail(
    suite_code: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(get_suite_by_code(db, suite_code))


@router.post("/suites")
def post_create_suite(
    payload: SuiteCreateRequest,
    principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    created = create_suite(db, payload, created_by=principal.identifier)
    return ok_response(created)


@router.put("/suites/{suite_code}")
def put_update_suite(
    suite_code: str,
    payload: SuiteUpdateRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    updated = update_suite(db, suite_code, payload)
    return ok_response(updated)
