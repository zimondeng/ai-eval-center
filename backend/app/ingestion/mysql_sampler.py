from __future__ import annotations

import json
import os
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Engine, MetaData, Table, create_engine, func, inspect, select
from sqlalchemy.engine import make_url

from app.common.crypto import sha256_hex
from app.common.errors import AppError
from app.ingestion.schemas import IngestFilter

_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,95}$")
_SAFE_CODE_RE = re.compile(r"[^A-Za-z0-9_.-]+")

PRESET_CANDIDATE_TABLES: dict[str, tuple[str, ...]] = {
    "gov_routing_conversation": (
        "gov_routing_conversation",
        "ai_routing_audit_log",
        "routing_conversation_log",
        "ai_chat_record",
        "chat_conversation_log",
    ),
    "generic_conversation": (
        "generic_conversation",
        "conversation_log",
        "qa_log",
        "gov_routing_conversation",
    ),
}

_ROUTING_METADATA_COLS = (
    "selected_region",
    "route_mode",
    "target_agent_id",
    "app_code",
    "component",
)


@dataclass(slots=True)
class ExtractedSourceRecord:
    case_code: str
    turn_mode: str
    input_text: str
    conversation_turns_json: list[dict[str, Any]] | None
    expected_output: str | None
    reference_contexts_json: list[str] | None
    expected_structured_json: dict[str, Any] | None
    sut_input_json: dict[str, Any] | None
    source_ref_json: dict[str, Any]
    tags_json: list[str] | None


def validate_readonly_dsn_env_name(
    source_dsn_env: str,
    *,
    allowed_env_names: Collection[str] | None = None,
) -> str:
    cleaned = (source_dsn_env or "").strip()
    if not _ENV_NAME_RE.match(cleaned):
        raise AppError(
            f"非法的只读数据源环境变量名: {source_dsn_env}",
            status_code=400,
            code=400,
        )
    allowed_set = set(allowed_env_names or ())
    if not cleaned.endswith("_READONLY_DSN") and cleaned not in allowed_set:
        raise AppError(
            "数据源环境变量名必须以 _READONLY_DSN 结尾以确保只读源隔离",
            status_code=400,
            code=400,
        )
    return cleaned


def _validate_readonly_dsn(dsn: str) -> str:
    cleaned = dsn.strip()
    if not cleaned:
        raise AppError("只读数据源 DSN 不能为空", status_code=400, code=400)
    try:
        parsed = make_url(cleaned)
    except Exception as exc:
        raise AppError("只读数据源 DSN 格式不合法", status_code=400, code=400) from exc

    backend = parsed.get_backend_name().lower()
    if backend not in {"mysql", "sqlite"}:
        raise AppError(
            f"仅支持只读 MySQL (或测试 SQLite) 数据源，当前驱动: {backend}",
            status_code=400,
            code=400,
        )
    db_name = (parsed.database or "").lower()
    if backend == "mysql" and db_name == "kd_eval":
        raise AppError(
            "禁止跨库或直接将评测中心库 kd_eval 作为外部抽样源",
            status_code=400,
            code=400,
        )
    return cleaned


def _parse_iso_dt(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        if raw.tzinfo is not None:
            return raw.astimezone(timezone.utc).replace(tzinfo=None)
        return raw
    text = str(raw).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise AppError(
            f"时间过滤参数格式不合法，需为 ISO-8601 格式: {text}",
            status_code=400,
            code=400,
        ) from exc
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _parse_json_if_str(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        stripped = val.strip()
        if not stripped:
            return None
        try:
            return json.loads(stripped)
        except ValueError:
            return None
    return None


def _sanitize_case_code(raw_id: str, prefix: str = "pull") -> str:
    cleaned = _SAFE_CODE_RE.sub("_", raw_id.strip()).strip("_")
    if not cleaned:
        cleaned = sha256_hex(raw_id)[:12]
    candidate = cleaned if cleaned.startswith(f"{prefix}_") else f"{prefix}_{cleaned}"
    if len(candidate) <= 64:
        return candidate
    return f"{prefix}_{sha256_hex(raw_id)[:16]}"


def _normalize_row_to_record(
    row: Mapping[str, Any],
    *,
    preset_extractor: str,
    idx: int,
) -> ExtractedSourceRecord | None:
    raw_input = (
        row.get("input_text")
        if row.get("input_text") is not None
        else (
            row.get("query")
            if row.get("query") is not None
            else row.get("user_query")
        )
    )
    if raw_input is None or not str(raw_input).strip():
        return None
    input_text = str(raw_input)

    req_id_raw = (
        row.get("request_id")
        or row.get("trace_id")
        or row.get("conversation_id")
        or row.get("id")
        or f"row_{idx}"
    )
    req_id_str = str(req_id_raw).strip()

    explicit_case_code = row.get("case_code")
    if isinstance(explicit_case_code, str) and explicit_case_code.strip():
        case_code = _SAFE_CODE_RE.sub("_", explicit_case_code.strip())[:64]
    else:
        case_code = _sanitize_case_code(req_id_str, prefix="pull")

    turns_raw = _parse_json_if_str(
        row.get("conversation_turns_json")
        if row.get("conversation_turns_json") is not None
        else row.get("turns_json")
    )
    turns: list[dict[str, Any]] | None = None
    if isinstance(turns_raw, list):
        cleaned_turns: list[dict[str, Any]] = []
        for t in turns_raw:
            if isinstance(t, Mapping) and "content" in t:
                cleaned_turns.append(
                    {
                        "role": str(t.get("role") or "user"),
                        "content": str(t.get("content") or ""),
                    }
                )
        if cleaned_turns:
            turns = cleaned_turns

    turn_mode_raw = str(row.get("turn_mode") or "").strip().upper()
    if turn_mode_raw in {"SINGLE_TURN", "MULTI_TURN"}:
        turn_mode = turn_mode_raw
    else:
        turn_mode = "MULTI_TURN" if turns else "SINGLE_TURN"

    exp_raw = (
        row.get("expected_output")
        if row.get("expected_output") is not None
        else (
            row.get("reference_answer")
            if row.get("reference_answer") is not None
            else row.get("answer_text")
        )
    )
    expected_output = str(exp_raw) if exp_raw is not None and str(exp_raw).strip() else None

    refs_parsed = _parse_json_if_str(
        row.get("reference_contexts_json")
        if row.get("reference_contexts_json") is not None
        else row.get("references_json")
    )
    refs: list[str] | None = None
    if isinstance(refs_parsed, list):
        refs = [str(item) for item in refs_parsed if item is not None]

    sut_in_parsed = _parse_json_if_str(row.get("sut_input_json"))
    sut_input: dict[str, Any] = (
        dict(sut_in_parsed) if isinstance(sut_in_parsed, Mapping) else {}
    )
    for col_name in _ROUTING_METADATA_COLS:
        col_val = row.get(col_name)
        if col_val is not None and col_val != "" and col_name not in sut_input:
            sut_input[col_name] = col_val

    exp_struct_parsed = _parse_json_if_str(row.get("expected_structured_json"))
    exp_struct = (
        dict(exp_struct_parsed) if isinstance(exp_struct_parsed, Mapping) else None
    )

    mining_sig = row.get("mining_signal")
    source_ref: dict[str, Any] = {
        "preset_extractor": preset_extractor,
        "request_id": req_id_str,
    }
    if mining_sig is not None and str(mining_sig).strip():
        source_ref["mining_signal"] = str(mining_sig).strip()

    tags_parsed = _parse_json_if_str(row.get("tags_json"))
    tags: list[str] = (
        [str(x) for x in tags_parsed if x is not None]
        if isinstance(tags_parsed, list)
        else []
    )
    if mining_sig is not None and str(mining_sig).strip():
        sig_tag = f"MINING:{str(mining_sig).strip()}"
        if sig_tag not in tags:
            tags.append(sig_tag)

    return ExtractedSourceRecord(
        case_code=case_code,
        turn_mode=turn_mode,
        input_text=input_text,
        conversation_turns_json=turns,
        expected_output=expected_output,
        reference_contexts_json=refs,
        expected_structured_json=exp_struct,
        sut_input_json=sut_input or None,
        source_ref_json=source_ref,
        tags_json=tags or None,
    )


def _filter_rows_in_python(
    rows: Sequence[Mapping[str, Any]],
    *,
    start_dt: datetime | None,
    end_dt: datetime | None,
    mining_signals: set[str] | None,
    limit: int,
) -> list[Mapping[str, Any]]:
    matched: list[Mapping[str, Any]] = []
    for row in rows:
        if mining_signals is not None and "mining_signal" in row:
            sig_val = str(row.get("mining_signal") or "").strip().upper()
            if sig_val not in mining_signals:
                continue

        if start_dt is not None or end_dt is not None:
            raw_ts = (
                row.get("create_time")
                if row.get("create_time") is not None
                else row.get("created_at")
            )
            if raw_ts is not None:
                row_dt = _parse_iso_dt(raw_ts)
                if row_dt is not None:
                    if start_dt is not None and row_dt < start_dt:
                        continue
                    if end_dt is not None and row_dt > end_dt:
                        continue

        matched.append(row)
        if len(matched) >= limit:
            break
    return matched


def extract_source_records(
    *,
    source_dsn_env: str,
    preset_extractor: str,
    ingest_filter: IngestFilter,
    allowed_env_names: Collection[str] | None = None,
    source_engine: Engine | None = None,
    source_rows: Sequence[Mapping[str, Any]] | None = None,
) -> list[ExtractedSourceRecord]:
    env_key = validate_readonly_dsn_env_name(
        source_dsn_env, allowed_env_names=allowed_env_names
    )
    preset = (preset_extractor or "").strip()
    if preset not in PRESET_CANDIDATE_TABLES:
        raise AppError(
            f"不支持的预置提取器: {preset_extractor}",
            status_code=400,
            code=400,
        )

    start_dt = _parse_iso_dt(ingest_filter.start_time)
    end_dt = _parse_iso_dt(ingest_filter.end_time)
    if start_dt is not None and end_dt is not None and start_dt > end_dt:
        raise AppError("start_time 不能晚于 end_time", status_code=400, code=400)

    signals_set: set[str] | None = None
    if ingest_filter.mining_signals:
        cleaned_sigs = {
            s.strip().upper() for s in ingest_filter.mining_signals if s and s.strip()
        }
        if cleaned_sigs:
            signals_set = cleaned_sigs

    if source_rows is not None:
        raw_filtered = _filter_rows_in_python(
            source_rows,
            start_dt=start_dt,
            end_dt=end_dt,
            mining_signals=signals_set,
            limit=ingest_filter.limit,
        )
        records: list[ExtractedSourceRecord] = []
        for idx, row_map in enumerate(raw_filtered):
            rec = _normalize_row_to_record(
                row_map,
                preset_extractor=preset,
                idx=idx,
            )
            if rec is not None:
                records.append(rec)
        return records

    created_local_engine = False
    engine = source_engine
    if engine is None:
        raw_dsn = os.environ.get(env_key, "").strip()
        if not raw_dsn:
            raise AppError(
                f"未配置只读数据源环境变量: {env_key}",
                status_code=400,
                code=400,
            )
        validated_dsn = _validate_readonly_dsn(raw_dsn)
        engine = create_engine(validated_dsn, pool_pre_ping=True)
        created_local_engine = True

    records = []
    try:
        inspector = inspect(engine)
        available_tables = set(inspector.get_table_names())
        chosen_table: str | None = None
        for candidate in PRESET_CANDIDATE_TABLES[preset]:
            if candidate in available_tables:
                chosen_table = candidate
                break
        if chosen_table is None:
            raise AppError(
                f"只读源库中未找到提取器 '{preset}' 所需的数据表",
                status_code=400,
                code=400,
            )

        metadata = MetaData()
        tbl = Table(chosen_table, metadata, autoload_with=engine)
        stmt = select(tbl)

        if signals_set is not None and "mining_signal" in tbl.c:
            stmt = stmt.where(tbl.c.mining_signal.in_(sorted(signals_set)))

        ts_col = (
            tbl.c.create_time
            if "create_time" in tbl.c
            else (tbl.c.created_at if "created_at" in tbl.c else None)
        )
        if ts_col is not None and (start_dt is not None or end_dt is not None):
            if engine.dialect.name == "sqlite" and not isinstance(ts_col.type, DateTime):
                norm_ts = func.replace(ts_col, "T", " ")
                if start_dt is not None:
                    stmt = stmt.where(norm_ts >= start_dt.isoformat(sep=" "))
                if end_dt is not None:
                    stmt = stmt.where(norm_ts <= end_dt.isoformat(sep=" "))
            else:
                if start_dt is not None:
                    stmt = stmt.where(ts_col >= start_dt)
                if end_dt is not None:
                    stmt = stmt.where(ts_col <= end_dt)

        for input_col_name in ("input_text", "query", "user_query"):
            if input_col_name in tbl.c:
                in_col = tbl.c[input_col_name]
                stmt = stmt.where(in_col.is_not(None), in_col != "")
                break

        order_cols = []
        if "create_time" in tbl.c:
            order_cols.append(tbl.c.create_time.asc())
        elif "created_at" in tbl.c:
            order_cols.append(tbl.c.created_at.asc())
        if "id" in tbl.c:
            order_cols.append(tbl.c.id.asc())
        if order_cols:
            stmt = stmt.order_by(*order_cols)

        has_unpushed_mining = signals_set is not None and "mining_signal" not in tbl.c
        has_unpushed_time = (start_dt is not None or end_dt is not None) and ts_col is None
        if not has_unpushed_mining and not has_unpushed_time and ingest_filter.limit > 0:
            stmt = stmt.limit(int(ingest_filter.limit))

        with engine.connect() as conn:
            if engine.dialect.name == "mysql":
                conn.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")
            elif engine.dialect.name == "sqlite":
                conn.exec_driver_sql("PRAGMA query_only = ON")

            stream_result = (
                conn.execution_options(stream_results=True).execute(stmt).yield_per(200)
            )
            for idx, row in enumerate(stream_result):
                row_map = dict(row._mapping)
                if has_unpushed_mining or has_unpushed_time:
                    if not _filter_rows_in_python(
                        [row_map],
                        start_dt=start_dt,
                        end_dt=end_dt,
                        mining_signals=signals_set,
                        limit=1,
                    ):
                        continue
                rec = _normalize_row_to_record(
                    row_map,
                    preset_extractor=preset,
                    idx=idx,
                )
                if rec is not None:
                    records.append(rec)
                    if len(records) >= ingest_filter.limit:
                        break
    finally:
        if created_local_engine and engine is not None:
            engine.dispose()

    return records
