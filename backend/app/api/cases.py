from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.common.auth_deps import CurrentPrincipal, require_console_user
from app.common.envelope import ok_response
from app.db.session import get_db
from app.ingestion.schemas import CaseCreateRequest, CaseCurateRequest
from app.ingestion.service import (
    create_manual_case,
    curate_case,
    delete_case,
    get_case_detail,
    list_dataset_cases,
)

router = APIRouter(tags=["cases"])


@router.get("/datasets/{dataset_id}/cases")
def get_dataset_cases(
    dataset_id: int,
    split: str | None = Query(default=None),
    dataset_split: str | None = Query(default=None),
    review_status: str | None = Query(default=None),
    pii_masked: int | bool | None = Query(default=None),
    has_snapshot: bool | None = Query(default=None),
    q: str | None = Query(default=None),
    sort: str = Query(default="id_asc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    _principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    effective_split = split if split is not None else dataset_split
    return ok_response(
        list_dataset_cases(
            db,
            dataset_id,
            split=effective_split,
            review_status=review_status,
            pii_masked=pii_masked,
            has_snapshot=has_snapshot,
            q=q,
            sort=sort,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/datasets/{dataset_id}/cases")
def post_create_manual_case(
    dataset_id: int,
    payload: CaseCreateRequest,
    principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(
        create_manual_case(
            db,
            dataset_id,
            payload,
            operator=principal.identifier,
        )
    )


@router.get("/cases/{case_id}")
def get_case(
    case_id: int,
    _principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(get_case_detail(db, case_id))


@router.put("/cases/{case_id}/curate")
def put_curate_case(
    case_id: int,
    payload: CaseCurateRequest,
    principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(
        curate_case(
            db,
            case_id,
            payload,
            reviewer=principal.identifier,
        )
    )


@router.delete("/cases/{case_id}")
def delete_single_case(
    case_id: int,
    _principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(delete_case(db, case_id))
