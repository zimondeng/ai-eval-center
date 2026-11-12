from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.api.adapters import get_sut_http_transport
from app.api.providers import get_provider_http_transport
from app.common.auth_deps import CurrentPrincipal, require_console_or_api_key
from app.common.envelope import ok_response
from app.config import Settings, get_settings
from app.db.session import get_db
from app.runner.executor import (
    cancel_eval_run,
    create_and_dispatch_run,
    get_run_by_no_or_404,
    list_eval_runs,
    serialize_run_detail,
)
from app.runner.queries import compare_eval_runs, list_run_cases
from app.runner.recalculator import recalculate_run, recommend_run_thresholds
from app.runner.schemas import (
    RecalculateRunRequest,
    RecommendThresholdsRequest,
    RunCreateRequest,
)

router = APIRouter(tags=["runs"])


def get_run_sut_http_transport(
    sut_transport: httpx.AsyncBaseTransport | None = Depends(get_sut_http_transport),
) -> httpx.AsyncBaseTransport | None:
    return sut_transport


def get_run_judge_http_transport(
    provider_transport: httpx.AsyncBaseTransport | None = Depends(get_provider_http_transport),
) -> httpx.AsyncBaseTransport | None:
    return provider_transport


@router.post("/runs")
async def post_create_run(
    payload: RunCreateRequest,
    principal: CurrentPrincipal = Depends(require_console_or_api_key),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    sut_transport: httpx.AsyncBaseTransport | None = Depends(get_run_sut_http_transport),
    judge_transport: httpx.AsyncBaseTransport | None = Depends(get_run_judge_http_transport),
) -> dict[str, Any]:
    result = await create_and_dispatch_run(
        db,
        payload,
        triggered_by=principal.identifier,
        settings=settings,
        sut_transport=sut_transport,
        judge_transport=judge_transport,
    )
    return ok_response(result)


@router.get("/runs")
def get_runs_list(
    suite_code: str | None = Query(default=None),
    suite_id: int | None = Query(default=None),
    dataset_id: int | None = Query(default=None),
    dataset_split_filter: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    _principal: CurrentPrincipal = Depends(require_console_or_api_key),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(
        list_eval_runs(
            db,
            suite_code=suite_code,
            suite_id=suite_id,
            dataset_id=dataset_id,
            dataset_split_filter=dataset_split_filter,
            status=status,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/runs/{run_no}")
def get_run_report(
    run_no: str,
    _principal: CurrentPrincipal = Depends(require_console_or_api_key),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = get_run_by_no_or_404(db, run_no)
    return ok_response(serialize_run_detail(db, run))


@router.post("/runs/{run_no}/cancel")
def post_cancel_run(
    run_no: str,
    _principal: CurrentPrincipal = Depends(require_console_or_api_key),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(cancel_eval_run(db, run_no))


@router.get("/runs/{run_no}/cases")
def get_run_cases_endpoint(
    run_no: str,
    passed: int | bool | str | None = Query(default=None),
    needs_human_review: int | bool | str | None = Query(default=None),
    regression_tag: str | None = Query(default=None),
    exec_status: str | None = Query(default=None),
    case_ids: str | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    _principal: CurrentPrincipal = Depends(require_console_or_api_key),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(
        list_run_cases(
            db,
            run_no,
            passed=passed,
            needs_human_review=needs_human_review,
            regression_tag=regression_tag,
            exec_status=exec_status,
            case_ids=case_ids,
            q=q,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/runs/{run_no}/compare/{baseline_run_no}")
def get_compare_runs_endpoint(
    run_no: str,
    baseline_run_no: str,
    regression_tag: str | None = Query(default=None),
    only_changed: bool = Query(default=False),
    _principal: CurrentPrincipal = Depends(require_console_or_api_key),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(
        compare_eval_runs(
            db,
            run_no,
            baseline_run_no,
            regression_tag=regression_tag,
            only_changed=only_changed,
        )
    )


@router.post("/runs/{run_no}/recalculate")
def post_recalculate_run(
    run_no: str,
    payload: RecalculateRunRequest | None = Body(default=None),
    _principal: CurrentPrincipal = Depends(require_console_or_api_key),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(recalculate_run(db, run_no, payload))


@router.post("/runs/{run_no}/recommend-thresholds")
def post_recommend_thresholds(
    run_no: str,
    payload: RecommendThresholdsRequest | None = Body(default=None),
    _principal: CurrentPrincipal = Depends(require_console_or_api_key),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(recommend_run_thresholds(db, run_no, payload))
