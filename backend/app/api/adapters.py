from __future__ import annotations

from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import (
    SutExecutionError,
    SutExecutionInput,
    execute_sut_adapter,
    validate_mapping_jsonpaths,
)
from app.common.auth_deps import CurrentPrincipal, require_console_user
from app.common.envelope import ok_response
from app.common.errors import AppError
from app.db.base import utcnow_naive
from app.db.models import SutAdapter
from app.db.session import get_db

router = APIRouter(tags=["adapters"])

EvalLevelLiteral = Literal["E2E", "COMPONENT"]
AdapterTypeLiteral = Literal["HTTP_JSON", "HTTP_SSE", "COMPONENT_HARNESS"]
AdapterStatusLiteral = Literal["ACTIVE", "DISABLED"]


def _validate_http_url(url: str | None, field_name: str) -> str | None:
    if url is None:
        return None
    cleaned = url.strip()
    if not cleaned:
        return None
    if not cleaned.startswith(("http://", "https://")):
        raise AppError(
            f"{field_name} 必须以 http:// 或 https:// 开头",
            status_code=400,
            code=400,
        )
    return cleaned


class AdapterCreateOrUpsertRequest(BaseModel):
    adapter_code: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    adapter_name: str = Field(..., min_length=1, max_length=128)
    eval_level: EvalLevelLiteral
    adapter_type: AdapterTypeLiteral
    endpoint_url: str = Field(..., min_length=1, max_length=255)
    record_endpoint_url: str | None = Field(default=None, max_length=255)
    auth_config_json: dict[str, Any] | None = None
    request_mapping_json: dict[str, Any] = Field(default_factory=dict)
    response_mapping_json: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    max_concurrency: int = Field(default=4, ge=1, le=128)
    status: AdapterStatusLiteral = "ACTIVE"
    description: str | None = Field(default=None, max_length=500)


class AdapterUpdateRequest(BaseModel):
    adapter_name: str | None = Field(default=None, min_length=1, max_length=128)
    eval_level: EvalLevelLiteral | None = None
    adapter_type: AdapterTypeLiteral | None = None
    endpoint_url: str | None = Field(default=None, min_length=1, max_length=255)
    record_endpoint_url: str | None = Field(default=None, max_length=255)
    auth_config_json: dict[str, Any] | None = None
    request_mapping_json: dict[str, Any] | None = None
    response_mapping_json: dict[str, Any] | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)
    max_concurrency: int | None = Field(default=None, ge=1, le=128)
    status: AdapterStatusLiteral | None = None
    description: str | None = Field(default=None, max_length=500)


class AdapterTestRequest(BaseModel):
    input_text: str = Field(default="你好，请介绍常州住房公积金咨询渠道。")
    conversation_turns_json: list[dict[str, Any]] | None = None
    sut_input_json: dict[str, Any] | None = None
    sut_context_json: dict[str, Any] | None = None
    run_overrides: dict[str, Any] | None = None


def get_sut_http_transport() -> httpx.AsyncBaseTransport | None:
    return None


def _serialize_adapter(row: SutAdapter) -> dict[str, Any]:
    return {
        "id": row.id,
        "uuid": row.uuid,
        "adapter_code": row.adapter_code,
        "adapter_name": row.adapter_name,
        "eval_level": row.eval_level,
        "adapter_type": row.adapter_type,
        "endpoint_url": row.endpoint_url,
        "record_endpoint_url": row.record_endpoint_url,
        "auth_config_json": row.auth_config_json,
        "request_mapping_json": row.request_mapping_json,
        "response_mapping_json": row.response_mapping_json,
        "timeout_seconds": row.timeout_seconds,
        "max_concurrency": row.max_concurrency,
        "status": row.status,
        "description": row.description,
        "created_by": row.created_by,
        "create_time": row.create_time.isoformat() if row.create_time else None,
        "update_time": row.update_time.isoformat() if row.update_time else None,
    }


def _validate_adapter_mappings(
    request_mapping: dict[str, Any] | None,
    response_mapping: dict[str, Any] | None,
) -> None:
    try:
        validate_mapping_jsonpaths(request_mapping, response_mapping)
    except ValueError as exc:
        raise AppError(str(exc), status_code=400, code=400) from exc


@router.get("/adapters")
def list_adapters(
    eval_level: str | None = Query(default=None),
    adapter_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    _principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(SutAdapter).where(SutAdapter.is_deleted == 0)
    if eval_level:
        stmt = stmt.where(SutAdapter.eval_level == eval_level.strip().upper())
    if adapter_type:
        stmt = stmt.where(SutAdapter.adapter_type == adapter_type.strip().upper())
    if status:
        stmt = stmt.where(SutAdapter.status == status.strip().upper())
    stmt = stmt.order_by(SutAdapter.id.asc())
    rows = db.scalars(stmt).all()
    return ok_response([_serialize_adapter(r) for r in rows])


@router.get("/adapters/{adapter_code}")
def get_adapter(
    adapter_code: str,
    _principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = db.scalar(
        select(SutAdapter).where(
            SutAdapter.adapter_code == adapter_code,
            SutAdapter.is_deleted == 0,
        )
    )
    if row is None:
        raise AppError(f"被测适配器不存在: {adapter_code}", status_code=404, code=404)
    return ok_response(_serialize_adapter(row))


@router.post("/adapters")
def create_or_upsert_adapter(
    payload: AdapterCreateOrUpsertRequest,
    principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    endpoint_url = _validate_http_url(payload.endpoint_url, "endpoint_url")
    assert endpoint_url is not None
    record_url = _validate_http_url(payload.record_endpoint_url, "record_endpoint_url")
    _validate_adapter_mappings(payload.request_mapping_json, payload.response_mapping_json)

    existing = db.scalar(
        select(SutAdapter).where(SutAdapter.adapter_code == payload.adapter_code)
    )
    now = utcnow_naive()
    if existing is None:
        row = SutAdapter(
            adapter_code=payload.adapter_code,
            adapter_name=payload.adapter_name,
            eval_level=payload.eval_level,
            adapter_type=payload.adapter_type,
            endpoint_url=endpoint_url,
            record_endpoint_url=record_url,
            auth_config_json=payload.auth_config_json,
            request_mapping_json=payload.request_mapping_json,
            response_mapping_json=payload.response_mapping_json,
            timeout_seconds=payload.timeout_seconds,
            max_concurrency=payload.max_concurrency,
            status=payload.status,
            description=payload.description,
            created_by=principal.username,
            is_deleted=0,
            create_time=now,
            update_time=now,
        )
        db.add(row)
    else:
        row = existing
        row.adapter_name = payload.adapter_name
        row.eval_level = payload.eval_level
        row.adapter_type = payload.adapter_type
        row.endpoint_url = endpoint_url
        row.record_endpoint_url = record_url
        row.auth_config_json = payload.auth_config_json
        row.request_mapping_json = payload.request_mapping_json
        row.response_mapping_json = payload.response_mapping_json
        row.timeout_seconds = payload.timeout_seconds
        row.max_concurrency = payload.max_concurrency
        row.status = payload.status
        row.description = payload.description
        row.is_deleted = 0
        row.update_time = now

    db.commit()
    db.refresh(row)
    return ok_response(_serialize_adapter(row))


@router.put("/adapters/{adapter_code}")
def update_adapter(
    adapter_code: str,
    payload: AdapterUpdateRequest,
    _principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = db.scalar(
        select(SutAdapter).where(
            SutAdapter.adapter_code == adapter_code,
            SutAdapter.is_deleted == 0,
        )
    )
    if row is None:
        raise AppError(f"被测适配器不存在: {adapter_code}", status_code=404, code=404)

    fields_set = payload.model_fields_set
    new_req_map = (
        payload.request_mapping_json
        if "request_mapping_json" in fields_set and payload.request_mapping_json is not None
        else row.request_mapping_json
    )
    new_resp_map = (
        payload.response_mapping_json
        if "response_mapping_json" in fields_set and payload.response_mapping_json is not None
        else row.response_mapping_json
    )
    _validate_adapter_mappings(new_req_map, new_resp_map)

    if "adapter_name" in fields_set and payload.adapter_name is not None:
        row.adapter_name = payload.adapter_name
    if "eval_level" in fields_set and payload.eval_level is not None:
        row.eval_level = payload.eval_level
    if "adapter_type" in fields_set and payload.adapter_type is not None:
        row.adapter_type = payload.adapter_type
    if "endpoint_url" in fields_set and payload.endpoint_url is not None:
        validated_ep = _validate_http_url(payload.endpoint_url, "endpoint_url")
        assert validated_ep is not None
        row.endpoint_url = validated_ep
    if "record_endpoint_url" in fields_set:
        row.record_endpoint_url = _validate_http_url(
            payload.record_endpoint_url, "record_endpoint_url"
        )
    if "auth_config_json" in fields_set:
        row.auth_config_json = payload.auth_config_json
    if "request_mapping_json" in fields_set and payload.request_mapping_json is not None:
        row.request_mapping_json = payload.request_mapping_json
    if "response_mapping_json" in fields_set and payload.response_mapping_json is not None:
        row.response_mapping_json = payload.response_mapping_json
    if "timeout_seconds" in fields_set and payload.timeout_seconds is not None:
        row.timeout_seconds = payload.timeout_seconds
    if "max_concurrency" in fields_set and payload.max_concurrency is not None:
        row.max_concurrency = payload.max_concurrency
    if "status" in fields_set and payload.status is not None:
        row.status = payload.status
    if "description" in fields_set:
        row.description = payload.description

    row.update_time = utcnow_naive()
    db.commit()
    db.refresh(row)
    return ok_response(_serialize_adapter(row))


@router.post("/adapters/{adapter_code}/test")
async def test_adapter(
    adapter_code: str,
    payload: AdapterTestRequest,
    _principal: CurrentPrincipal = Depends(require_console_user),
    db: Session = Depends(get_db),
    transport: httpx.AsyncBaseTransport | None = Depends(get_sut_http_transport),
) -> dict[str, Any]:
    row = db.scalar(
        select(SutAdapter).where(
            SutAdapter.adapter_code == adapter_code,
            SutAdapter.is_deleted == 0,
        )
    )
    if row is None:
        raise AppError(f"被测适配器不存在: {adapter_code}", status_code=404, code=404)

    exec_input = SutExecutionInput(
        input_text=payload.input_text,
        conversation_turns_json=payload.conversation_turns_json,
        sut_input_json=payload.sut_input_json,
        sut_context_json=payload.sut_context_json,
        run_overrides=payload.run_overrides,
    )
    try:
        result = await execute_sut_adapter(
            row,
            exec_input,
            run_overrides=payload.run_overrides,
            transport=transport,
        )
    except SutExecutionError as exc:
        raise AppError(
            exc.message,
            status_code=502,
            code=502,
            data={
                "request_preview": exc.request_preview,
                "upstream_status_code": exc.status_code,
            },
        ) from exc

    return ok_response(
        {
            "adapter_code": row.adapter_code,
            "adapter_type": row.adapter_type,
            "eval_level": row.eval_level,
            "request_preview": result.request_preview,
            "actual_output": result.actual_output,
            "actual_references": result.actual_references,
            "actual_structured": result.actual_structured,
            "sut_trace": result.sut_trace,
            "sut_latency_ms": result.sut_latency_ms,
            "ttft_ms": result.ttft_ms,
        }
    )

