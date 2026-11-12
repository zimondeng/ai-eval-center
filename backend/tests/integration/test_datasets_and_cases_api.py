from __future__ import annotations

import json
from datetime import timedelta

import httpx
from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.common.crypto import sha256_hex
from app.db.base import utcnow_naive
from app.db.models import ApiKey
from app.ingestion import assign_dataset_split


async def test_datasets_crud_auth_boundaries_and_ci_api_key_import(
    app: FastAPI,
    client: httpx.AsyncClient,
    db_session: Session,
    admin_headers: dict[str, str],
) -> None:
    # 1. Unauthenticated requests rejected with 401
    unauth = await client.get("/api/v1/datasets")
    assert unauth.status_code == 401
    assert unauth.json()["code"] == 401

    # Provision an active CI API Key (evk_*)
    raw_ci_key = "evk_live_corpus_ci_import_key_2026"
    db_session.add(
        ApiKey(
            key_name="corpus-ci-bot",
            key_prefix=raw_ci_key[:13],
            key_sha256=sha256_hex(raw_ci_key),
            status="ACTIVE",
            expires_at=utcnow_naive() + timedelta(days=7),
        )
    )
    db_session.commit()
    ci_headers = {"X-API-Key": raw_ci_key}

    # Console-only endpoints MUST reject CI API key
    ci_list_attempt = await client.get("/api/v1/datasets", headers=ci_headers)
    assert ci_list_attempt.status_code == 401

    # 2. Create E2E dataset via console JWT
    create_resp = await client.post(
        "/api/v1/datasets",
        headers=admin_headers,
        json={
            "dataset_code": "gov_e2e_golden",
            "version_tag": "v1.0",
            "dataset_name": "政务客服端到端黄金集",
            "eval_level": "E2E",
            "description": "首版验收黄金集",
        },
    )
    assert create_resp.status_code == 200
    ds = create_resp.json()["data"]
    ds_id = ds["id"]
    assert ds["dataset_code"] == "gov_e2e_golden"
    assert ds["version_tag"] == "v1.0"
    assert ds["status"] == "DRAFT"
    assert ds["dev_case_count"] == 0
    assert ds["holdout_case_count"] == 0
    assert ds["created_by"] == "admin"

    # Duplicate (dataset_code, version_tag) rejected with 409
    dup_resp = await client.post(
        "/api/v1/datasets",
        headers=admin_headers,
        json={
            "dataset_code": "gov_e2e_golden",
            "version_tag": "v1.0",
            "dataset_name": "重复数据集",
            "eval_level": "E2E",
        },
    )
    assert dup_resp.status_code == 409

    # 3. List & Get & Update dataset
    list_resp = await client.get(
        "/api/v1/datasets",
        params={"eval_level": "E2E", "q": "黄金集"},
        headers=admin_headers,
    )
    assert list_resp.status_code == 200
    assert [d["id"] for d in list_resp.json()["data"]] == [ds_id]

    upd_resp = await client.put(
        f"/api/v1/datasets/{ds_id}",
        headers=admin_headers,
        json={"status": "PUBLISHED", "description": "已发布供跑批验收"},
    )
    assert upd_resp.status_code == 200
    assert upd_resp.json()["data"]["status"] == "PUBLISHED"

    # 4. POST /datasets/{id}/import MUST accept BOTH X-API-Key: evk_* and Console JWT
    jsonl_lines = "\n".join(
        [
            json.dumps(
                {
                    "case_code": "ci_case_001",
                    "input_text": "常州住房公积金咨询热线是多少？",
                    "expected_output": "常州市住房公积金咨询服务热线为 12329。",
                    "pii_masked": 1,
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "case_code": "ci_case_002",
                    "input_text": "新生儿医保在哪个小程序办理？",
                    "expected_output": "江苏医保云 APP 或小程序。",
                    "pii_masked": 1,
                },
                ensure_ascii=False,
            ),
        ]
    )
    ci_import_resp = await client.post(
        f"/api/v1/datasets/{ds_id}/import",
        headers=ci_headers,
        json={"jsonl_text": jsonl_lines, "dev_ratio": 0.5},
    )
    assert ci_import_resp.status_code == 200
    ci_imp_data = ci_import_resp.json()["data"]
    assert ci_imp_data["imported_count"] == 2

    jwt_import_resp = await client.post(
        f"/api/v1/datasets/{ds_id}/import",
        headers=admin_headers,
        json={
            "cases": [
                {
                    "case_code": "jwt_case_003",
                    "input_text": "商业贷款转公积金贷款需要满足什么条件？",
                    "dataset_split": "HOLDOUT",
                    "pii_masked": 0,
                }
            ]
        },
    )
    assert jwt_import_resp.status_code == 200
    assert jwt_import_resp.json()["data"]["imported_count"] == 1

    cases_resp = await client.get(
        f"/api/v1/datasets/{ds_id}/cases",
        headers=admin_headers,
    )
    assert cases_resp.status_code == 200
    cases_page = cases_resp.json()["data"]
    assert cases_page["total"] == 3
    by_code = {c["case_code"]: c for c in cases_page["items"]}
    assert by_code["ci_case_001"]["dataset_split"] == assign_dataset_split("ci_case_001", 0.5)
    assert by_code["ci_case_002"]["dataset_split"] == assign_dataset_split("ci_case_002", 0.5)
    assert by_code["jwt_case_003"]["dataset_split"] == "HOLDOUT"
    assert by_code["jwt_case_003"]["pii_masked"] == 0


async def test_ingest_from_mysql_api_with_sqlite_source_and_zero_persist_on_mask_error(
    app: FastAPI,
    client: httpx.AsyncClient,
    db_session: Session,
    admin_headers: dict[str, str],
    tmp_path,
    monkeypatch,
) -> None:
    import sqlite3
    from app.api.datasets import get_desensitize_http_transport

    # Provision CI API Key to verify /ingest-from-mysql also accepts X-API-Key
    raw_ci_key = "evk_live_ingest_mysql_key_009"
    db_session.add(
        ApiKey(
            key_name="nightly-mysql-sampler",
            key_prefix=raw_ci_key[:13],
            key_sha256=sha256_hex(raw_ci_key),
            status="ACTIVE",
            expires_at=utcnow_naive() + timedelta(days=3),
        )
    )
    db_session.commit()

    # Build temporary SQLite source table with PII
    src_file = tmp_path / "online_gov_ro.db"
    conn = sqlite3.connect(src_file)
    conn.execute(
        """
        CREATE TABLE gov_routing_conversation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            input_text TEXT NOT NULL,
            answer_text TEXT,
            selected_region TEXT,
            mining_signal TEXT NOT NULL,
            create_time TEXT NOT NULL
        )
        """
    )
    pii_phone = "13899990000"
    pii_id = "320402199208083344"
    conn.executemany(
        """
        INSERT INTO gov_routing_conversation (
            request_id, input_text, answer_text, selected_region, mining_signal, create_time
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "req_pull_a1",
                f"本人电话{pii_phone}，咨询常州灵活就业医保缴费标准",
                f"2026年缴费标准详见公告（切勿登记{pii_phone}）",
                "钟楼区",
                "USER_DISLIKE",
                "2026-11-03T09:15:00",
            ),
            (
                "req_pull_a2",
                f"身份证{pii_id}办理不动产过户预约",
                "可通过常州不动产登记公众号预约。",
                "武进区",
                "CITY_BACKUP",
                "2026-11-09T16:40:00",
            ),
        ],
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("GOV_ONLINE_READONLY_DSN", f"sqlite:///{src_file.as_posix()}")

    ds_resp = await client.post(
        "/api/v1/datasets",
        headers=admin_headers,
        json={
            "dataset_code": "gov_online_sampled",
            "version_tag": "v1.0",
            "dataset_name": "线上回流脱敏测试集",
            "eval_level": "E2E",
        },
    )
    ds_id = ds_resp.json()["data"]["id"]

    # 1. Desensitization center failure -> 502 AND zero rows persisted!
    app.dependency_overrides[get_desensitize_http_transport] = lambda: httpx.MockTransport(
        lambda _req: httpx.Response(500, text="masker crash")
    )
    try:
        fail_ingest = await client.post(
            f"/api/v1/datasets/{ds_id}/ingest-from-mysql",
            headers={"X-API-Key": raw_ci_key},
            json={
                "source_dsn_env": "GOV_ONLINE_READONLY_DSN",
                "preset_extractor": "gov_routing_conversation",
                "filter": {
                    "start_time": "2026-11-01T00:00:00",
                    "end_time": "2026-11-12T00:00:00",
                    "mining_signals": ["USER_DISLIKE", "CITY_BACKUP"],
                    "limit": 100,
                },
                "desensitize_policy_code": "gov_eval_corpus",
                "dev_ratio": 0.5,
            },
        )
        assert fail_ingest.status_code == 502
        empty_check = await client.get(
            f"/api/v1/datasets/{ds_id}/cases", headers=admin_headers
        )
        assert empty_check.json()["data"]["total"] == 0

        # 2. Healthy desensitization center -> masks all fields, sets policy snapshot, persists PENDING_REVIEW
        def mask_ok(req: httpx.Request) -> httpx.Response:
            assert req.url.path == "/api/v1/mask/batch-inline"
            body = json.loads(req.content.decode("utf-8"))
            out = []
            for item in body["items"]:
                raw = item["text"]
                counts = {}
                if pii_phone in raw:
                    counts["PHONE"] = 1
                if pii_id in raw:
                    counts["ID_CARD"] = 1
                masked = raw.replace(pii_phone, "[PHONE]").replace(pii_id, "[ID_CARD]")
                out.append({"id": item["id"], "masked_text": masked, "tag_counts": counts})
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "policy_snapshot": "gov_eval_corpus:v1",
                        "items": out,
                    },
                },
            )

        app.dependency_overrides[get_desensitize_http_transport] = lambda: httpx.MockTransport(
            mask_ok
        )
        ok_ingest = await client.post(
            f"/api/v1/datasets/{ds_id}/ingest-from-mysql",
            headers={"X-API-Key": raw_ci_key},
            json={
                "source_dsn_env": "GOV_ONLINE_READONLY_DSN",
                "preset_extractor": "gov_routing_conversation",
                "filter": {
                    "start_time": "2026-11-01T00:00:00",
                    "end_time": "2026-11-12T00:00:00",
                    "mining_signals": ["USER_DISLIKE", "CITY_BACKUP"],
                    "limit": 100,
                },
                "desensitize_policy_code": "gov_eval_corpus",
                "dev_ratio": 0.5,
            },
        )
        assert ok_ingest.status_code == 200
        ing_data = ok_ingest.json()["data"]
        assert ing_data["ingested_count"] == 2
        assert ing_data["desensitize_policy_snapshot"] == "gov_eval_corpus:v1"

        ds_detail = (
            await client.get(f"/api/v1/datasets/{ds_id}", headers=admin_headers)
        ).json()["data"]
        assert ds_detail["desensitize_policy_snapshot"] == "gov_eval_corpus:v1"
        assert ds_detail["total_case_count"] == 2
        assert ds_detail["pending_review_count"] == 2
        assert ds_detail["dev_case_count"] == 0
        assert ds_detail["holdout_case_count"] == 0

        pulled_cases = (
            await client.get(f"/api/v1/datasets/{ds_id}/cases", headers=admin_headers)
        ).json()["data"]["items"]
        full_json = json.dumps(pulled_cases, ensure_ascii=False)
        assert pii_phone not in full_json
        assert pii_id not in full_json
        assert all(c["pii_masked"] == 1 for c in pulled_cases)
        assert all(c["review_status"] == "PENDING_REVIEW" for c in pulled_cases)
    finally:
        app.dependency_overrides.pop(get_desensitize_http_transport, None)


async def test_component_snapshot_recording_api_and_level_validations(
    app: FastAPI,
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    monkeypatch,
) -> None:
    from app.api.adapters import get_sut_http_transport

    monkeypatch.setenv("ROUTING_EVAL_HARNESS_TOKEN", "rec-secret-token-777")

    e2e_ds = (
        await client.post(
            "/api/v1/datasets",
            headers=admin_headers,
            json={
                "dataset_code": "e2e_cannot_record",
                "version_tag": "v1.0",
                "dataset_name": "E2E不支持组件录制",
                "eval_level": "E2E",
            },
        )
    ).json()["data"]

    comp_ds = (
        await client.post(
            "/api/v1/datasets",
            headers=admin_headers,
            json={
                "dataset_code": "comp_supervisor_eval",
                "version_tag": "v1.0",
                "dataset_name": "Supervisor组件隔离回放集",
                "eval_level": "COMPONENT",
            },
        )
    ).json()["data"]
    comp_ds_id = comp_ds["id"]

    await client.post(
        f"/api/v1/datasets/{comp_ds_id}/cases",
        headers=admin_headers,
        json={
            "case_code": "sup_001",
            "dataset_split": "DEV",
            "input_text": "个体工商户营业执照注销去哪里办？",
            "sut_input_json": {"selected_region": "天宁区", "component": "SupervisorRouter"},
            "pii_masked": 1,
        },
    )

    # 1. Rejects E2E dataset on record-component-snapshots
    bad_ds_level = await client.post(
        f"/api/v1/datasets/{e2e_ds['id']}/record-component-snapshots",
        headers=admin_headers,
        json={"adapter_code": "gov_comp_routing_harness"},
    )
    assert bad_ds_level.status_code == 400

    # 2. Rejects non-COMPONENT_HARNESS adapter
    bad_adapter_type = await client.post(
        f"/api/v1/datasets/{comp_ds_id}/record-component-snapshots",
        headers=admin_headers,
        json={"adapter_code": "gov_e2e_routing_blocking"},
    )
    assert bad_adapter_type.status_code == 400

    # 3. Records component snapshot via MockTransport and supports has_snapshot filter
    seen_token: list[str] = []

    def record_mock(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/internal/eval-harness/record"
        seen_token.append(req.headers.get("x-eval-harness-token", ""))
        body = json.loads(req.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "sut_context": {
                        "agents": [
                            {"agent_id": "cz_scjg_agent", "name": "市场监管智能体"}
                        ],
                        "candidate_responses": [
                            {"agent_id": "cz_scjg_agent", "answer": "江苏政务服务网一网通办"}
                        ],
                        "recorded_region": body.get("selected_region"),
                    }
                },
            },
        )

    app.dependency_overrides[get_sut_http_transport] = lambda: httpx.MockTransport(record_mock)
    try:
        before_snap = await client.get(
            f"/api/v1/datasets/{comp_ds_id}/cases",
            params={"has_snapshot": False},
            headers=admin_headers,
        )
        assert before_snap.json()["data"]["total"] == 1

        rec_resp = await client.post(
            f"/api/v1/datasets/{comp_ds_id}/record-component-snapshots",
            headers=admin_headers,
            json={"adapter_code": "gov_comp_routing_harness"},
        )
        assert rec_resp.status_code == 200
        rec_data = rec_resp.json()["data"]
        assert seen_token == ["rec-secret-token-777"]
        assert rec_data["recorded_count"] == 1
        recorded_case = rec_data["cases"][0]
        assert recorded_case["source_kind"] == "HARNESS_RECORD"
        assert recorded_case["has_snapshot"] is True
        assert recorded_case["sut_context_json"]["recorded_region"] == "天宁区"

        after_snap = await client.get(
            f"/api/v1/datasets/{comp_ds_id}/cases",
            params={"has_snapshot": True},
            headers=admin_headers,
        )
        assert after_snap.json()["data"]["total"] == 1
    finally:
        app.dependency_overrides.pop(get_sut_http_transport, None)


async def test_cases_uncertainty_desc_nulls_last_curation_guardrail_and_dataset_counts(
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
) -> None:
    ds_id = (
        await client.post(
            "/api/v1/datasets",
            headers=admin_headers,
            json={
                "dataset_code": "curation_workbench_ds",
                "version_tag": "v1.0",
                "dataset_name": "真值校准工作台测试集",
                "eval_level": "E2E",
            },
        )
    ).json()["data"]["id"]

    # Create 5 cases with mixed uncertainty_score (including NULLs), splits, and pii_masked flags
    seed_cases = [
        {
            "case_code": "u_null_1",
            "dataset_split": "DEV",
            "input_text": "无不确定度样本1",
            "pii_masked": 1,
            "uncertainty_score": None,
        },
        {
            "case_code": "u_low_025",
            "dataset_split": "DEV",
            "input_text": "低不确定度样本",
            "pii_masked": 1,
            "uncertainty_score": 0.25,
        },
        {
            "case_code": "u_high_091",
            "dataset_split": "DEV",
            "input_text": "高不确定度优先复核样本",
            "pii_masked": 0,
            "uncertainty_score": 0.91,
        },
        {
            "case_code": "u_null_2",
            "dataset_split": "HOLDOUT",
            "input_text": "无不确定度样本2",
            "pii_masked": 1,
            "uncertainty_score": None,
        },
        {
            "case_code": "u_mid_063",
            "dataset_split": "HOLDOUT",
            "input_text": "中不确定度留出集样本",
            "pii_masked": 1,
            "uncertainty_score": 0.63,
        },
    ]
    created_by_code = {}
    for item in seed_cases:
        resp = await client.post(
            f"/api/v1/datasets/{ds_id}/cases",
            headers=admin_headers,
            json=item,
        )
        assert resp.status_code == 200
        c_data = resp.json()["data"]
        created_by_code[c_data["case_code"]] = c_data

    # 1. Verify sort=uncertainty_desc places highest scores first and NULLs strictly last!
    sorted_resp = await client.get(
        f"/api/v1/datasets/{ds_id}/cases",
        params={"sort": "uncertainty_desc", "page": 1, "page_size": 10},
        headers=admin_headers,
    )
    assert sorted_resp.status_code == 200
    ordered_codes = [c["case_code"] for c in sorted_resp.json()["data"]["items"]]
    assert ordered_codes == [
        "u_high_091",
        "u_mid_063",
        "u_low_025",
        "u_null_1",
        "u_null_2",
    ]

    # Verify pagination & filter combinations (split + pii_masked + q)
    page1 = (
        await client.get(
            f"/api/v1/datasets/{ds_id}/cases",
            params={"sort": "uncertainty_desc", "page": 1, "page_size": 2},
            headers=admin_headers,
        )
    ).json()["data"]
    assert page1["total"] == 5
    assert [c["case_code"] for c in page1["items"]] == ["u_high_091", "u_mid_063"]

    unmasked_only = (
        await client.get(
            f"/api/v1/datasets/{ds_id}/cases",
            params={"pii_masked": 0, "q": "优先复核"},
            headers=admin_headers,
        )
    ).json()["data"]
    assert [c["case_code"] for c in unmasked_only["items"]] == ["u_high_091"]

    # 2. Hard guardrail: pii_masked == 0 MUST reject review_status="APPROVED" with 400
    high_id = created_by_code["u_high_091"]["id"]
    blocked_resp = await client.put(
        f"/api/v1/cases/{high_id}/curate",
        headers=admin_headers,
        json={
            "expected_output": "人工补齐标准答案",
            "review_status": "APPROVED",
        },
    )
    assert blocked_resp.status_code == 400
    assert blocked_resp.json()["code"] == 400
    assert (
        blocked_resp.json()["message"]
        == "当前用例尚未完成脱敏确认（pii_masked = 0），禁止置为已审核生效"
    )

    # Confirming pii_masked=1 together with APPROVED succeeds and stamps reviewed_by / reviewed_at
    approved_high = await client.put(
        f"/api/v1/cases/{high_id}/curate",
        headers=admin_headers,
        json={
            "expected_output": "校准后的高优先级标准答案",
            "expected_structured_json": {"expected_agent_ids": ["cz_gjj_agent"]},
            "pii_masked": 1,
            "review_status": "APPROVED",
            "review_note": "脱敏人工核验通过",
        },
    )
    assert approved_high.status_code == 200
    curated_data = approved_high.json()["data"]
    assert curated_data["review_status"] == "APPROVED"
    assert curated_data["pii_masked"] == 1
    assert curated_data["reviewed_by"] == "admin"
    assert curated_data["reviewed_at"] is not None

    # Attempting to revert pii_masked back to 0 while still APPROVED is ALSO blocked!
    revert_pii_attempt = await client.put(
        f"/api/v1/cases/{high_id}/curate",
        headers=admin_headers,
        json={"pii_masked": 0},
    )
    assert revert_pii_attempt.status_code == 400

    # Approve one DEV case (u_low_025) and one HOLDOUT case (u_mid_063) -> counts become DEV=2, HOLDOUT=1
    await client.put(
        f"/api/v1/cases/{created_by_code['u_low_025']['id']}/curate",
        headers=admin_headers,
        json={"expected_output": "低不确定度标准答案", "review_status": "APPROVED"},
    )
    await client.put(
        f"/api/v1/cases/{created_by_code['u_mid_063']['id']}/curate",
        headers=admin_headers,
        json={"expected_output": "留出集标准答案", "review_status": "APPROVED"},
    )

    ds_after_approve = (
        await client.get(f"/api/v1/datasets/{ds_id}", headers=admin_headers)
    ).json()["data"]
    assert ds_after_approve["dev_case_count"] == 2
    assert ds_after_approve["holdout_case_count"] == 1

    # Reassign u_low_025 from DEV to HOLDOUT -> counts become DEV=1, HOLDOUT=2
    await client.put(
        f"/api/v1/cases/{created_by_code['u_low_025']['id']}/curate",
        headers=admin_headers,
        json={"dataset_split": "HOLDOUT"},
    )
    ds_after_move = (
        await client.get(f"/api/v1/datasets/{ds_id}", headers=admin_headers)
    ).json()["data"]
    assert ds_after_move["dev_case_count"] == 1
    assert ds_after_move["holdout_case_count"] == 2

    # Disable u_mid_063 -> counts become DEV=1, HOLDOUT=1
    await client.put(
        f"/api/v1/cases/{created_by_code['u_mid_063']['id']}/curate",
        headers=admin_headers,
        json={"review_status": "DISABLED"},
    )
    ds_after_disable = (
        await client.get(f"/api/v1/datasets/{ds_id}", headers=admin_headers)
    ).json()["data"]
    assert ds_after_disable["dev_case_count"] == 1
    assert ds_after_disable["holdout_case_count"] == 1

    # Delete u_high_091 (DEV) -> counts become DEV=0, HOLDOUT=1
    del_resp = await client.delete(
        f"/api/v1/cases/{high_id}",
        headers=admin_headers,
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["data"]["dev_case_count"] == 0
    assert del_resp.json()["data"]["holdout_case_count"] == 1


async def test_import_auto_desensitize_and_unmasked_approval_guard_on_import(
    app: FastAPI,
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
) -> None:
    from app.api.datasets import get_desensitize_http_transport

    ds_id = (
        await client.post(
            "/api/v1/datasets",
            headers=admin_headers,
            json={
                "dataset_code": "offline_auto_mask_ds",
                "version_tag": "v1.0",
                "dataset_name": "离线自动脱敏导入集",
                "eval_level": "E2E",
            },
        )
    ).json()["data"]["id"]

    # Reject offline import attempting APPROVED with pii_masked=0
    bad_imp = await client.post(
        f"/api/v1/datasets/{ds_id}/import",
        headers=admin_headers,
        json={
            "cases": [
                {
                    "case_code": "unsafe_approved",
                    "input_text": "未脱敏想直接生效",
                    "pii_masked": 0,
                    "review_status": "APPROVED",
                }
            ]
        },
    )
    assert bad_imp.status_code == 400
    assert "pii_masked = 0" in bad_imp.json()["message"]

    raw_phone = "13611112222"
    app.dependency_overrides[get_desensitize_http_transport] = lambda: httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "policy_snapshot": "gov_eval_corpus:v4",
                    "items": [
                        {
                            "id": e["id"],
                            "masked_text": e["text"].replace(raw_phone, "<PHONE>"),
                            "tag_counts": {"PHONE": 1} if raw_phone in e["text"] else {},
                        }
                        for e in json.loads(req.content.decode("utf-8"))["items"]
                    ],
                },
            },
        )
    )
    try:
        ok_imp = await client.post(
            f"/api/v1/datasets/{ds_id}/import",
            headers=admin_headers,
            json={
                "auto_desensitize": True,
                "desensitize_policy_code": "gov_eval_corpus",
                "cases": [
                    {
                        "case_code": "masked_imp_1",
                        "dataset_split": "DEV",
                        "input_text": f"联系电话{raw_phone}怎么变更参保地？",
                        "review_status": "APPROVED",
                    }
                ],
            },
        )
        assert ok_imp.status_code == 200
        imp_data = ok_imp.json()["data"]
        assert imp_data["imported_count"] == 1
        assert imp_data["dev_case_count"] == 1
        assert imp_data["desensitize_policy_snapshot"] == "gov_eval_corpus:v4"

        listed = (
            await client.get(f"/api/v1/datasets/{ds_id}/cases", headers=admin_headers)
        ).json()["data"]["items"]
        assert len(listed) == 1
        c0 = listed[0]
        assert raw_phone not in c0["input_text"]
        assert c0["pii_masked"] == 1
        assert c0["desensitize_summary_json"] == {"PHONE": 1}

        single = await client.get(f"/api/v1/cases/{c0['id']}", headers=admin_headers)
        assert single.status_code == 200
        assert single.json()["data"]["case_code"] == "masked_imp_1"

        missing = await client.get("/api/v1/cases/999999", headers=admin_headers)
        assert missing.status_code == 404
    finally:
        app.dependency_overrides.pop(get_desensitize_http_transport, None)


