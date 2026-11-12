from __future__ import annotations

import copy
import json
import re
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import Engine, JSON as SQLJSON, and_, case as sql_case, func, or_, select
from sqlalchemy.orm import Session

from app.adapters.dispatcher import record_component_snapshot
from app.adapters.models import SutExecutionError
from app.common.crypto import sha256_hex
from app.common.errors import AppError
from app.config import Settings, get_settings
from app.db.base import new_uuid_hex, utcnow_naive
from app.db.models import Dataset, EvalCase, SutAdapter
from app.ingestion.desensitize import (
    DesensitizationClient,
    DesensitizationError,
    merge_tag_counts,
    sanitize_desensitize_summary,
)
from app.ingestion.mysql_sampler import extract_source_records
from app.ingestion.schemas import (
    CaseCreateRequest,
    CaseCurateRequest,
    CaseImportItem,
    DatasetCreateRequest,
    DatasetImportRequest,
    DatasetUpdateRequest,
    MysqlIngestRequest,
    RecordComponentSnapshotsRequest,
)
from app.ingestion.splitter import assign_dataset_split

UNMASKED_APPROVAL_ERR_MSG = (
    "当前用例尚未完成脱敏确认（pii_masked = 0），禁止置为已审核生效"
)
_CASE_CODE_CLEAN_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_MASK_BATCH_CHUNK_SIZE = 200


def _fmt_dt(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def normalize_pii_masked(value: int | bool | None, *, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int) and value in (0, 1):
        return int(value)
    raise AppError("pii_masked 仅允许取 0 或 1", status_code=400, code=400)


def _enforce_approval_pii_guard(review_status: str, pii_masked: int) -> None:
    if review_status == "APPROVED" and int(pii_masked) == 0:
        raise AppError(UNMASKED_APPROVAL_ERR_MSG, status_code=400, code=400)


def _clean_or_generate_case_code(
    raw_code: str | None,
    *,
    fallback_seed: str,
    prefix: str = "case",
) -> str:
    if raw_code is not None and raw_code.strip():
        cleaned = _CASE_CODE_CLEAN_RE.sub("_", raw_code.strip()).strip("_")
        if cleaned:
            return cleaned[:64]
    return f"{prefix}_{sha256_hex(fallback_seed)[:12]}"


def _dataset_case_stats(
    db: Session, dataset_ids: Sequence[int]
) -> dict[int, dict[str, int]]:
    if not dataset_ids:
        return {}
    stmt = (
        select(
            EvalCase.dataset_id,
            EvalCase.review_status,
            func.count(EvalCase.id),
        )
        .where(
            EvalCase.dataset_id.in_(list(dataset_ids)),
            EvalCase.is_deleted == 0,
        )
        .group_by(EvalCase.dataset_id, EvalCase.review_status)
    )
    stats: dict[int, dict[str, int]] = {
        ds_id: {"total_case_count": 0, "pending_review_count": 0}
        for ds_id in dataset_ids
    }
    for ds_id, rev_status, cnt in db.execute(stmt).all():
        entry = stats.setdefault(
            int(ds_id), {"total_case_count": 0, "pending_review_count": 0}
        )
        c_int = int(cnt or 0)
        entry["total_case_count"] += c_int
        if rev_status == "PENDING_REVIEW":
            entry["pending_review_count"] += c_int
    return stats


def serialize_dataset(
    row: Dataset,
    *,
    extra_stats: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    total_cnt = (
        int(extra_stats["total_case_count"])
        if extra_stats and "total_case_count" in extra_stats
        else int(row.dev_case_count or 0) + int(row.holdout_case_count or 0)
    )
    pending_cnt = (
        int(extra_stats["pending_review_count"])
        if extra_stats and "pending_review_count" in extra_stats
        else 0
    )
    return {
        "id": row.id,
        "uuid": row.uuid,
        "dataset_code": row.dataset_code,
        "version_tag": row.version_tag,
        "dataset_name": row.dataset_name,
        "eval_level": row.eval_level,
        "desensitize_policy_snapshot": row.desensitize_policy_snapshot,
        "dev_case_count": int(row.dev_case_count or 0),
        "holdout_case_count": int(row.holdout_case_count or 0),
        "total_case_count": total_cnt,
        "pending_review_count": pending_cnt,
        "status": row.status,
        "description": row.description,
        "created_by": row.created_by,
        "create_time": _fmt_dt(row.create_time),
        "update_time": _fmt_dt(row.update_time),
    }


def serialize_case(row: EvalCase) -> dict[str, Any]:
    return {
        "id": row.id,
        "uuid": row.uuid,
        "dataset_id": row.dataset_id,
        "case_code": row.case_code,
        "dataset_split": row.dataset_split,
        "source_kind": row.source_kind,
        "source_ref_json": row.source_ref_json,
        "turn_mode": row.turn_mode,
        "input_text": row.input_text,
        "conversation_turns_json": row.conversation_turns_json,
        "expected_output": row.expected_output,
        "expected_outcome": row.expected_outcome,
        "reference_contexts_json": row.reference_contexts_json,
        "expected_structured_json": row.expected_structured_json,
        "sut_input_json": row.sut_input_json,
        "sut_context_json": row.sut_context_json,
        "has_snapshot": row.sut_context_json is not None,
        "tags_json": row.tags_json,
        "difficulty": row.difficulty,
        "pii_masked": int(row.pii_masked or 0),
        "desensitize_summary_json": row.desensitize_summary_json,
        "uncertainty_score": (
            float(row.uncertainty_score)
            if row.uncertainty_score is not None
            else None
        ),
        "review_status": row.review_status,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": _fmt_dt(row.reviewed_at),
        "review_note": row.review_note,
        "create_time": _fmt_dt(row.create_time),
        "update_time": _fmt_dt(row.update_time),
    }


def get_dataset_or_404(db: Session, dataset_id: int) -> Dataset:
    row = db.scalar(
        select(Dataset).where(
            Dataset.id == dataset_id,
            Dataset.is_deleted == 0,
        )
    )
    if row is None:
        raise AppError(f"测试集不存在: {dataset_id}", status_code=404, code=404)
    return row


def get_case_or_404(db: Session, case_id: int) -> EvalCase:
    row = db.scalar(
        select(EvalCase).where(
            EvalCase.id == case_id,
            EvalCase.is_deleted == 0,
        )
    )
    if row is None:
        raise AppError(f"测试用例不存在: {case_id}", status_code=404, code=404)
    return row


def recompute_dataset_counts(db: Session, dataset_id: int) -> tuple[int, int]:
    """Recompute non-deleted APPROVED case counts per split and persist on ev_dataset."""
    dataset = db.scalar(
        select(Dataset).where(
            Dataset.id == dataset_id,
            Dataset.is_deleted == 0,
        )
    )
    if dataset is None:
        raise AppError(f"测试集不存在: {dataset_id}", status_code=404, code=404)

    db.flush()
    stmt = (
        select(EvalCase.dataset_split, func.count(EvalCase.id))
        .where(
            EvalCase.dataset_id == dataset_id,
            EvalCase.is_deleted == 0,
            EvalCase.review_status == "APPROVED",
        )
        .group_by(EvalCase.dataset_split)
    )
    counts_by_split = {
        str(split_name).upper(): int(cnt or 0)
        for split_name, cnt in db.execute(stmt).all()
    }
    dev_cnt = counts_by_split.get("DEV", 0)
    holdout_cnt = counts_by_split.get("HOLDOUT", 0)

    dataset.dev_case_count = dev_cnt
    dataset.holdout_case_count = holdout_cnt
    dataset.update_time = utcnow_naive()
    db.flush()
    return dev_cnt, holdout_cnt


def list_datasets(
    db: Session,
    *,
    eval_level: str | None = None,
    status: str | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    stmt = select(Dataset).where(Dataset.is_deleted == 0)
    if eval_level:
        stmt = stmt.where(Dataset.eval_level == eval_level.strip().upper())
    if status:
        stmt = stmt.where(Dataset.status == status.strip().upper())
    if q and q.strip():
        kw = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Dataset.dataset_code.like(kw),
                Dataset.dataset_name.like(kw),
                Dataset.version_tag.like(kw),
            )
        )
    stmt = stmt.order_by(Dataset.id.desc())
    rows = list(db.scalars(stmt).all())
    stats_map = _dataset_case_stats(db, [r.id for r in rows])
    return [serialize_dataset(r, extra_stats=stats_map.get(r.id)) for r in rows]


def get_dataset_detail(db: Session, dataset_id: int) -> dict[str, Any]:
    row = get_dataset_or_404(db, dataset_id)
    stats_map = _dataset_case_stats(db, [row.id])
    return serialize_dataset(row, extra_stats=stats_map.get(row.id))


def create_dataset(
    db: Session,
    payload: DatasetCreateRequest,
    *,
    created_by: str | None = None,
) -> dict[str, Any]:
    code = payload.dataset_code.strip()
    ver = payload.version_tag.strip()
    existing = db.scalar(
        select(Dataset).where(
            Dataset.dataset_code == code,
            Dataset.version_tag == ver,
        )
    )
    now = utcnow_naive()
    if existing is not None:
        if existing.is_deleted == 0:
            raise AppError(
                f"测试集编码与版本已存在: {code} ({ver})",
                status_code=409,
                code=409,
            )
        row = existing
        row.uuid = new_uuid_hex()
        row.dataset_name = payload.dataset_name.strip()
        row.eval_level = payload.eval_level
        row.desensitize_policy_snapshot = payload.desensitize_policy_snapshot
        row.dev_case_count = 0
        row.holdout_case_count = 0
        row.status = payload.status
        row.description = payload.description
        row.created_by = created_by
        row.is_deleted = 0
        row.create_time = now
        row.update_time = now
    else:
        row = Dataset(
            dataset_code=code,
            version_tag=ver,
            dataset_name=payload.dataset_name.strip(),
            eval_level=payload.eval_level,
            desensitize_policy_snapshot=payload.desensitize_policy_snapshot,
            dev_case_count=0,
            holdout_case_count=0,
            status=payload.status,
            description=payload.description,
            created_by=created_by,
            is_deleted=0,
            create_time=now,
            update_time=now,
        )
        db.add(row)

    db.commit()
    db.refresh(row)
    return serialize_dataset(
        row, extra_stats={"total_case_count": 0, "pending_review_count": 0}
    )


def update_dataset(
    db: Session,
    dataset_id: int,
    payload: DatasetUpdateRequest,
) -> dict[str, Any]:
    row = get_dataset_or_404(db, dataset_id)
    fields_set = payload.model_fields_set

    if "version_tag" in fields_set and payload.version_tag is not None:
        new_ver = payload.version_tag.strip()
        if new_ver != row.version_tag:
            conflict = db.scalar(
                select(Dataset).where(
                    Dataset.dataset_code == row.dataset_code,
                    Dataset.version_tag == new_ver,
                    Dataset.id != row.id,
                    Dataset.is_deleted == 0,
                )
            )
            if conflict is not None:
                raise AppError(
                    f"测试集编码与版本已存在: {row.dataset_code} ({new_ver})",
                    status_code=409,
                    code=409,
                )
            row.version_tag = new_ver

    if "dataset_name" in fields_set and payload.dataset_name is not None:
        row.dataset_name = payload.dataset_name.strip()
    if "eval_level" in fields_set and payload.eval_level is not None:
        row.eval_level = payload.eval_level
    if "desensitize_policy_snapshot" in fields_set:
        row.desensitize_policy_snapshot = payload.desensitize_policy_snapshot
    if "status" in fields_set and payload.status is not None:
        row.status = payload.status
    if "description" in fields_set:
        row.description = payload.description

    row.update_time = utcnow_naive()
    recompute_dataset_counts(db, row.id)
    db.commit()
    db.refresh(row)
    stats_map = _dataset_case_stats(db, [row.id])
    return serialize_dataset(row, extra_stats=stats_map.get(row.id))


@dataclass(slots=True)
class _DraftCaseMaskTarget:
    input_text: str
    conversation_turns_json: list[dict[str, Any]] | None = None
    expected_output: str | None = None
    expected_outcome: str | None = None
    reference_contexts_json: list[str] | None = None
    pii_masked: int = 0
    desensitize_summary_json: dict[str, int] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


async def _mask_draft_cases_inline(
    drafts: Sequence[_DraftCaseMaskTarget],
    *,
    policy_code: str,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    desensitize_client: DesensitizationClient | None = None,
) -> str:
    client = desensitize_client or DesensitizationClient(settings=settings)
    segments: list[tuple[int, str, int | None, str]] = []
    # tuple: (draft_idx, field_kind, sub_idx, raw_text)
    for d_idx, draft in enumerate(drafts):
        segments.append((d_idx, "input_text", None, draft.input_text))
        if draft.conversation_turns_json:
            for t_idx, turn in enumerate(draft.conversation_turns_json):
                if isinstance(turn, Mapping) and isinstance(turn.get("content"), str):
                    segments.append((d_idx, "turn", t_idx, turn["content"]))
        if isinstance(draft.expected_output, str) and draft.expected_output:
            segments.append((d_idx, "expected_output", None, draft.expected_output))
        if isinstance(draft.expected_outcome, str) and draft.expected_outcome:
            segments.append((d_idx, "expected_outcome", None, draft.expected_outcome))
        if draft.reference_contexts_json:
            for r_idx, ref_txt in enumerate(draft.reference_contexts_json):
                if isinstance(ref_txt, str) and ref_txt:
                    segments.append((d_idx, "ref", r_idx, ref_txt))

    if not segments:
        return f"{policy_code}:v1"

    policy_snapshot = f"{policy_code}:v1"
    masked_results = []
    try:
        for start in range(0, len(segments), _MASK_BATCH_CHUNK_SIZE):
            chunk = segments[start : start + _MASK_BATCH_CHUNK_SIZE]
            chunk_texts = [seg[3] for seg in chunk]
            chunk_ids = [str(start + offset) for offset in range(len(chunk))]
            batch_res = await client.mask_batch(
                chunk_texts,
                policy_code=policy_code,
                item_ids=chunk_ids,
                transport=transport,
            )
            policy_snapshot = batch_res.policy_snapshot
            masked_results.extend(batch_res.items)
    except DesensitizationError as exc:
        raise AppError(
            exc.message,
            status_code=exc.status_code,
            code=exc.status_code,
        ) from exc

    if len(masked_results) != len(segments):
        raise AppError(
            "脱敏返回条目总数异常，已阻断语料入库",
            status_code=502,
            code=502,
        )

    per_draft_counts: list[dict[str, int]] = [{} for _ in drafts]
    for seg_meta, masked_item in zip(segments, masked_results, strict=True):
        d_idx, kind, sub_idx, _ = seg_meta
        target = drafts[d_idx]
        if kind == "input_text":
            target.input_text = masked_item.masked_text
        elif kind == "turn" and sub_idx is not None and target.conversation_turns_json is not None:
            target.conversation_turns_json[sub_idx]["content"] = masked_item.masked_text
        elif kind == "expected_output":
            target.expected_output = masked_item.masked_text
        elif kind == "expected_outcome":
            target.expected_outcome = masked_item.masked_text
        elif kind == "ref" and sub_idx is not None and target.reference_contexts_json is not None:
            target.reference_contexts_json[sub_idx] = masked_item.masked_text

        per_draft_counts[d_idx] = merge_tag_counts(
            per_draft_counts[d_idx], masked_item.tag_counts
        )

    for d_idx, target in enumerate(drafts):
        target.pii_masked = 1
        target.desensitize_summary_json = per_draft_counts[d_idx]

    return policy_snapshot


def _upsert_or_skip_case_by_code(
    db: Session,
    *,
    dataset_id: int,
    case_code: str,
) -> tuple[EvalCase | None, bool]:
    """Return (revivable_deleted_row_or_None, should_skip_active_duplicate)."""
    existing = db.scalar(
        select(EvalCase).where(
            EvalCase.dataset_id == dataset_id,
            EvalCase.case_code == case_code,
        )
    )
    if existing is None:
        return None, False
    if existing.is_deleted == 0:
        return None, True
    return existing, False


def _parse_import_items(payload: DatasetImportRequest) -> list[CaseImportItem]:
    parsed: list[CaseImportItem] = []
    if payload.cases:
        parsed.extend(payload.cases)
    if payload.jsonl_text is not None and payload.jsonl_text.strip():
        for line_no, raw_line in enumerate(payload.jsonl_text.splitlines(), start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except ValueError as exc:
                raise AppError(
                    f"JSONL 第 {line_no} 行解析失败: {exc}",
                    status_code=400,
                    code=400,
                ) from exc
            if not isinstance(obj, Mapping):
                raise AppError(
                    f"JSONL 第 {line_no} 行必须为 JSON 对象",
                    status_code=400,
                    code=400,
                )
            try:
                parsed.append(CaseImportItem.model_validate(dict(obj)))
            except Exception as exc:
                raise AppError(
                    f"JSONL 第 {line_no} 行字段不合法: {exc}",
                    status_code=400,
                    code=400,
                ) from exc

    if not parsed:
        raise AppError("导入内容不能为空，请提供 cases 数组或非空 jsonl_text", status_code=400, code=400)
    return parsed


async def import_cases(
    db: Session,
    dataset_id: int,
    payload: DatasetImportRequest,
    *,
    operator: str | None = None,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    desensitize_client: DesensitizationClient | None = None,
) -> dict[str, Any]:
    dataset = get_dataset_or_404(db, dataset_id)
    items = _parse_import_items(payload)
    batch_default_pii = normalize_pii_masked(payload.default_pii_masked, default=0)

    drafts: list[_DraftCaseMaskTarget] = []
    seen_codes_in_batch: set[str] = set()

    for idx, item in enumerate(items):
        raw_input = item.input_text if item.input_text is not None else item.query
        if raw_input is None or not str(raw_input).strip():
            raise AppError(
                f"第 {idx + 1} 条导入用例缺少非空 input_text",
                status_code=400,
                code=400,
            )
        input_str = str(raw_input)
        code = _clean_or_generate_case_code(
            item.case_code,
            fallback_seed=f"{idx}:{input_str}",
            prefix="imp",
        )
        if code in seen_codes_in_batch:
            code = f"{code[:55]}_{idx}"
        seen_codes_in_batch.add(code)

        split_val = (
            item.dataset_split
            if item.dataset_split is not None
            else assign_dataset_split(code, dev_ratio=payload.dev_ratio)
        )
        item_pii = (
            1
            if payload.auto_desensitize
            else normalize_pii_masked(item.pii_masked, default=batch_default_pii)
        )
        _enforce_approval_pii_guard(item.review_status, item_pii)

        sanitized_summary = (
            sanitize_desensitize_summary(item.desensitize_summary_json)
            if item.desensitize_summary_json is not None
            else None
        )
        drafts.append(
            _DraftCaseMaskTarget(
                input_text=input_str,
                conversation_turns_json=copy.deepcopy(item.conversation_turns_json),
                expected_output=item.expected_output,
                expected_outcome=item.expected_outcome,
                reference_contexts_json=(
                    list(item.reference_contexts_json)
                    if item.reference_contexts_json is not None
                    else None
                ),
                pii_masked=item_pii,
                desensitize_summary_json=sanitized_summary,
                extra={
                    "case_code": code,
                    "dataset_split": split_val,
                    "source_kind": item.source_kind,
                    "source_ref_json": item.source_ref_json,
                    "turn_mode": item.turn_mode,
                    "expected_structured_json": item.expected_structured_json,
                    "sut_input_json": item.sut_input_json,
                    "sut_context_json": item.sut_context_json,
                    "tags_json": item.tags_json,
                    "difficulty": item.difficulty,
                    "uncertainty_score": item.uncertainty_score,
                    "review_status": item.review_status,
                    "review_note": item.review_note,
                },
            )
        )

    policy_snapshot: str | None = None
    if payload.auto_desensitize:
        policy_snapshot = await _mask_draft_cases_inline(
            drafts,
            policy_code=payload.desensitize_policy_code,
            settings=settings,
            transport=transport,
            desensitize_client=desensitize_client,
        )

    now = utcnow_naive()
    imported_count = 0
    skipped_count = 0
    dev_imported = 0
    holdout_imported = 0

    for draft in drafts:
        meta = draft.extra
        code = meta["case_code"]
        revivable, should_skip = _upsert_or_skip_case_by_code(
            db, dataset_id=dataset.id, case_code=code
        )
        if should_skip:
            skipped_count += 1
            continue

        rev_status = meta["review_status"]
        _enforce_approval_pii_guard(rev_status, draft.pii_masked)
        reviewed_by = operator if rev_status == "APPROVED" else None
        reviewed_at = now if rev_status == "APPROVED" else None

        if revivable is not None:
            row = revivable
            row.uuid = new_uuid_hex()
            row.dataset_split = meta["dataset_split"]
            row.source_kind = meta["source_kind"]
            row.source_ref_json = meta["source_ref_json"]
            row.turn_mode = meta["turn_mode"]
            row.input_text = draft.input_text
            row.conversation_turns_json = draft.conversation_turns_json
            row.expected_output = draft.expected_output
            row.expected_outcome = draft.expected_outcome
            row.reference_contexts_json = draft.reference_contexts_json
            row.expected_structured_json = meta["expected_structured_json"]
            row.sut_input_json = meta["sut_input_json"]
            row.sut_context_json = meta["sut_context_json"]
            row.tags_json = meta["tags_json"]
            row.difficulty = meta["difficulty"]
            row.pii_masked = int(draft.pii_masked)
            row.desensitize_summary_json = draft.desensitize_summary_json
            row.uncertainty_score = meta["uncertainty_score"]
            row.review_status = rev_status
            row.reviewed_by = reviewed_by
            row.reviewed_at = reviewed_at
            row.review_note = meta["review_note"]
            row.is_deleted = 0
            row.create_time = now
            row.update_time = now
        else:
            row = EvalCase(
                dataset_id=dataset.id,
                case_code=code,
                dataset_split=meta["dataset_split"],
                source_kind=meta["source_kind"],
                source_ref_json=meta["source_ref_json"],
                turn_mode=meta["turn_mode"],
                input_text=draft.input_text,
                conversation_turns_json=draft.conversation_turns_json,
                expected_output=draft.expected_output,
                expected_outcome=draft.expected_outcome,
                reference_contexts_json=draft.reference_contexts_json,
                expected_structured_json=meta["expected_structured_json"],
                sut_input_json=meta["sut_input_json"],
                sut_context_json=meta["sut_context_json"],
                tags_json=meta["tags_json"],
                difficulty=meta["difficulty"],
                pii_masked=int(draft.pii_masked),
                desensitize_summary_json=draft.desensitize_summary_json,
                uncertainty_score=meta["uncertainty_score"],
                review_status=rev_status,
                reviewed_by=reviewed_by,
                reviewed_at=reviewed_at,
                review_note=meta["review_note"],
                is_deleted=0,
                create_time=now,
                update_time=now,
            )
            db.add(row)

        imported_count += 1
        if meta["dataset_split"] == "DEV":
            dev_imported += 1
        else:
            holdout_imported += 1

    if policy_snapshot is not None:
        dataset.desensitize_policy_snapshot = policy_snapshot

    dev_approved, holdout_approved = recompute_dataset_counts(db, dataset.id)
    db.commit()

    return {
        "dataset_id": dataset.id,
        "imported_count": imported_count,
        "skipped_count": skipped_count,
        "dev_imported_count": dev_imported,
        "holdout_imported_count": holdout_imported,
        "dev_case_count": dev_approved,
        "holdout_case_count": holdout_approved,
        "desensitize_policy_snapshot": dataset.desensitize_policy_snapshot,
    }


async def ingest_from_mysql(
    db: Session,
    dataset_id: int,
    payload: MysqlIngestRequest,
    *,
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    desensitize_client: DesensitizationClient | None = None,
    allowed_env_names: Collection[str] | None = None,
    source_engine: Engine | None = None,
    source_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    dataset = get_dataset_or_404(db, dataset_id)
    extracted = extract_source_records(
        source_dsn_env=payload.source_dsn_env,
        preset_extractor=payload.preset_extractor,
        ingest_filter=payload.filter,
        allowed_env_names=allowed_env_names,
        source_engine=source_engine,
        source_rows=source_rows,
    )
    if not extracted:
        return {
            "dataset_id": dataset.id,
            "ingested_count": 0,
            "skipped_count": 0,
            "dev_ingested_count": 0,
            "holdout_ingested_count": 0,
            "desensitize_policy_snapshot": dataset.desensitize_policy_snapshot,
        }

    drafts: list[_DraftCaseMaskTarget] = []
    for rec in extracted:
        drafts.append(
            _DraftCaseMaskTarget(
                input_text=rec.input_text,
                conversation_turns_json=copy.deepcopy(rec.conversation_turns_json),
                expected_output=rec.expected_output,
                reference_contexts_json=(
                    list(rec.reference_contexts_json)
                    if rec.reference_contexts_json is not None
                    else None
                ),
                pii_masked=0,
                extra={"record": rec},
            )
        )

    # CRITICAL INVARIANT: All records are masked in-memory BEFORE any EvalCase touches kd_eval.
    policy_snapshot = await _mask_draft_cases_inline(
        drafts,
        policy_code=payload.desensitize_policy_code,
        settings=settings,
        transport=transport,
        desensitize_client=desensitize_client,
    )

    now = utcnow_naive()
    ingested_count = 0
    skipped_count = 0
    dev_ingested = 0
    holdout_ingested = 0

    try:
        for draft in drafts:
            if int(draft.pii_masked) != 1:
                raise AppError(
                    "检测到未完成脱敏校验的语料记录(pii_masked != 1)，已强制阻断落库",
                    status_code=500,
                    code=500,
                )
            rec = draft.extra["record"]
            revivable, should_skip = _upsert_or_skip_case_by_code(
                db, dataset_id=dataset.id, case_code=rec.case_code
            )
            if should_skip:
                skipped_count += 1
                continue

            split_val = assign_dataset_split(rec.case_code, dev_ratio=payload.dev_ratio)
            if revivable is not None:
                row = revivable
                row.uuid = new_uuid_hex()
                row.dataset_split = split_val
                row.source_kind = "ONLINE_PULL"
                row.source_ref_json = rec.source_ref_json
                row.turn_mode = rec.turn_mode
                row.input_text = draft.input_text
                row.conversation_turns_json = draft.conversation_turns_json
                row.expected_output = draft.expected_output
                row.expected_outcome = None
                row.reference_contexts_json = draft.reference_contexts_json
                row.expected_structured_json = rec.expected_structured_json
                row.sut_input_json = rec.sut_input_json
                row.sut_context_json = None
                row.tags_json = rec.tags_json
                row.difficulty = "NORMAL"
                row.pii_masked = 1
                row.desensitize_summary_json = draft.desensitize_summary_json
                row.uncertainty_score = None
                row.review_status = "PENDING_REVIEW"
                row.reviewed_by = None
                row.reviewed_at = None
                row.review_note = None
                row.is_deleted = 0
                row.create_time = now
                row.update_time = now
            else:
                row = EvalCase(
                    dataset_id=dataset.id,
                    case_code=rec.case_code,
                    dataset_split=split_val,
                    source_kind="ONLINE_PULL",
                    source_ref_json=rec.source_ref_json,
                    turn_mode=rec.turn_mode,
                    input_text=draft.input_text,
                    conversation_turns_json=draft.conversation_turns_json,
                    expected_output=draft.expected_output,
                    expected_outcome=None,
                    reference_contexts_json=draft.reference_contexts_json,
                    expected_structured_json=rec.expected_structured_json,
                    sut_input_json=rec.sut_input_json,
                    sut_context_json=None,
                    tags_json=rec.tags_json,
                    difficulty="NORMAL",
                    pii_masked=1,
                    desensitize_summary_json=draft.desensitize_summary_json,
                    uncertainty_score=None,
                    review_status="PENDING_REVIEW",
                    is_deleted=0,
                    create_time=now,
                    update_time=now,
                )
                db.add(row)

            ingested_count += 1
            if split_val == "DEV":
                dev_ingested += 1
            else:
                holdout_ingested += 1

        dataset.desensitize_policy_snapshot = policy_snapshot
        recompute_dataset_counts(db, dataset.id)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "dataset_id": dataset.id,
        "ingested_count": ingested_count,
        "skipped_count": skipped_count,
        "dev_ingested_count": dev_ingested,
        "holdout_ingested_count": holdout_ingested,
        "desensitize_policy_snapshot": dataset.desensitize_policy_snapshot,
    }


def _resolve_component_adapter(
    db: Session, payload: RecordComponentSnapshotsRequest
) -> SutAdapter:
    code = (payload.adapter_code or payload.sut_adapter_code or "").strip()
    if payload.adapter_id is not None:
        adapter = db.scalar(
            select(SutAdapter).where(
                SutAdapter.id == payload.adapter_id,
                SutAdapter.is_deleted == 0,
            )
        )
        if adapter is None:
            raise AppError(
                f"被测适配器不存在: id={payload.adapter_id}",
                status_code=404,
                code=404,
            )
    elif code:
        adapter = db.scalar(
            select(SutAdapter).where(
                SutAdapter.adapter_code == code,
                SutAdapter.is_deleted == 0,
            )
        )
        if adapter is None:
            raise AppError(
                f"被测适配器不存在: {code}",
                status_code=404,
                code=404,
            )
    else:
        raise AppError(
            "必须指定 adapter_code 或 adapter_id",
            status_code=400,
            code=400,
        )

    if adapter.adapter_type.upper() != "COMPONENT_HARNESS":
        raise AppError(
            f"仅 COMPONENT_HARNESS 类型适配器支持组件快照录制，当前类型: {adapter.adapter_type}",
            status_code=400,
            code=400,
        )
    return adapter


async def record_dataset_component_snapshots(
    db: Session,
    dataset_id: int,
    payload: RecordComponentSnapshotsRequest,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    dataset = get_dataset_or_404(db, dataset_id)
    if dataset.eval_level.upper() != "COMPONENT":
        raise AppError(
            f"仅 eval_level=COMPONENT 的测试集支持录制组件回放快照，当前层级: {dataset.eval_level}",
            status_code=400,
            code=400,
        )

    adapter = _resolve_component_adapter(db, payload)

    stmt = select(EvalCase).where(
        EvalCase.dataset_id == dataset.id,
        EvalCase.is_deleted == 0,
    )
    if payload.case_ids:
        stmt = stmt.where(EvalCase.id.in_(payload.case_ids))
    if payload.split:
        stmt = stmt.where(EvalCase.dataset_split == payload.split)
    if payload.only_missing:
        stmt = stmt.where(
            or_(
                EvalCase.sut_context_json.is_(None),
                EvalCase.sut_context_json == SQLJSON.NULL,
            )
        )
    stmt = stmt.order_by(EvalCase.id.asc())

    cases = list(db.scalars(stmt).all())
    recorded_count = 0
    now = utcnow_naive()

    try:
        for case_row in cases:
            try:
                rec_res = await record_component_snapshot(
                    adapter,
                    case_row,
                    transport=transport,
                )
            except SutExecutionError as exc:
                raise AppError(
                    exc.message,
                    status_code=502,
                    code=502,
                    data={
                        "case_id": case_row.id,
                        "case_code": case_row.case_code,
                        "request_preview": exc.request_preview,
                        "upstream_status_code": exc.status_code,
                    },
                ) from exc

            case_row.sut_context_json = rec_res.sut_context
            case_row.source_kind = "HARNESS_RECORD"
            case_row.update_time = now
            recorded_count += 1

        db.commit()
    except Exception:
        db.rollback()
        raise

    for c in cases:
        db.refresh(c)

    return {
        "dataset_id": dataset.id,
        "adapter_code": adapter.adapter_code,
        "total_targeted": len(cases),
        "recorded_count": recorded_count,
        "cases": [serialize_case(c) for c in cases],
    }


def list_dataset_cases(
    db: Session,
    dataset_id: int,
    *,
    split: str | None = None,
    review_status: str | None = None,
    pii_masked: int | bool | None = None,
    has_snapshot: bool | None = None,
    q: str | None = None,
    sort: str = "id_asc",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    get_dataset_or_404(db, dataset_id)

    filters = [
        EvalCase.dataset_id == dataset_id,
        EvalCase.is_deleted == 0,
    ]
    if split:
        filters.append(EvalCase.dataset_split == split.strip().upper())
    if review_status:
        filters.append(EvalCase.review_status == review_status.strip().upper())
    if pii_masked is not None:
        filters.append(EvalCase.pii_masked == normalize_pii_masked(pii_masked))
    if has_snapshot is True:
        filters.append(
            and_(
                EvalCase.sut_context_json.is_not(None),
                EvalCase.sut_context_json != SQLJSON.NULL,
            )
        )
    elif has_snapshot is False:
        filters.append(
            or_(
                EvalCase.sut_context_json.is_(None),
                EvalCase.sut_context_json == SQLJSON.NULL,
            )
        )
    if q and q.strip():
        kw = f"%{q.strip()}%"
        filters.append(
            or_(
                EvalCase.case_code.like(kw),
                EvalCase.input_text.like(kw),
                EvalCase.expected_output.like(kw),
            )
        )

    total = int(
        db.scalar(select(func.count(EvalCase.id)).where(*filters)) or 0
    )

    stmt = select(EvalCase).where(*filters)
    sort_key = (sort or "id_asc").strip().lower()
    if sort_key == "uncertainty_desc":
        null_rank = sql_case((EvalCase.uncertainty_score.is_(None), 1), else_=0)
        stmt = stmt.order_by(
            null_rank.asc(),
            EvalCase.uncertainty_score.desc(),
            EvalCase.id.asc(),
        )
    elif sort_key == "uncertainty_asc":
        null_rank = sql_case((EvalCase.uncertainty_score.is_(None), 1), else_=0)
        stmt = stmt.order_by(
            null_rank.asc(),
            EvalCase.uncertainty_score.asc(),
            EvalCase.id.asc(),
        )
    elif sort_key in {"id_desc", "create_time_desc"}:
        stmt = stmt.order_by(EvalCase.id.desc())
    else:
        stmt = stmt.order_by(EvalCase.id.asc())

    offset_val = max(0, (page - 1) * page_size)
    stmt = stmt.offset(offset_val).limit(page_size)
    rows = list(db.scalars(stmt).all())

    return {
        "items": [serialize_case(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def create_manual_case(
    db: Session,
    dataset_id: int,
    payload: CaseCreateRequest,
    *,
    operator: str | None = None,
) -> dict[str, Any]:
    dataset = get_dataset_or_404(db, dataset_id)
    code = _clean_or_generate_case_code(
        payload.case_code,
        fallback_seed=f"{uuid.uuid4().hex}:{payload.input_text}",
        prefix="manual",
    )
    revivable, is_active_dup = _upsert_or_skip_case_by_code(
        db, dataset_id=dataset.id, case_code=code
    )
    if is_active_dup:
        raise AppError(
            f"测试集内用例编码已存在: {code}",
            status_code=409,
            code=409,
        )

    pii_val = normalize_pii_masked(payload.pii_masked, default=0)
    _enforce_approval_pii_guard(payload.review_status, pii_val)

    split_val = (
        payload.dataset_split
        if payload.dataset_split is not None
        else assign_dataset_split(code, dev_ratio=0.5)
    )
    sanitized_summary = (
        sanitize_desensitize_summary(payload.desensitize_summary_json)
        if payload.desensitize_summary_json is not None
        else None
    )

    now = utcnow_naive()
    reviewed_by = operator if payload.review_status == "APPROVED" else None
    reviewed_at = now if payload.review_status == "APPROVED" else None

    if revivable is not None:
        row = revivable
        row.uuid = new_uuid_hex()
        row.dataset_split = split_val
        row.source_kind = payload.source_kind
        row.source_ref_json = payload.source_ref_json
        row.turn_mode = payload.turn_mode
        row.input_text = payload.input_text
        row.conversation_turns_json = payload.conversation_turns_json
        row.expected_output = payload.expected_output
        row.expected_outcome = payload.expected_outcome
        row.reference_contexts_json = payload.reference_contexts_json
        row.expected_structured_json = payload.expected_structured_json
        row.sut_input_json = payload.sut_input_json
        row.sut_context_json = payload.sut_context_json
        row.tags_json = payload.tags_json
        row.difficulty = payload.difficulty
        row.pii_masked = pii_val
        row.desensitize_summary_json = sanitized_summary
        row.uncertainty_score = payload.uncertainty_score
        row.review_status = payload.review_status
        row.reviewed_by = reviewed_by
        row.reviewed_at = reviewed_at
        row.review_note = payload.review_note
        row.is_deleted = 0
        row.create_time = now
        row.update_time = now
    else:
        row = EvalCase(
            dataset_id=dataset.id,
            case_code=code,
            dataset_split=split_val,
            source_kind=payload.source_kind,
            source_ref_json=payload.source_ref_json,
            turn_mode=payload.turn_mode,
            input_text=payload.input_text,
            conversation_turns_json=payload.conversation_turns_json,
            expected_output=payload.expected_output,
            expected_outcome=payload.expected_outcome,
            reference_contexts_json=payload.reference_contexts_json,
            expected_structured_json=payload.expected_structured_json,
            sut_input_json=payload.sut_input_json,
            sut_context_json=payload.sut_context_json,
            tags_json=payload.tags_json,
            difficulty=payload.difficulty,
            pii_masked=pii_val,
            desensitize_summary_json=sanitized_summary,
            uncertainty_score=payload.uncertainty_score,
            review_status=payload.review_status,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
            review_note=payload.review_note,
            is_deleted=0,
            create_time=now,
            update_time=now,
        )
        db.add(row)

    recompute_dataset_counts(db, dataset.id)
    db.commit()
    db.refresh(row)
    return serialize_case(row)


def get_case_detail(db: Session, case_id: int) -> dict[str, Any]:
    row = get_case_or_404(db, case_id)
    return serialize_case(row)


def curate_case(
    db: Session,
    case_id: int,
    payload: CaseCurateRequest,
    *,
    reviewer: str | None = None,
) -> dict[str, Any]:
    row = get_case_or_404(db, case_id)
    fields_set = payload.model_fields_set

    resulting_pii = (
        normalize_pii_masked(payload.pii_masked, default=int(row.pii_masked))
        if "pii_masked" in fields_set and payload.pii_masked is not None
        else int(row.pii_masked)
    )
    resulting_status = (
        payload.review_status
        if "review_status" in fields_set and payload.review_status is not None
        else row.review_status
    )
    _enforce_approval_pii_guard(resulting_status, resulting_pii)

    if "input_text" in fields_set and payload.input_text is not None:
        row.input_text = payload.input_text
    if "conversation_turns_json" in fields_set:
        row.conversation_turns_json = payload.conversation_turns_json
    if "expected_output" in fields_set:
        row.expected_output = payload.expected_output
    if "expected_outcome" in fields_set:
        row.expected_outcome = payload.expected_outcome
    if "reference_contexts_json" in fields_set:
        row.reference_contexts_json = payload.reference_contexts_json
    if "expected_structured_json" in fields_set:
        row.expected_structured_json = payload.expected_structured_json
    if "sut_input_json" in fields_set:
        row.sut_input_json = payload.sut_input_json
    if "sut_context_json" in fields_set:
        row.sut_context_json = payload.sut_context_json
    if "tags_json" in fields_set:
        row.tags_json = payload.tags_json
    if "dataset_split" in fields_set and payload.dataset_split is not None:
        row.dataset_split = payload.dataset_split
    if "difficulty" in fields_set and payload.difficulty is not None:
        row.difficulty = payload.difficulty
    if "pii_masked" in fields_set and payload.pii_masked is not None:
        row.pii_masked = resulting_pii
    if "uncertainty_score" in fields_set:
        row.uncertainty_score = payload.uncertainty_score
    if "review_status" in fields_set and payload.review_status is not None:
        row.review_status = payload.review_status
    if "review_note" in fields_set:
        row.review_note = payload.review_note

    now = utcnow_naive()
    row.reviewed_by = reviewer
    row.reviewed_at = now
    row.update_time = now

    recompute_dataset_counts(db, row.dataset_id)
    db.commit()
    db.refresh(row)
    return serialize_case(row)


def delete_case(db: Session, case_id: int) -> dict[str, Any]:
    row = get_case_or_404(db, case_id)
    row.is_deleted = 1
    row.update_time = utcnow_naive()
    dev_cnt, holdout_cnt = recompute_dataset_counts(db, row.dataset_id)
    db.commit()
    return {
        "deleted_case_id": row.id,
        "dataset_id": row.dataset_id,
        "dev_case_count": dev_cnt,
        "holdout_case_count": holdout_cnt,
    }
