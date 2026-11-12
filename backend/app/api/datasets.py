from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.adapters import get_sut_http_transport
from app.common.auth_deps import (
    CurrentPrincipal,
    require_console_or_api_key,
    require_console_user,
)
from app.common.envelope import ok_response
from app.config import Settings, get_settings
from app.db.session import get_db
from app.ingestion.schemas import (
    DatasetCreateRequest,
    DatasetImportRequest,
    DatasetUpdateRequest,
    MysqlIngestRequest,
    RecordComponentSnapshotsRequest,
)
from app.ingestion.service import (
    create_dataset,
    get_dataset_detail,
    import_cases,
    ingest_from_mysql,
    list_datasets,
    record_dataset_component_snapshots,
    update_dataset,
)

router = APIRouter(tags=["datasets"])


def get_desensitize_http_transport() -> httpx.AsyncBaseTransport | None:
    return None


def get_harness_record_http_transport(
    sut_transport: httpx.AsyncBaseTransport | None = Depends(get_sut_http_transport),
) -> httpx.AsyncBaseTransport | None:
    return sut_transport


@router.get("/datasets")
def get_datasets(
    eval_level: str | None = Query(default=None),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    _principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(
        list_datasets(db, eval_level=eval_level, status=status, q=q)
    )


@router.get("/datasets/{dataset_id}")
def get_dataset(
    dataset_id: int,
    _principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(get_dataset_detail(db, dataset_id))


@router.post("/datasets")
def post_create_dataset(
    payload: DatasetCreateRequest,
    principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(
        create_dataset(db, payload, created_by=principal.identifier)
    )


@router.put("/datasets/{dataset_id}")
def put_update_dataset(
    dataset_id: int,
    payload: DatasetUpdateRequest,
    _principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return ok_response(update_dataset(db, dataset_id, payload))


@router.post("/datasets/{dataset_id}/import")
async def post_import_dataset_cases(
    dataset_id: int,
    payload: DatasetImportRequest,
    principal: CurrentPrincipal = Depends(require_console_or_api_key),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    transport: httpx.AsyncBaseTransport | None = Depends(get_desensitize_http_transport),
) -> dict[str, Any]:
    result = await import_cases(
        db,
        dataset_id,
        payload,
        operator=principal.identifier,
        settings=settings,
        transport=transport,
    )
    return ok_response(result)


@router.post("/datasets/{dataset_id}/ingest-from-mysql")
async def post_ingest_dataset_from_mysql(
    dataset_id: int,
    payload: MysqlIngestRequest,
    _principal: CurrentPrincipal = Depends(require_console_or_api_key),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    transport: httpx.AsyncBaseTransport | None = Depends(get_desensitize_http_transport),
) -> dict[str, Any]:
    result = await ingest_from_mysql(
        db,
        dataset_id,
        payload,
        settings=settings,
        transport=transport,
    )
    return ok_response(result)


@router.post("/datasets/{dataset_id}/record-component-snapshots")
async def post_record_component_snapshots(
    dataset_id: int,
    payload: RecordComponentSnapshotsRequest,
    _principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
    transport: httpx.AsyncBaseTransport | None = Depends(
        get_harness_record_http_transport
    ),
) -> dict[str, Any]:
    result = await record_dataset_component_snapshots(
        db,
        dataset_id,
        payload,
        transport=transport,
    )
    return ok_response(result)
