-- ============================================================================
-- AI 评测中心 (AI Evaluation Center) MySQL 8.0 Schema
-- 数据库名: kd_eval
-- 设计原则: 100% 业务无关通用表结构 + System One 多渠道支持 + 原始概率账本
-- ============================================================================

CREATE DATABASE IF NOT EXISTS `kd_eval`
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE `kd_eval`;

-- ----------------------------------------------------------------------------
-- 1. 控制台本地管理员账号表
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `ev_admin_user` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` char(32) NOT NULL COMMENT '业务UUID(无连字符)',
  `username` varchar(64) NOT NULL COMMENT '登录用户名',
  `display_name` varchar(64) NOT NULL COMMENT '显示名称',
  `password_hash` varchar(255) NOT NULL COMMENT '密码Bcrypt哈希',
  `role` varchar(32) NOT NULL DEFAULT 'ADMIN' COMMENT '角色: ADMIN / EVALUATOR / VIEWER',
  `is_enabled` tinyint(1) NOT NULL DEFAULT 1 COMMENT '启用状态(1启用 0禁用)',
  `last_login_at` datetime(3) DEFAULT NULL COMMENT '最后登录时间',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0 COMMENT '逻辑删除标志',
  `create_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `update_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ev_user_uuid` (`uuid`),
  UNIQUE KEY `uk_ev_username` (`username`)
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci ROW_FORMAT = Dynamic COMMENT = '评测中心-控制台用户表';

-- ----------------------------------------------------------------------------
-- 2. 外部与 CI 自动化触发 API Key 表（仅存前缀与 SHA-256 哈希）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `ev_api_key` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` char(32) NOT NULL COMMENT '业务UUID',
  `key_name` varchar(128) NOT NULL COMMENT '令牌名称(如 github-ci-nightly)',
  `key_prefix` varchar(20) NOT NULL COMMENT '令牌可读前缀(如 evk_live_9a2c)',
  `key_sha256` char(64) NOT NULL COMMENT '完整API Key的SHA-256摘要',
  `rate_limit_rpm` int NOT NULL DEFAULT 300 COMMENT '每分钟最大请求数限制',
  `status` varchar(16) NOT NULL DEFAULT 'ACTIVE' COMMENT '状态: ACTIVE / REVOKED',
  `expires_at` datetime(3) DEFAULT NULL COMMENT '过期时间',
  `last_used_at` datetime(3) DEFAULT NULL COMMENT '最近使用时间',
  `created_by` varchar(64) DEFAULT NULL COMMENT '创建人',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0 COMMENT '逻辑删除标志',
  `create_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `update_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ev_apikey_uuid` (`uuid`),
  UNIQUE KEY `uk_ev_apikey_sha256` (`key_sha256`)
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci ROW_FORMAT = Dynamic COMMENT = '评测中心-自动化API密钥表';

-- ----------------------------------------------------------------------------
-- 3. 判别模型渠道配置表（统一 System One 协议：TypeSafe / 第三方中转 / 本地 Ollama）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `ev_decision_provider` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` char(32) NOT NULL COMMENT '业务UUID',
  `provider_code` varchar(64) NOT NULL COMMENT '渠道唯一编码(如 ollama_local_tev1 / emrank_relay / typesafe_official)',
  `provider_name` varchar(128) NOT NULL COMMENT '渠道显示名称',
  `provider_kind` varchar(32) NOT NULL COMMENT '渠道类型: OLLAMA_LOCAL / DECISION_RELAY / TYPESAFE_OFFICIAL',
  `endpoint_url` varchar(255) NOT NULL COMMENT '完整决策端点URL(如 https://emrank.tech/v1/decisions 或 http://127.0.0.1:11434/v1/systemone)',
  `models_list_url` varchar(255) DEFAULT NULL COMMENT '可选模型列表探测地址(如 https://emrank.tech/v1/models)',
  `default_model` varchar(128) NOT NULL COMMENT '默认固定模型名称(如 tev1:0.8b 或 typesafe/jev-1.14.0)',
  `auth_type` varchar(24) NOT NULL DEFAULT 'BEARER' COMMENT '鉴权方式: NONE / BEARER / ENV_REF',
  `api_key_cipher` text DEFAULT NULL COMMENT 'AES-256-GCM加密后的API密钥(auth_type=BEARER时使用)',
  `api_key_env_name` varchar(64) DEFAULT NULL COMMENT '环境变量名引用(auth_type=ENV_REF时使用，如 EMRANK_API_KEY)',
  `api_key_masked` varchar(32) DEFAULT NULL COMMENT '脱敏尾号回显(如 sk-****9f2a)',
  `network_mode` varchar(24) NOT NULL DEFAULT 'PROXY_FALLBACK' COMMENT '网络模式: DIRECT(本地Ollama强制直连) / PROXY_FALLBACK(10808->7897->直连) / CUSTOM_PROXY',
  `custom_proxy_url` varchar(255) DEFAULT NULL COMMENT '自定义代理地址(CUSTOM_PROXY时有效)',
  `timeout_seconds` int NOT NULL DEFAULT 90 COMMENT '单次决策HTTP超时秒数',
  `max_concurrency` int NOT NULL DEFAULT 8 COMMENT '最大并发请求数',
  `max_questions_per_call` int NOT NULL DEFAULT 64 COMMENT '单次请求最大合批原子题数(tev1上限64)',
  `max_options_per_question` int NOT NULL DEFAULT 24 COMMENT '单题最大选项/档位数(tev1上限24)',
  `waf_strip_code_retry` tinyint(1) NOT NULL DEFAULT 1 COMMENT '遇边缘WAF HTML 403时是否剥离代码块自动重试',
  `cost_per_1m_input_tokens` decimal(10,4) NOT NULL DEFAULT 0.0000 COMMENT '每百万输入Token计费单价(本地模型为0)',
  `extra_options_json` json DEFAULT NULL COMMENT '渠道附加请求字段(如 Ollama {"keep_alive":"15m"})',
  `is_default` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否为系统默认判别渠道',
  `status` varchar(16) NOT NULL DEFAULT 'ACTIVE' COMMENT '状态: ACTIVE / DISABLED',
  `last_health_ok` tinyint(1) DEFAULT NULL COMMENT '最近连通性探测结果',
  `last_checked_at` datetime(3) DEFAULT NULL COMMENT '最近探测时间',
  `created_by` varchar(64) DEFAULT NULL COMMENT '创建人',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0 COMMENT '逻辑删除标志',
  `create_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `update_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ev_provider_uuid` (`uuid`),
  UNIQUE KEY `uk_ev_provider_code` (`provider_code`),
  KEY `idx_ev_provider_status` (`status`, `is_default`, `is_deleted`)
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci ROW_FORMAT = Dynamic COMMENT = '评测中心-System One判别模型渠道表';

-- ----------------------------------------------------------------------------
-- 4. 可插拔被测适配器配置表（SUT Adapter：隔离具体业务系统协议）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `ev_sut_adapter` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` char(32) NOT NULL COMMENT '业务UUID',
  `adapter_code` varchar(64) NOT NULL COMMENT '适配器唯一编码(如 gov_e2e_routing_blocking / gov_comp_fast_router)',
  `adapter_name` varchar(128) NOT NULL COMMENT '适配器名称',
  `eval_level` varchar(16) NOT NULL COMMENT '评测层级: E2E / COMPONENT',
  `adapter_type` varchar(32) NOT NULL COMMENT '驱动类型: HTTP_JSON / HTTP_SSE / COMPONENT_HARNESS',
  `endpoint_url` varchar(255) NOT NULL COMMENT '被测目标执行接口URL',
  `record_endpoint_url` varchar(255) DEFAULT NULL COMMENT '组件录制模式接口URL(仅COMPONENT_HARNESS可选)',
  `auth_config_json` json DEFAULT NULL COMMENT '被测端鉴权头配置(支持环境变量引用，如 EVAL_JAVA_BEARER_TOKEN)',
  `request_mapping_json` json NOT NULL COMMENT '如何从通用用例组装被测请求体的模板与路径映射',
  `response_mapping_json` json NOT NULL COMMENT '如何从被测响应提取 actual_output / references / actual_structured / trace 的JSONPath映射',
  `timeout_seconds` int NOT NULL DEFAULT 60 COMMENT '调用被测系统超时秒数',
  `max_concurrency` int NOT NULL DEFAULT 4 COMMENT '压测被测系统最大并发度',
  `status` varchar(16) NOT NULL DEFAULT 'ACTIVE' COMMENT '状态: ACTIVE / DISABLED',
  `description` varchar(500) DEFAULT NULL COMMENT '适配器契约说明',
  `created_by` varchar(64) DEFAULT NULL COMMENT '创建人',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0 COMMENT '逻辑删除标志',
  `create_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `update_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ev_adapter_uuid` (`uuid`),
  UNIQUE KEY `uk_ev_adapter_code` (`adapter_code`),
  KEY `idx_ev_adapter_level` (`eval_level`, `status`, `is_deleted`)
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci ROW_FORMAT = Dynamic COMMENT = '评测中心-可插拔被测适配器配置表';

-- ----------------------------------------------------------------------------
-- 5. 评价维度定义版本表（按 dimension_code + version 不可变版本化）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `ev_dimension` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` char(32) NOT NULL COMMENT '业务UUID',
  `dimension_code` varchar(64) NOT NULL COMMENT '维度编码(如 rag_faithfulness / gov_policy_accuracy / route_top1_match)',
  `version` int NOT NULL DEFAULT 1 COMMENT '不可变版本号(修改题干/档位/分值须升版)',
  `dimension_name` varchar(128) NOT NULL COMMENT '维度中文名称',
  `category` varchar(32) NOT NULL COMMENT '通用分类: GROUNDING / RELEVANCY / ACCURACY / ROUTING / SYNTHESIS / SAFETY / PERFORMANCE',
  `target_turn_mode` varchar(16) NOT NULL DEFAULT 'SINGLE_TURN' COMMENT '适用轮次: SINGLE_TURN / MULTI_TURN / BOTH',
  `evaluator_kind` varchar(24) NOT NULL COMMENT '评估机制: SYSTEM_ONE / DETERMINISTIC',
  `state_fields_json` json DEFAULT NULL COMMENT 'SYSTEM_ONE可见的state标准字段列表，相同集合自动单请求合批',
  `questions_json` json DEFAULT NULL COMMENT 'SYSTEM_ONE原子题数组(noul / worst-first score / 带option_scores的choice)',
  `deterministic_rule_json` json DEFAULT NULL COMMENT 'DETERMINISTIC规则配置(如 json_exact_or_in / numeric_fact_grounding)',
  `default_threshold` decimal(5,4) NOT NULL DEFAULT 0.7000 COMMENT '默认及格阈值 [0,1]',
  `strict_mode` tinyint(1) NOT NULL DEFAULT 0 COMMENT '严格模式(1=任一适用子题未满分则整维度置0)',
  `default_weight` decimal(6,3) NOT NULL DEFAULT 1.000 COMMENT '在套件中的默认合成权重',
  `lint_passed` tinyint(1) NOT NULL DEFAULT 1 COMMENT '题干静态Linter是否无ERROR通过',
  `lint_report_json` json DEFAULT NULL COMMENT '题干静态Linter诊断明细JSON',
  `status` varchar(16) NOT NULL DEFAULT 'ACTIVE' COMMENT '状态: DRAFT / ACTIVE / DEPRECATED',
  `description` varchar(500) DEFAULT NULL COMMENT '维度说明',
  `created_by` varchar(64) DEFAULT NULL COMMENT '创建人',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0 COMMENT '逻辑删除标志',
  `create_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `update_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ev_dim_uuid` (`uuid`),
  UNIQUE KEY `uk_ev_dim_code_ver` (`dimension_code`, `version`),
  KEY `idx_ev_dim_cat_status` (`category`, `status`, `is_deleted`)
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci ROW_FORMAT = Dynamic COMMENT = '评测中心-评价维度不可变版本表';

-- ----------------------------------------------------------------------------
-- 6. 评测套件配置表
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `ev_suite` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` char(32) NOT NULL COMMENT '业务UUID',
  `suite_code` varchar(64) NOT NULL COMMENT '套件唯一编码',
  `suite_name` varchar(128) NOT NULL COMMENT '套件名称',
  `eval_level` varchar(16) NOT NULL COMMENT '评测层级: E2E / COMPONENT',
  `default_adapter_id` bigint UNSIGNED DEFAULT NULL COMMENT '默认关联被测适配器ID',
  `default_provider_id` bigint UNSIGNED DEFAULT NULL COMMENT '默认关联判别模型渠道ID',
  `default_model_override` varchar(128) DEFAULT NULL COMMENT '可选覆盖判别模型名',
  `dimensions_json` json NOT NULL COMMENT '绑定维度版本数组: [{"dimension_code":"...","version":1,"weight":2.0,"threshold":0.75,"is_blocker":true}]',
  `pass_policy` varchar(32) NOT NULL DEFAULT 'ALL_BLOCKERS_AND_WEIGHTED' COMMENT '门禁策略: ALL_DIMENSIONS / ALL_BLOCKERS_AND_WEIGHTED',
  `suite_pass_threshold` decimal(5,4) NOT NULL DEFAULT 0.7500 COMMENT '套件加权总分及格线 [0,1]',
  `confidence_review_floor` decimal(5,4) NOT NULL DEFAULT 0.5000 COMMENT '原子题最低把握度低于此值时标记人工复核',
  `enable_semantic_regression` tinyint(1) NOT NULL DEFAULT 1 COMMENT '指定基线批次时是否自动追加四选一语义回归比对题',
  `status` varchar(16) NOT NULL DEFAULT 'ACTIVE' COMMENT '状态: ACTIVE / ARCHIVED',
  `description` varchar(500) DEFAULT NULL COMMENT '套件用途说明',
  `created_by` varchar(64) DEFAULT NULL COMMENT '创建人',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0 COMMENT '逻辑删除标志',
  `create_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `update_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ev_suite_uuid` (`uuid`),
  UNIQUE KEY `uk_ev_suite_code` (`suite_code`)
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci ROW_FORMAT = Dynamic COMMENT = '评测中心-评测套件门禁表';

-- ----------------------------------------------------------------------------
-- 7. 测试集版本主表
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `ev_dataset` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` char(32) NOT NULL COMMENT '业务UUID',
  `dataset_code` varchar(64) NOT NULL COMMENT '测试集编码',
  `version_tag` varchar(32) NOT NULL DEFAULT 'v1.0' COMMENT '测试集版本标签',
  `dataset_name` varchar(128) NOT NULL COMMENT '测试集名称',
  `eval_level` varchar(16) NOT NULL COMMENT '适用层级: E2E / COMPONENT',
  `desensitize_policy_snapshot` varchar(96) DEFAULT NULL COMMENT '调用数据脱敏中心使用的策略与版本快照(如 gov_eval_corpus:v1)',
  `dev_case_count` int NOT NULL DEFAULT 0 COMMENT '已审核生效的DEV调优集用例数',
  `holdout_case_count` int NOT NULL DEFAULT 0 COMMENT '已审核生效的HOLDOUT留出集用例数',
  `status` varchar(16) NOT NULL DEFAULT 'DRAFT' COMMENT '状态: DRAFT / PUBLISHED / ARCHIVED',
  `description` varchar(500) DEFAULT NULL COMMENT '采样范围与构建说明',
  `created_by` varchar(64) DEFAULT NULL COMMENT '创建人',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0 COMMENT '逻辑删除标志',
  `create_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `update_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ev_ds_uuid` (`uuid`),
  UNIQUE KEY `uk_ev_ds_code_ver` (`dataset_code`, `version_tag`),
  KEY `idx_ev_ds_level_status` (`eval_level`, `status`, `is_deleted`)
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci ROW_FORMAT = Dynamic COMMENT = '评测中心-通用测试集版本表';

-- ----------------------------------------------------------------------------
-- 8. 通用黄金测试用例表（零特定业务硬编码列，严格隔离 DEV / HOLDOUT）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `ev_case` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` char(32) NOT NULL COMMENT '业务UUID',
  `dataset_id` bigint UNSIGNED NOT NULL COMMENT '所属测试集ID(关联 ev_dataset.id)',
  `case_code` varchar(64) NOT NULL COMMENT '测试集内唯一用例编码',
  `dataset_split` varchar(16) NOT NULL DEFAULT 'HOLDOUT' COMMENT '防过拟合分集: DEV(调参集) / HOLDOUT(独立验真集)',
  `source_kind` varchar(24) NOT NULL DEFAULT 'MANUAL' COMMENT '来源方式: ONLINE_PULL / FILE_IMPORT / HARNESS_RECORD / MANUAL / SYNTHETIC',
  `source_ref_json` json DEFAULT NULL COMMENT '源系统血缘追踪标识JSON(仅存request_id等非敏感ID)',
  `turn_mode` varchar(16) NOT NULL DEFAULT 'SINGLE_TURN' COMMENT '轮次模式: SINGLE_TURN / MULTI_TURN',
  `input_text` mediumtext NOT NULL COMMENT '已脱敏的当前轮用户输入文本',
  `conversation_turns_json` json DEFAULT NULL COMMENT '已脱敏的多轮对话上下文 [{"role":"user|assistant","content":"..."}]',
  `expected_output` mediumtext DEFAULT NULL COMMENT '人工校准后的标准参考答案或核心要点(单轮Ground Truth)',
  `expected_outcome` text DEFAULT NULL COMMENT '多轮交互期望达成的最终业务目标',
  `reference_contexts_json` json DEFAULT NULL COMMENT '冻结的标准参考知识片段数组(可选)',
  `expected_structured_json` json DEFAULT NULL COMMENT '通用结构化期望输出(如期望命中的agent_ids、route_type、usable标签)',
  `sut_input_json` json DEFAULT NULL COMMENT '传给SUT适配器的业务附加参数(如 selected_region、route_mode)',
  `sut_context_json` json DEFAULT NULL COMMENT '组件级评测冻结的组件回放快照(如当时的agents目录数组与候选mock回复)',
  `tags_json` json DEFAULT NULL COMMENT '通用分类标签数组',
  `difficulty` varchar(16) NOT NULL DEFAULT 'NORMAL' COMMENT '难度等级: EASY / NORMAL / HARD / ADVERSARIAL',
  `pii_masked` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否已经过脱敏确认(0未脱敏禁止置为APPROVED)',
  `desensitize_summary_json` json DEFAULT NULL COMMENT '脱敏命中标签计数摘要(严禁存明文)',
  `uncertainty_score` decimal(5,4) DEFAULT NULL COMMENT '不确定度评分[0,1]，越高越优先排入人工校准队列',
  `review_status` varchar(20) NOT NULL DEFAULT 'PENDING_REVIEW' COMMENT '审核状态: DRAFT / PENDING_REVIEW / APPROVED / DISABLED',
  `reviewed_by` varchar(64) DEFAULT NULL COMMENT '校准审核人',
  `reviewed_at` datetime(3) DEFAULT NULL COMMENT '审核时间',
  `review_note` varchar(500) DEFAULT NULL COMMENT '审核备注',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0 COMMENT '逻辑删除标志',
  `create_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `update_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ev_case_uuid` (`uuid`),
  UNIQUE KEY `uk_ev_ds_case_code` (`dataset_id`, `case_code`),
  KEY `idx_ev_case_split_review` (`dataset_id`, `dataset_split`, `review_status`, `is_deleted`),
  KEY `idx_ev_case_uncertainty` (`dataset_id`, `review_status`, `uncertainty_score`)
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci ROW_FORMAT = Dynamic COMMENT = '评测中心-通用黄金测试用例表';

-- ----------------------------------------------------------------------------
-- 9. 实验评测批次主表（锁定判别模型快照与被测系统版本指纹）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `ev_run` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` char(32) NOT NULL COMMENT '业务UUID',
  `run_no` varchar(64) NOT NULL COMMENT '实验批次号(如 RUN_20261112_0001)',
  `run_name` varchar(128) NOT NULL COMMENT '实验批次名称',
  `suite_id` bigint UNSIGNED NOT NULL COMMENT '评测套件ID(关联 ev_suite.id)',
  `dataset_id` bigint UNSIGNED NOT NULL COMMENT '测试集ID(关联 ev_dataset.id)',
  `dataset_split_filter` varchar(16) NOT NULL DEFAULT 'HOLDOUT' COMMENT '本次执行的分集范围: DEV / HOLDOUT / ALL',
  `sut_adapter_id` bigint UNSIGNED NOT NULL COMMENT '执行的被测适配器ID(关联 ev_sut_adapter.id)',
  `sut_fingerprint_json` json NOT NULL COMMENT '被测版本指纹JSON(如 git_sha、prompt_hash、config_overrides)',
  `provider_id` bigint UNSIGNED NOT NULL COMMENT '判别模型渠道ID(关联 ev_decision_provider.id)',
  `pinned_provider_kind` varchar(32) NOT NULL COMMENT '渠道类型快照',
  `pinned_endpoint_url` varchar(255) NOT NULL COMMENT '判别端点URL快照',
  `pinned_model_name` varchar(128) NOT NULL COMMENT '判别模型名称快照(如 tev1:0.8b 或 typesafe/jev-1.14.0)',
  `baseline_run_id` bigint UNSIGNED DEFAULT NULL COMMENT '回归对比基线批次ID(关联本表 id)',
  `status` varchar(20) NOT NULL DEFAULT 'PENDING' COMMENT '状态: PENDING / RUNNING / COMPLETED / FAILED / CANCELLED',
  `total_cases` int NOT NULL DEFAULT 0 COMMENT '计划执行总用例数',
  `completed_cases` int NOT NULL DEFAULT 0 COMMENT '已完成用例数',
  `passed_cases` int NOT NULL DEFAULT 0 COMMENT '通过门禁用例数',
  `failed_cases` int NOT NULL DEFAULT 0 COMMENT '未通过门禁用例数',
  `error_cases` int NOT NULL DEFAULT 0 COMMENT 'SUT或判别调用异常用例数',
  `uncertain_cases` int NOT NULL DEFAULT 0 COMMENT '低把握度需人工复核用例数',
  `pass_rate` decimal(5,4) DEFAULT NULL COMMENT '总体通过率 [0,1]',
  `weighted_avg_score` decimal(5,4) DEFAULT NULL COMMENT '总体加权平均分 [0,1]',
  `p50_sut_latency_ms` int DEFAULT NULL COMMENT '被测系统P50响应耗时(ms)',
  `p95_sut_latency_ms` int DEFAULT NULL COMMENT '被测系统P95响应耗时(ms)',
  `p50_ttft_ms` int DEFAULT NULL COMMENT '流式模式下P50首字耗时(ms)',
  `total_decision_calls` int NOT NULL DEFAULT 0 COMMENT '合批后实际发起的System One HTTP请求次数',
  `total_input_tokens` bigint NOT NULL DEFAULT 0 COMMENT '累计消耗输入Token数',
  `total_eval_cost` decimal(12,6) NOT NULL DEFAULT 0.000000 COMMENT '累计判别费用估算',
  `dimension_summary_json` json DEFAULT NULL COMMENT '各维度均值、通过率及较基线涨跌汇总JSON',
  `regression_summary_json` json DEFAULT NULL COMMENT '语义回归分类计数汇总(SAME / FACT_DIFFERS / ACTION_DIFFERS / SPECIFICITY_DIFFERS)',
  `error_message` varchar(1000) DEFAULT NULL COMMENT '批次级错误摘要',
  `started_at` datetime(3) DEFAULT NULL COMMENT '开始时间',
  `finished_at` datetime(3) DEFAULT NULL COMMENT '完成时间',
  `triggered_by` varchar(64) DEFAULT NULL COMMENT '触发账号或API Key前缀',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0 COMMENT '逻辑删除标志',
  `create_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `update_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ev_run_uuid` (`uuid`),
  UNIQUE KEY `uk_ev_run_no` (`run_no`),
  KEY `idx_ev_run_suite_ds` (`suite_id`, `dataset_id`, `status`),
  KEY `idx_ev_run_time` (`create_time`)
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci ROW_FORMAT = Dynamic COMMENT = '评测中心-实验批次主表';

-- ----------------------------------------------------------------------------
-- 10. 单条用例评测结果快照表
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `ev_run_case` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` char(32) NOT NULL COMMENT '业务UUID',
  `run_id` bigint UNSIGNED NOT NULL COMMENT '所属批次ID(关联 ev_run.id)',
  `case_id` bigint UNSIGNED NOT NULL COMMENT '黄金用例ID(关联 ev_case.id)',
  `exec_status` varchar(20) NOT NULL DEFAULT 'SUCCESS' COMMENT '执行状态: SUCCESS / SUT_ERROR / JUDGE_ERROR',
  `actual_output` mediumtext DEFAULT NULL COMMENT '被测系统实际生成的输出正文',
  `actual_references_json` json DEFAULT NULL COMMENT '被测系统实际返回的参考知识片段列表',
  `actual_structured_json` json DEFAULT NULL COMMENT '适配器提取的结构化决策结果(如 actual_agent_id、selected_branch)',
  `sut_trace_json` json DEFAULT NULL COMMENT '被测系统返回的链路事件、诊断或回放元数据快照',
  `sut_latency_ms` int DEFAULT NULL COMMENT '被测系统总响应耗时(ms)',
  `ttft_ms` int DEFAULT NULL COMMENT '流式模式首字响应耗时(ms)',
  `overall_score` decimal(5,4) DEFAULT NULL COMMENT '本条用例套件加权总分 [0,1]',
  `overall_passed` tinyint(1) DEFAULT NULL COMMENT '本条用例是否通过套件门禁(1通过 0未通过)',
  `min_confidence` decimal(5,4) DEFAULT NULL COMMENT '所有System One原子题中的最低confidence',
  `needs_human_review` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否因低把握度标记人工复核(1是 0否)',
  `regression_tag` varchar(32) DEFAULT NULL COMMENT '相对基线语义变化分类: SAME / FACT_DIFFERS / ACTION_DIFFERS / SPECIFICITY_DIFFERS',
  `regression_confidence` decimal(5,4) DEFAULT NULL COMMENT '语义回归分类把握度',
  `error_detail` varchar(1000) DEFAULT NULL COMMENT '异常详情',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0 COMMENT '逻辑删除标志',
  `create_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `update_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ev_runcase_uuid` (`uuid`),
  UNIQUE KEY `uk_ev_runcase_pair` (`run_id`, `case_id`),
  KEY `idx_ev_runcase_filter` (`run_id`, `overall_passed`, `needs_human_review`, `regression_tag`)
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci ROW_FORMAT = Dynamic COMMENT = '评测中心-单条用例执行快照表';

-- ----------------------------------------------------------------------------
-- 11. 原始概率账本明细表（支撑 0 API 成本实时重算阈值与扣分归因）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `ev_probability_ledger` (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `uuid` char(32) NOT NULL COMMENT '业务UUID',
  `run_id` bigint UNSIGNED NOT NULL COMMENT '所属批次ID(冗余加速整批内存加载重算)',
  `run_case_id` bigint UNSIGNED NOT NULL COMMENT '单条执行ID(关联 ev_run_case.id)',
  `dimension_id` bigint UNSIGNED NOT NULL COMMENT '评价维度版本ID(关联 ev_dimension.id)',
  `dimension_code` varchar(64) NOT NULL COMMENT '维度编码快照',
  `dimension_version` int NOT NULL COMMENT '维度版本快照',
  `evaluator_kind` varchar(24) NOT NULL COMMENT '评估机制快照: SYSTEM_ONE / DETERMINISTIC',
  `raw_dimension_score` decimal(6,5) DEFAULT NULL COMMENT '未经阈值截断的连续维度得分 [0,1]',
  `applied_threshold` decimal(5,4) NOT NULL COMMENT '跑批当时应用的及格阈值',
  `applied_weight` decimal(6,3) NOT NULL DEFAULT 1.000 COMMENT '跑批当时应用的维度权重',
  `is_blocker` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否为一票否决维度',
  `passed` tinyint(1) DEFAULT NULL COMMENT '在当时阈值下是否达标(1达标 0未达标)',
  `min_question_confidence` decimal(5,4) DEFAULT NULL COMMENT '该维度下各适用子题的最低把握度',
  `deterministic_reason` text DEFAULT NULL COMMENT '由子题概率或规则自动拼装的确定性扣分归因文本',
  `raw_answers_json` json NOT NULL COMMENT '原子题全档位概率分布与适用性明细JSON(零成本重算数据源)',
  `waf_fallback_used` tinyint(1) NOT NULL DEFAULT 0 COMMENT '是否触发了WAF 403剥代码块重试',
  `upstream_request_id` varchar(128) DEFAULT NULL COMMENT '上游渠道响应头 X-Request-ID(便于中转站对账排障)',
  `eval_latency_ms` int DEFAULT NULL COMMENT '该批次判别请求分摊耗时(ms)',
  `input_tokens` int DEFAULT NULL COMMENT '该判别消耗输入Token数',
  `is_deleted` tinyint(1) NOT NULL DEFAULT 0 COMMENT '逻辑删除标志',
  `create_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
  `update_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ev_ledger_uuid` (`uuid`),
  UNIQUE KEY `uk_ev_ledger_case_dim` (`run_case_id`, `dimension_id`),
  KEY `idx_ev_ledger_run_dim` (`run_id`, `dimension_code`, `passed`)
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci ROW_FORMAT = Dynamic COMMENT = '评测中心-原始概率账本明细表';
