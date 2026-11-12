-- ============================================================================
-- AI 评测中心 默认判别模型渠道、政务被测适配器与预置评价维度种子脚本
-- ============================================================================

USE `kd_eval`;

-- 1. 预置三类统一 System One 协议判别模型渠道模板
INSERT INTO `ev_decision_provider` (
  `uuid`, `provider_code`, `provider_name`, `provider_kind`, `endpoint_url`, `models_list_url`,
  `default_model`, `auth_type`, `api_key_env_name`, `network_mode`,
  `max_questions_per_call`, `max_options_per_question`, `waf_strip_code_retry`,
  `cost_per_1m_input_tokens`, `extra_options_json`, `is_default`, `status`, `created_by`
) VALUES
  (
    REPLACE(UUID(), '-', ''),
    'ollama_local_tev1',
    '本地 Ollama Tev1 轻量决策模型 (0.8B)',
    'OLLAMA_LOCAL',
    'http://127.0.0.1:11434/v1/systemone',
    'http://127.0.0.1:11434/v1/models',
    'tev1:0.8b',
    'NONE',
    NULL,
    'DIRECT',
    64, 24, 0,
    0.0000,
    JSON_OBJECT('keep_alive', '15m'),
    1,
    'ACTIVE',
    'system'
  ),
  (
    REPLACE(UUID(), '-', ''),
    'emrank_decisions_relay',
    'EMRank 第三方决策中转站 (/v1/decisions)',
    'DECISION_RELAY',
    'https://emrank.tech/v1/decisions',
    'https://emrank.tech/v1/models',
    'typesafe/jev-1.14.0',
    'ENV_REF',
    'EMRANK_API_KEY',
    'PROXY_FALLBACK',
    64, 24, 1,
    0.2000,
    NULL,
    0,
    'ACTIVE',
    'system'
  ),
  (
    REPLACE(UUID(), '-', ''),
    'typesafe_official',
    'TypeSafe AI 官方云端 (/v1/systemone)',
    'TYPESAFE_OFFICIAL',
    'https://api.typesafe.ai/v1/systemone',
    NULL,
    'jev-1.14.0',
    'ENV_REF',
    'TYPESAFE_API_KEY',
    'PROXY_FALLBACK',
    64, 24, 1,
    0.2000,
    NULL,
    0,
    'ACTIVE',
    'system'
  );

-- 2. 预置可插拔被测适配器（政务智能助手 E2E 与 Component 预设）
INSERT INTO `ev_sut_adapter` (
  `uuid`, `adapter_code`, `adapter_name`, `eval_level`, `adapter_type`,
  `endpoint_url`, `record_endpoint_url`, `auth_config_json`,
  `request_mapping_json`, `response_mapping_json`, `description`, `created_by`
) VALUES
  (
    REPLACE(UUID(), '-', ''),
    'gov_e2e_routing_blocking',
    '政务智能客服-智能路由总入口 E2E (Blocking)',
    'E2E',
    'HTTP_JSON',
    'http://127.0.0.1:8080/ai/invoke',
    NULL,
    JSON_OBJECT('header', 'Authorization', 'prefix', 'Bearer ', 'env_name', 'EVAL_JAVA_BEARER_TOKEN'),
    JSON_OBJECT(
      'body_template', JSON_OBJECT('responseMode', 'blocking', 'routeMode', 'auto'),
      'field_map', JSON_OBJECT(
        'query', '$.input_text',
        'selectedRegion', '$.sut_input_json.selected_region',
        'routeMode', '$.sut_input_json.route_mode',
        'targetAgentId', '$.sut_input_json.target_agent_id'
      )
    ),
    JSON_OBJECT(
      'actual_output', '$.data.answer',
      'actual_references', '$.data.references[*].content',
      'actual_structured', JSON_OBJECT(
        'actual_agent_id', '$.data.finalAgentId',
        'selected_branch', '$.data.selectedBranch',
        'final_route_type', '$.data.finalRouteType',
        'request_id', '$.data.requestId'
      ),
      'sut_trace', '$.data.events'
    ),
    '直接请求 Java 后端 POST /ai/invoke (blocking 模式)，验证智能路由全链路回答、引用与分支选择',
    'system'
  ),
  (
    REPLACE(UUID(), '-', ''),
    'gov_e2e_normal_chat',
    '政务智能客服-普通问答入口 E2E',
    'E2E',
    'HTTP_JSON',
    'http://127.0.0.1:8080/ai/chat',
    NULL,
    JSON_OBJECT('header', 'Authorization', 'prefix', 'Bearer ', 'env_name', 'EVAL_JAVA_BEARER_TOKEN'),
    JSON_OBJECT(
      'body_template', JSON_OBJECT(),
      'field_map', JSON_OBJECT(
        'query', '$.input_text',
        'appCode', '$.sut_input_json.app_code'
      )
    ),
    JSON_OBJECT(
      'actual_output', '$.data.answer',
      'actual_references', '$.data.references[*].content',
      'actual_structured', JSON_OBJECT(),
      'sut_trace', '$.data'
    ),
    '直接请求 Java 后端普通对话接口 POST /ai/chat',
    'system'
  ),
  (
    REPLACE(UUID(), '-', ''),
    'gov_comp_routing_harness',
    '政务智能客服-Python路由与汇总组件隔离回放 Harness',
    'COMPONENT',
    'COMPONENT_HARNESS',
    'http://127.0.0.1:8000/internal/eval-harness/replay',
    'http://127.0.0.1:8000/internal/eval-harness/record',
    JSON_OBJECT('header', 'X-Eval-Harness-Token', 'prefix', '', 'env_name', 'ROUTING_EVAL_HARNESS_TOKEN'),
    JSON_OBJECT(
      'field_map', JSON_OBJECT(
        'component', '$.sut_input_json.component',
        'query', '$.input_text',
        'turns', '$.conversation_turns_json',
        'sut_context', '$.sut_context_json',
        'overrides', '$.run_overrides'
      )
    ),
    JSON_OBJECT(
      'actual_output', '$.data.actual_output',
      'actual_references', '$.data.references',
      'actual_structured', '$.data.structured_decision',
      'sut_trace', '$.data.trace'
    ),
    '对接 gov-service-ai-assistant/python 测试态回放网关，支持 FastRouter / SupervisorRouter / TeamRoutingRunner / AnswerQualityEvaluator 隔离消融',
    'system'
  );

-- 3. 预置首批通用与政务评价维度 (v1)
INSERT INTO `ev_dimension` (
  `uuid`, `dimension_code`, `version`, `dimension_name`, `category`, `target_turn_mode`,
  `evaluator_kind`, `state_fields_json`, `questions_json`, `deterministic_rule_json`,
  `default_threshold`, `default_weight`, `lint_passed`, `status`, `description`, `created_by`
) VALUES
  (
    REPLACE(UUID(), '-', ''),
    'route_top1_match',
    1,
    '路由目标 Top-1 命中率',
    'ROUTING',
    'SINGLE_TURN',
    'DETERMINISTIC',
    NULL,
    NULL,
    JSON_OBJECT(
      'rule_type', 'json_exact_or_in',
      'actual_path', '$.actual_agent_id',
      'expected_path', '$.expected_agent_ids'
    ),
    1.0000, 1.500, 1, 'ACTIVE',
    '零成本确定性校验实际选中的智能体稳定路由标识是否落在期望智能体列表中',
    'system'
  ),
  (
    REPLACE(UUID(), '-', ''),
    'numeric_fact_grounding',
    1,
    '电话/金额/时限数字事实核对',
    'GROUNDING',
    'SINGLE_TURN',
    'DETERMINISTIC',
    NULL,
    NULL,
    JSON_OBJECT(
      'rule_type', 'numeric_fact_grounding',
      'check_types', JSON_ARRAY('phone', 'amount', 'workdays'),
      'grounding_sources', JSON_ARRAY('references', 'expected_output')
    ),
    1.0000, 1.500, 1, 'ACTIVE',
    '纯代码提取回答中的电话号码、金额与办理工作日数，校验是否真实存在于参考材料或标准答案中',
    'system'
  ),
  (
    REPLACE(UUID(), '-', ''),
    'rag_faithfulness_s1',
    1,
    '知识引用忠实度与零编造 (System One)',
    'GROUNDING',
    'SINGLE_TURN',
    'SYSTEM_ONE',
    JSON_ARRAY('input', 'actual_output', 'references'),
    JSON_ARRAY(
      JSON_OBJECT(
        'key', 'grounded_in_refs',
        'type', 'score',
        'weight', 2.0,
        'instructions', 'actual_output 中给出的实质性办理结论、条件与流程在多大程度上由 references 支撑？',
        'criteria', JSON_ARRAY(
          '大量关键结论在 references 中毫无依据或与 references 矛盾',
          '部分结论有依据，但夹杂了 references 未提及的具体条件或流程',
          '绝大部分内容有依据，仅有不影响结论的常识性衔接表述',
          '所有实质性结论、条件与操作细节均完全忠实于 references'
        )
      ),
      JSON_OBJECT(
        'key', 'honest_on_missing',
        'type', 'choice',
        'weight', 1.5,
        'instructions', '对于用户提问中涉及但 references 未提供的信息，actual_output 如何处理？',
        'criteria', JSON_OBJECT(
          'fabricated_as_fact', '将 references 中不存在的具体规定或材料当作确定事实写出',
          'vague_guess', '做了模棱两可的猜测且未提示需核实',
          'honest_boundary', '明确说明暂未查到或引导通过官方咨询渠道核实',
          'nothing_missing', 'references 已完整覆盖用户所问内容，不存在缺失项'
        ),
        'option_scores', JSON_OBJECT(
          'fabricated_as_fact', 0.0,
          'vague_guess', 0.4,
          'honest_boundary', 1.0,
          'nothing_missing', NULL
        )
      )
    ),
    NULL,
    0.7500, 2.000, 1, 'ACTIVE',
    '基于 System One 原语评估生成回答相对召回知识片段的忠实度与边界诚实性',
    'system'
  ),
  (
    REPLACE(UUID(), '-', ''),
    'policy_and_action_accuracy_s1',
    1,
    '政策结论与办事指引准确性 (System One)',
    'ACCURACY',
    'SINGLE_TURN',
    'SYSTEM_ONE',
    JSON_ARRAY('input', 'actual_output', 'expected_output', 'references'),
    JSON_ARRAY(
      JSON_OBJECT(
        'key', 'core_conclusion',
        'type', 'score',
        'weight', 2.5,
        'instructions', '对比 expected_output，actual_output 给出的核心办理结论与适用条件是否准确？',
        'criteria', JSON_ARRAY(
          '核心结论错误，或与 expected_output 直接相反',
          '核心结论部分正确，但遗漏关键前置限制条件',
          '核心结论与关键限制条件均与 expected_output 一致'
        )
      ),
      JSON_OBJECT(
        'key', 'no_contradiction',
        'type', 'noul',
        'weight', 2.0,
        'instructions', 'actual_output 中没有任何与 expected_output 相冲突的材料要求、办理时限或收费说明。'
      ),
      JSON_OBJECT(
        'key', 'channel_completeness',
        'type', 'choice',
        'weight', 1.5,
        'instructions', '当用户咨询如何办理时，actual_output 给出的办理渠道与所需材料是否清晰可操作？',
        'criteria', JSON_OBJECT(
          'missing_both', '未提供具体办理渠道或材料，或信息混乱无法执行',
          'partial_info', '仅提供材料或渠道其中一项，指引不够完整',
          'actionable_complete', '清晰完整地给出了所需材料与线上/线下办理渠道',
          'not_applicable', '用户仅询问概念解释，不涉及具体申办步骤'
        ),
        'option_scores', JSON_OBJECT(
          'missing_both', 0.0,
          'partial_info', 0.5,
          'actionable_complete', 1.0,
          'not_applicable', NULL
        )
      )
    ),
    NULL,
    0.7500, 2.500, 1, 'ACTIVE',
    '对比人工校准的标准答案评估政策结论、前置条件与申办指引的准确性',
    'system'
  );
