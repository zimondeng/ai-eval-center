from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.ingestion import (
    DesensitizationClient,
    DesensitizationError,
    assign_dataset_split,
    sanitize_desensitize_summary,
)


def test_assign_dataset_split_deterministic_boundaries_and_balance() -> None:
    # Deterministic across repeated invocations for identical seed keys
    first_pass = [assign_dataset_split(f"gov_case_{i:04d}", dev_ratio=0.5) for i in range(200)]
    second_pass = [assign_dataset_split(f"gov_case_{i:04d}", dev_ratio=0.5) for i in range(200)]
    assert first_pass == second_pass
    assert set(first_pass) == {"DEV", "HOLDOUT"}

    dev_count = sum(1 for s in first_pass if s == "DEV")
    # 200 SHA-256 samples at p=0.5 should comfortably fall within [70, 130]
    assert 70 <= dev_count <= 130

    # Boundary ratios: 0.0 -> all HOLDOUT, 1.0 -> all DEV
    assert all(assign_dataset_split(f"k_{i}", dev_ratio=0.0) == "HOLDOUT" for i in range(30))
    assert all(assign_dataset_split(f"k_{i}", dev_ratio=1.0) == "DEV" for i in range(30))

    # Monotonic subset property: increasing dev_ratio never moves a DEV case back to HOLDOUT
    for i in range(100):
        key = f"monotonic_case_{i}"
        if assign_dataset_split(key, dev_ratio=0.3) == "DEV":
            assert assign_dataset_split(key, dev_ratio=0.7) == "DEV"

    # Out-of-range ratios rejected
    with pytest.raises(ValueError):
        assign_dataset_split("bad_low", dev_ratio=-0.01)
    with pytest.raises(ValueError):
        assign_dataset_split("bad_high", dev_ratio=1.01)


async def test_desensitization_client_masks_batch_strips_pii_from_summary_and_fails_closed() -> None:
    raw_phone = "13812345678"
    raw_id_card = "320402199001011234"
    raw_name = "张伟"
    captured_requests: list[tuple[str, dict[str, str], dict[str, Any]]] = []

    def mask_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured_requests.append((str(request.url), dict(request.headers), body))
        policy = body.get("policy_code")
        if policy == "trigger_http_500":
            return httpx.Response(500, text="desensitize engine down")
        if policy == "trigger_missing_masked_text":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "policy_snapshot": "gov_eval_corpus:v1",
                        "items": [{"id": "0", "summary": {"PHONE": 1}}],
                    },
                },
            )
        if policy == "trigger_count_mismatch":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "policy_snapshot": "gov_eval_corpus:v1",
                        "items": [],
                    },
                },
            )

        out_items = []
        for entry in body["items"]:
            masked = (
                entry["text"]
                .replace(raw_phone, "<PHONE>")
                .replace(raw_id_card, "<ID_CARD>")
                .replace(raw_name, "<PERSON>")
            )
            # Simulate an upstream response that includes both tag counts and dangerous raw entity spans
            out_items.append(
                {
                    "id": entry["id"],
                    "masked_text": masked,
                    "entities": [
                        {"tag": "PHONE", "original": raw_phone},
                        {"tag": "ID_CARD", "value": raw_id_card},
                        {"tag": "PERSON", "plaintext": raw_name},
                    ],
                }
            )
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "policy_snapshot": f"{policy}:v3",
                    "items": out_items,
                },
            },
        )

    transport = httpx.MockTransport(mask_handler)
    client = DesensitizationClient(
        base_url="http://127.0.0.1:18100/",
        api_key="dsc_secret_key_001",
    )

    batch = await client.mask_batch(
        [f"我是{raw_name}，身份证{raw_id_card}，电话{raw_phone}，咨询公积金提取"],
        policy_code="gov_eval_corpus",
        transport=transport,
    )
    assert len(captured_requests) == 1
    req_url, req_headers, req_body = captured_requests[0]
    assert req_url == "http://127.0.0.1:18100/api/v1/mask/batch-inline"
    assert req_headers.get("x-api-key") == "dsc_secret_key_001"
    assert req_body["policy_code"] == "gov_eval_corpus"

    assert batch.policy_snapshot == "gov_eval_corpus:v3"
    assert len(batch.items) == 1
    item0 = batch.items[0]
    assert item0.masked_text == "我是<PERSON>，身份证<ID_CARD>，电话<PHONE>，咨询公积金提取"
    assert item0.tag_counts == {"PHONE": 1, "ID_CARD": 1, "PERSON": 1}

    # Verify NO plaintext PII exists anywhere in serialized summary
    serialized_summary = json.dumps(item0.tag_counts, ensure_ascii=False)
    assert raw_phone not in serialized_summary
    assert raw_id_card not in serialized_summary
    assert raw_name not in serialized_summary

    # Also verify direct sanitize_desensitize_summary against hostile dict shapes
    sanitized = sanitize_desensitize_summary(
        {
            "tag_counts": {"phone": 2, "id_card": 0, "name": 1},
            "raw_text": raw_phone,
            "plaintext": raw_id_card,
        }
    )
    assert sanitized == {"PHONE": 2, "NAME": 1}

    # Closed-fail checks: HTTP 500, missing masked_text, and item count mismatch all raise DesensitizationError
    for bad_policy in (
        "trigger_http_500",
        "trigger_missing_masked_text",
        "trigger_count_mismatch",
    ):
        with pytest.raises(DesensitizationError):
            await client.mask_batch(
                [f"手机 {raw_phone}"],
                policy_code=bad_policy,
                transport=transport,
            )


async def test_ingest_from_mysql_masks_all_fields_and_persists_zero_rows_on_mask_failure(
    db_session,
    test_settings,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlite3
    from sqlalchemy import select

    from app.common.errors import AppError
    from app.db.models import Dataset, EvalCase
    from app.ingestion import (
        DatasetCreateRequest,
        IngestFilter,
        MysqlIngestRequest,
        create_dataset,
        ingest_from_mysql,
    )

    # Build temporary external source DB simulating gov_routing_conversation
    src_db_path = tmp_path / "gov_online_readonly.db"
    conn = sqlite3.connect(src_db_path)
    conn.execute(
        """
        CREATE TABLE gov_routing_conversation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            input_text TEXT NOT NULL,
            conversation_turns_json TEXT,
            answer_text TEXT,
            selected_region TEXT,
            route_mode TEXT,
            mining_signal TEXT NOT NULL,
            user_phone TEXT,
            create_time TEXT NOT NULL
        )
        """
    )
    secret_phone_1 = "13988887777"
    secret_id_1 = "320404198805056677"
    secret_phone_2 = "13766665555"
    conn.executemany(
        """
        INSERT INTO gov_routing_conversation (
            request_id, input_text, conversation_turns_json, answer_text,
            selected_region, route_mode, mining_signal, user_phone, create_time
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "req_online_001",
                f"我的手机号是{secret_phone_1}，在天宁区怎么提取公积金？",
                json.dumps(
                    [{"role": "user", "content": f"身份证号{secret_id_1}能线上办吗"}],
                    ensure_ascii=False,
                ),
                f"请拨打 12329，勿泄露{secret_phone_1}",
                "天宁区",
                "auto",
                "USER_DISLIKE",
                secret_phone_1,
                "2026-11-05T10:30:00",
            ),
            (
                "req_online_002",
                f"灵活就业社保补贴怎么申请？联系电话{secret_phone_2}",
                None,
                "可通过常州人社网上申办。",
                "武进区",
                "auto",
                "FALLBACK",
                secret_phone_2,
                "2026-11-08 14:15:00",
            ),
            # Out-of-window record -> must be filtered out
            (
                "req_online_old",
                "早于时间窗口的历史会话",
                None,
                "旧回答",
                "钟楼区",
                "auto",
                "USER_DISLIKE",
                "13500001111",
                "2026-10-15T09:00:00",
            ),
            # Unmatched mining_signal -> must be filtered out
            (
                "req_online_other_sig",
                "普通未命中挖掘信号的会话",
                None,
                "普通回答",
                "新北区",
                "auto",
                "NORMAL_OK",
                "13600002222",
                "2026-11-06T12:00:00",
            ),
        ],
    )
    conn.commit()
    conn.close()

    src_dsn = f"sqlite:///{src_db_path.as_posix()}"
    monkeypatch.setenv("GOV_ONLINE_READONLY_DSN", src_dsn)
    monkeypatch.setenv("GOV_MUTABLE_PRIMARY_URL", src_dsn)

    ds_dict = create_dataset(
        db_session,
        DatasetCreateRequest(
            dataset_code="gov_online_pull_ds",
            version_tag="v1.0",
            dataset_name="线上抽样测试集",
            eval_level="E2E",
        ),
        created_by="admin",
    )
    dataset_id = ds_dict["id"]

    # 1. Reject source_dsn_env that does not end with _READONLY_DSN
    with pytest.raises(AppError) as bad_env_exc:
        await ingest_from_mysql(
            db_session,
            dataset_id,
            MysqlIngestRequest(
                source_dsn_env="GOV_MUTABLE_PRIMARY_URL",
                preset_extractor="gov_routing_conversation",
            ),
            settings=test_settings,
        )
    assert bad_env_exc.value.status_code == 400

    # 2. When DesensitizationClient fails, ZERO rows may be persisted to kd_eval.ev_case
    failing_transport = httpx.MockTransport(
        lambda req: httpx.Response(503, text="desensitize service unavailable")
    )
    with pytest.raises(AppError) as fail_exc:
        await ingest_from_mysql(
            db_session,
            dataset_id,
            MysqlIngestRequest(
                source_dsn_env="GOV_ONLINE_READONLY_DSN",
                preset_extractor="gov_routing_conversation",
                filter=IngestFilter(
                    start_time="2026-11-01T00:00:00",
                    end_time="2026-11-12T00:00:00",
                    mining_signals=["USER_DISLIKE", "FALLBACK"],
                    limit=50,
                ),
                desensitize_policy_code="gov_eval_corpus",
                dev_ratio=0.5,
            ),
            settings=test_settings,
            transport=failing_transport,
        )
    assert fail_exc.value.status_code == 502
    persisted_after_failure = db_session.scalars(
        select(EvalCase).where(EvalCase.dataset_id == dataset_id)
    ).all()
    assert persisted_after_failure == []

    # 3. Successful desensitization persists masked cases only, updates policy_snapshot, and strips all PII
    def ok_mask_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        out = []
        for entry in body["items"]:
            txt = entry["text"]
            hits = []
            if secret_phone_1 in txt or secret_phone_2 in txt:
                hits.append({"tag": "PHONE", "raw": secret_phone_1})
            if secret_id_1 in txt:
                hits.append({"tag": "ID_CARD", "raw": secret_id_1})
            masked = (
                txt.replace(secret_phone_1, "<PHONE>")
                .replace(secret_phone_2, "<PHONE>")
                .replace(secret_id_1, "<ID_CARD>")
            )
            out.append({"id": entry["id"], "masked_text": masked, "hits": hits})
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "policy_snapshot": "gov_eval_corpus:v2",
                    "items": out,
                },
            },
        )

    ok_transport = httpx.MockTransport(ok_mask_handler)
    ingest_res = await ingest_from_mysql(
        db_session,
        dataset_id,
        MysqlIngestRequest(
            source_dsn_env="GOV_ONLINE_READONLY_DSN",
            preset_extractor="gov_routing_conversation",
            filter=IngestFilter(
                start_time="2026-11-01T00:00:00",
                end_time="2026-11-12T00:00:00",
                mining_signals=["USER_DISLIKE", "FALLBACK"],
                limit=50,
            ),
            desensitize_policy_code="gov_eval_corpus",
            dev_ratio=0.5,
        ),
        settings=test_settings,
        transport=ok_transport,
    )
    assert ingest_res["ingested_count"] == 2
    assert ingest_res["desensitize_policy_snapshot"] == "gov_eval_corpus:v2"

    ds_row = db_session.get(Dataset, dataset_id)
    assert ds_row is not None
    assert ds_row.desensitize_policy_snapshot == "gov_eval_corpus:v2"
    # Pending review cases must NOT inflate approved dev_case_count / holdout_case_count yet
    assert ds_row.dev_case_count == 0
    assert ds_row.holdout_case_count == 0

    rows = db_session.scalars(
        select(EvalCase).where(EvalCase.dataset_id == dataset_id).order_by(EvalCase.id.asc())
    ).all()
    assert len(rows) == 2
    for row in rows:
        assert row.pii_masked == 1
        assert row.review_status == "PENDING_REVIEW"
        assert row.source_kind == "ONLINE_PULL"
        assert row.dataset_split == assign_dataset_split(row.case_code, dev_ratio=0.5)
        raw_dump = json.dumps(
            {
                "input_text": row.input_text,
                "turns": row.conversation_turns_json,
                "expected": row.expected_output,
                "source_ref": row.source_ref_json,
                "sut_input": row.sut_input_json,
                "summary": row.desensitize_summary_json,
            },
            ensure_ascii=False,
        )
        assert secret_phone_1 not in raw_dump
        assert secret_phone_2 not in raw_dump
        assert secret_id_1 not in raw_dump

    assert rows[0].sut_input_json == {"selected_region": "天宁区", "route_mode": "auto"}
    assert rows[0].desensitize_summary_json == {"PHONE": 2, "ID_CARD": 1}


async def test_import_record_snapshots_and_recompute_counts_service_invariants(
    db_session,
    test_settings,
) -> None:
    from app.common.errors import AppError
    from app.db.models import Dataset
    from app.ingestion import (
        CaseCurateRequest,
        DatasetCreateRequest,
        DatasetImportRequest,
        RecordComponentSnapshotsRequest,
        create_dataset,
        curate_case,
        delete_case,
        import_cases,
        recompute_dataset_counts,
        record_dataset_component_snapshots,
    )

    comp_ds = create_dataset(
        db_session,
        DatasetCreateRequest(
            dataset_code="comp_router_ds",
            version_tag="v1.0",
            dataset_name="路由组件快照集",
            eval_level="COMPONENT",
        ),
        created_by="admin",
    )
    comp_ds_id = comp_ds["id"]

    jsonl_payload = "\n".join(
        [
            json.dumps(
                {
                    "case_code": "comp_c1",
                    "input_text": "异地就医备案怎么办理？",
                    "dataset_split": "DEV",
                    "sut_input_json": {"selected_region": "新北区", "component": "SupervisorRouter"},
                    "pii_masked": 1,
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "case_code": "comp_c2",
                    "input_text": "新生儿医保参保需要哪些材料？",
                    "dataset_split": "HOLDOUT",
                    "sut_input_json": {"selected_region": "天宁区", "component": "FastRouter"},
                    "pii_masked": 0,
                },
                ensure_ascii=False,
            ),
        ]
    )
    imp = await import_cases(
        db_session,
        comp_ds_id,
        DatasetImportRequest(jsonl_text=jsonl_payload, dev_ratio=0.5),
        settings=test_settings,
    )
    assert imp["imported_count"] == 2

    def harness_record_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "sut_context": {
                        "agents": [{"agent_id": "cz_yb_agent", "region": body.get("selected_region")}],
                        "candidate_responses": [],
                    },
                    "reference_output": f"线上参考:{body['query']}",
                },
            },
        )

    rec_res = await record_dataset_component_snapshots(
        db_session,
        comp_ds_id,
        RecordComponentSnapshotsRequest(adapter_code="gov_comp_routing_harness"),
        transport=httpx.MockTransport(harness_record_handler),
    )
    assert rec_res["recorded_count"] == 2
    c1_id = rec_res["cases"][0]["id"]
    c2_id = rec_res["cases"][1]["id"]
    assert rec_res["cases"][0]["source_kind"] == "HARNESS_RECORD"
    assert rec_res["cases"][0]["sut_context_json"]["agents"][0]["region"] == "新北区"

    # Rejects curating comp_c2 (pii_masked=0) to APPROVED
    with pytest.raises(AppError) as block_exc:
        curate_case(
            db_session,
            c2_id,
            CaseCurateRequest(review_status="APPROVED"),
            reviewer="admin",
        )
    assert block_exc.value.status_code == 400
    assert "pii_masked = 0" in block_exc.value.message

    # Approve comp_c1 (DEV, pii_masked=1) and confirm + approve comp_c2 (HOLDOUT, pii_masked=1)
    curate_case(
        db_session,
        c1_id,
        CaseCurateRequest(expected_output="国家医保服务平台APP备案", review_status="APPROVED"),
        reviewer="admin",
    )
    curate_case(
        db_session,
        c2_id,
        CaseCurateRequest(
            expected_output="出生医学证明与户口簿",
            pii_masked=1,
            review_status="APPROVED",
        ),
        reviewer="admin",
    )
    ds_row = db_session.get(Dataset, comp_ds_id)
    assert ds_row is not None
    assert (ds_row.dev_case_count, ds_row.holdout_case_count) == (1, 1)

    # Soft delete c1 -> dev_case_count drops to 0 immediately
    delete_case(db_session, c1_id)
    assert recompute_dataset_counts(db_session, comp_ds_id) == (0, 1)


