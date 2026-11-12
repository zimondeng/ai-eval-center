# API 规格文档：AI 评测中心 (AI Evaluation Center)

- **默认服务监听**：`http://127.0.0.1:18200`
- **统一响应包络**：
```json
{
  "code": 200,
  "message": "ok",
  "request_id": "req_7c1d8a3f91e04b55",
  "data": {}
}
```
- **鉴权方式**：
  1. **Web 控制台接口 (`/api/v1/*`)**：支持控制台管理员登录签发的 `Authorization: Bearer <jwt_token>`；
  2. **CI / 自动化触发接口 (`/api/v1/runs*`, `/api/v1/datasets/*/import*`)**：同时支持 `X-API-Key: evk_live_xxxxxxxx`。

---

## 1. 判别模型渠道管理 API（支持 TypeSafe / EMRank / 本地 Ollama `tev1`）

### 1.1 新增或更新判别模型渠道
`POST /api/v1/providers`

**请求体示例 A（第三方中转站 EMRank `/v1/decisions`）**：
```json
{
  "provider_code": "emrank_decisions_prod",
  "provider_name": "EMRank 决策中转站",
  "provider_kind": "DECISION_RELAY",
  "endpoint_url": "https://emrank.tech/v1/decisions",
  "models_list_url": "https://emrank.tech/v1/models",
  "default_model": "typesafe/jev-1.14.0",
  "auth_type": "BEARER",
  "api_key_plain": "sk-xxxxxxxxxxxxxxxx",
  "network_mode": "PROXY_FALLBACK",
  "max_questions_per_call": 64,
  "max_options_per_question": 24,
  "waf_strip_code_retry": true,
  "cost_per_1m_input_tokens": 0.20,
  "is_default": false
}
```

**请求体示例 B（本地 Ollama `tev1:0.8b`）**：
```json
{
  "provider_code": "ollama_tev1_08b",
  "provider_name": "本地 Ollama tev1:0.8b",
  "provider_kind": "OLLAMA_LOCAL",
  "endpoint_url": "http://127.0.0.1:11434/v1/systemone",
  "models_list_url": "http://127.0.0.1:11434/v1/models",
  "default_model": "tev1:0.8b",
  "auth_type": "NONE",
  "network_mode": "DIRECT",
  "max_questions_per_call": 64,
  "max_options_per_question": 24,
  "waf_strip_code_retry": false,
  "cost_per_1m_input_tokens": 0.0,
  "extra_options_json": { "keep_alive": "15m" },
  "is_default": true
}
```

### 1.2 渠道决策探活与可用模型拉取
- `POST /api/v1/providers/{provider_code}/test`：发送一条最小化 `{"state": "探活测试", "questions": {"ping": {"type": "noul", "instructions": "该文本是否为中文？"}}}` 请求验证连通性、延迟、代理通道及概率返回格式。
- `GET /api/v1/providers/{provider_code}/models`：若配置了 `models_list_url`（如 `https://emrank.tech/v1/models`），动态拉取上游当前已启用的完整模型 ID 列表供前端下拉选择。

---

## 2. 评价维度、题干静态 Linter 与评测套件 API

### 2.1 题干静态质检预检接口
`POST /api/v1/dimensions/lint`

在保存前对 `state_fields_json` 与 `questions_json` 执行实时离线质检，返回 `ERROR`（阻断发布）与 `WARN`（优化建议）：
```json
{
  "passed": false,
  "issues": [
    {
      "severity": "ERROR",
      "code": "LINT_SCORE_ORDER",
      "question_key": "core_conclusion",
      "message": "Score 第 0 档检测到正向高分措辞('完全准确')，必须按最差到最好(worst-first)升序排列"
    },
    {
      "severity": "WARN",
      "code": "LINT_COMPOUND_QUESTION",
      "question_key": "channel_check",
      "message": "题干包含复合多问句，建议拆分为多道独立 noul 原子题由权重合成"
    }
  ]
}
```

### 2.2 维度与套件 CRUD
- `GET / POST /api/v1/dimensions`（同一 `dimension_code` 更新题干时自动生成 `version + 1` 新版本，原版本保持不可变）
- `GET / POST / PUT /api/v1/suites`（绑定锁定的维度版本、权重、`is_blocker`、`pass_policy` 与默认适配器/渠道）
- `GET / POST / PUT /api/v1/adapters` & `POST /api/v1/adapters/{adapter_code}/test`（管理并调试可插拔 SUT 适配器）

---

## 3. 测试集治理、单向脱敏采集与组件快照录制 API

### 3.1 从业务库抽样并自动调数据脱敏中心入库
`POST /api/v1/datasets/{dataset_id}/ingest-from-mysql`

由评测中心流式读取只读源库记录，在内存中直接调用 `data-desensitization-center` 的 `POST /api/v1/mask/batch-inline`，仅将脱敏后结果写入 `ev_case`（初始 `review_status = "PENDING_REVIEW"`，并按默认 `DEV:HOLDOUT = 50:50` 哈希稳定切分）。

**请求体**：
```json
{
  "source_dsn_env": "GOV_ONLINE_READONLY_DSN",
  "preset_extractor": "gov_routing_conversation",
  "filter": {
    "start_time": "2026-11-01T00:00:00",
    "end_time": "2026-11-12T00:00:00",
    "mining_signals": ["USER_DISLIKE", "CITY_BACKUP", "FALLBACK", "STRATIFIED_SAMPLE"],
    "limit": 500
  },
  "desensitize_policy_code": "gov_eval_corpus",
  "dev_ratio": 0.5
}
```

### 3.2 触发测试态组件回放快照录制（Record Mode）
`POST /api/v1/datasets/{dataset_id}/record-component-snapshots`

针对 `eval_level = "COMPONENT"` 的测试集，调用指定 `COMPONENT_HARNESS` 适配器的 `record_endpoint_url`，在测试环境真实执行一次路由并把返回的 `agents` 目录描述符与全量候选响应冻结进 `ev_case.sut_context_json`。

### 3.3 黄金集人工校准与 `DEV / HOLDOUT` 管理
- `GET /api/v1/datasets/{dataset_id}/cases?split=DEV&review_status=PENDING_REVIEW&sort=uncertainty_desc`
- `PUT /api/v1/cases/{case_id}/curate`：人工修订 `expected_output` / `expected_structured_json` 并置为 `APPROVED`（校验 `pii_masked == 1`）。

---

## 4. 实验批次执行、基线语义回归与零成本阈值重算 API

### 4.1 发起评测实验批次（E2E 或 Component）
`POST /api/v1/runs`

**请求体**：
```json
{
  "run_name": "路由主管提示词 v2 对比实验 (本地 tev1:0.8b 预筛)",
  "suite_code": "suite_gov_e2e_golden_v1",
  "dataset_id": 12,
  "dataset_split_filter": "DEV",
  "sut_adapter_code": "gov_e2e_routing_blocking",
  "provider_code": "ollama_local_tev1",
  "model_override": "tev1:0.8b",
  "baseline_run_id": 101,
  "sut_fingerprint": {
    "git_sha": "846e4b9b",
    "prompt_version": "supervisor_v2",
    "config_overrides": {}
  }
}
```

### 4.2 查询批次进度、汇总大盘与逐题概率归因
- `GET /api/v1/runs/{run_no}`：返回批次状态、通过率、P50/P95/TTFT 耗时、合批节省后的请求次数与输入 Token、`dimension_summary_json` 及语义回归分布 `regression_summary_json`。
- `GET /api/v1/runs/{run_no}/cases?passed=0&regression_tag=FACT_DIFFERS`：分页查询用例详情及每个维度子题的原始概率分布（`raw_answers_json`）与确定性扣分归因。
- `GET /api/v1/runs/{run_no}/compare/{baseline_run_no}`：双批次并排对比报告（若两批次 `pinned_model_name` 不同，返回 `"cross_model_warning": true`）。

### 4.3 纯内存零成本实时重算阈值 & 自动阈值校准接口
`POST /api/v1/runs/{run_no}/recalculate`

前端拖动阈值滑块或权重滑块时调用，**纯内存基于 `ev_probability_ledger` 计算，零模型 API 请求**：

**请求体**：
```json
{
  "suite_pass_threshold": 0.78,
  "dimension_overrides": {
    "rag_faithfulness_s1": { "threshold": 0.82, "weight": 2.5, "is_blocker": true },
    "policy_and_action_accuracy_s1": { "threshold": 0.70, "weight": 2.0, "is_blocker": true }
  }
}
```

**响应 `data`**：
```json
{
  "recalculated_pass_rate": 0.8420,
  "delta_pass_rate": -0.0360,
  "passed_cases": 210,
  "failed_cases": 39,
  "dimension_pass_rates": {
    "rag_faithfulness_s1": 0.8680,
    "policy_and_action_accuracy_s1": 0.8920
  },
  "flipped_case_ids": [1024, 1088, 1102],
  "compute_elapsed_ms": 6
}
```

---

## 5. 附录：被测端配套组件回放 Harness 协议契约

供 `f:\work_space\gov-service-ai-assistant\python` 在 `ROUTING_EVAL_HARNESS_ENABLED=true` 时挂载（生产环境默认不挂载），实现跨仓库解耦：

1. **录制端点 `POST /internal/eval-harness/record`**（请求头校验 `X-Eval-Harness-Token`）：
   - **入参**：`{ "query": "...", "selected_region": "天宁区", "turns": [...] }`
   - **出参**：返回当次组装的 `sut_context: { "agents": [...], "candidate_responses": [...] }` 与线上参考输出。
2. **无缓存隔离回放端点 `POST /internal/eval-harness/replay`**（请求头校验 `X-Eval-Harness-Token`）：
   - **入参**：
     ```json
     {
       "component": "FastRouter | SupervisorRouter | TeamRoutingRunner | AnswerQualityEvaluator",
       "query": "...",
       "turns": [],
       "sut_context": { "agents": [...], "candidate_responses": [...] },
       "overrides": { "supervisor_prompt": null, "confidence_threshold": 0.72 }
     }
     ```
   - **出参**：
     ```json
     {
       "code": 200,
       "data": {
         "actual_output": "...",
         "references": ["..."],
         "structured_decision": {
           "route_type": "department",
           "actual_agent_id": "cz_gjj_agent",
           "selected_agent_ids": ["cz_gjj_agent"],
           "usable": true
         },
         "trace": { "elapsed_ms": 18, "cache_bypassed": true }
       }
     }
     ```
