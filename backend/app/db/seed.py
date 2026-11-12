from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.common.crypto import hash_password
from app.config import Settings, get_settings
from app.db.base import Base
from app.db.models import AdminUser, DecisionProvider, Dimension, Suite, SutAdapter
from app.db.session import build_session_factory

DEFAULT_PROVIDERS: tuple[dict[str, Any], ...] = (
    {
        "provider_code": "ollama_local_tev1",
        "provider_name": "本地 Ollama Tev1 轻量决策模型 (0.8B)",
        "provider_kind": "OLLAMA_LOCAL",
        "endpoint_url": "http://127.0.0.1:11434/v1/systemone",
        "models_list_url": "http://127.0.0.1:11434/v1/models",
        "default_model": "tev1:0.8b",
        "auth_type": "NONE",
        "api_key_env_name": None,
        "network_mode": "DIRECT",
        "max_questions_per_call": 64,
        "max_options_per_question": 24,
        "waf_strip_code_retry": 0,
        "cost_per_1m_input_tokens": 0.0,
        "extra_options_json": {"keep_alive": "15m"},
        "is_default": 1,
        "status": "ACTIVE",
        "created_by": "system",
    },
    {
        "provider_code": "emrank_decisions_relay",
        "provider_name": "EMRank 第三方决策中转站 (/v1/decisions)",
        "provider_kind": "DECISION_RELAY",
        "endpoint_url": "https://emrank.tech/v1/decisions",
        "models_list_url": "https://emrank.tech/v1/models",
        "default_model": "typesafe/jev-1.14.0",
        "auth_type": "ENV_REF",
        "api_key_env_name": "EMRANK_API_KEY",
        "network_mode": "PROXY_FALLBACK",
        "max_questions_per_call": 64,
        "max_options_per_question": 24,
        "waf_strip_code_retry": 1,
        "cost_per_1m_input_tokens": 0.2,
        "extra_options_json": None,
        "is_default": 0,
        "status": "ACTIVE",
        "created_by": "system",
    },
    {
        "provider_code": "typesafe_official",
        "provider_name": "TypeSafe AI 官方云端 (/v1/systemone)",
        "provider_kind": "TYPESAFE_OFFICIAL",
        "endpoint_url": "https://api.typesafe.ai/v1/systemone",
        "models_list_url": None,
        "default_model": "jev-1.14.0",
        "auth_type": "ENV_REF",
        "api_key_env_name": "TYPESAFE_API_KEY",
        "network_mode": "PROXY_FALLBACK",
        "max_questions_per_call": 64,
        "max_options_per_question": 24,
        "waf_strip_code_retry": 1,
        "cost_per_1m_input_tokens": 0.2,
        "extra_options_json": None,
        "is_default": 0,
        "status": "ACTIVE",
        "created_by": "system",
    },
)

DEFAULT_ADAPTERS: tuple[dict[str, Any], ...] = (
    {
        "adapter_code": "gov_e2e_routing_blocking",
        "adapter_name": "政务智能客服-智能路由总入口 E2E (Blocking)",
        "eval_level": "E2E",
        "adapter_type": "HTTP_JSON",
        "endpoint_url": "http://127.0.0.1:8080/ai/invoke",
        "record_endpoint_url": None,
        "auth_config_json": {
            "header": "Authorization",
            "prefix": "Bearer ",
            "env_name": "EVAL_JAVA_BEARER_TOKEN",
        },
        "request_mapping_json": {
            "body_template": {"responseMode": "blocking", "routeMode": "auto"},
            "field_map": {
                "query": "$.input_text",
                "selectedRegion": "$.sut_input_json.selected_region",
                "routeMode": "$.sut_input_json.route_mode",
                "targetAgentId": "$.sut_input_json.target_agent_id",
            },
        },
        "response_mapping_json": {
            "actual_output": "$.data.answer",
            "actual_references": "$.data.references[*].content",
            "actual_structured": {
                "actual_agent_id": "$.data.finalAgentId",
                "selected_branch": "$.data.selectedBranch",
                "final_route_type": "$.data.finalRouteType",
                "request_id": "$.data.requestId",
            },
            "sut_trace": "$.data.events",
        },
        "description": "直接请求 Java 后端 POST /ai/invoke (blocking 模式)，验证智能路由全链路回答、引用与分支选择",
        "created_by": "system",
    },
    {
        "adapter_code": "gov_e2e_normal_chat",
        "adapter_name": "政务智能客服-普通问答入口 E2E",
        "eval_level": "E2E",
        "adapter_type": "HTTP_JSON",
        "endpoint_url": "http://127.0.0.1:8080/ai/chat",
        "record_endpoint_url": None,
        "auth_config_json": {
            "header": "Authorization",
            "prefix": "Bearer ",
            "env_name": "EVAL_JAVA_BEARER_TOKEN",
        },
        "request_mapping_json": {
            "body_template": {},
            "field_map": {
                "query": "$.input_text",
                "appCode": "$.sut_input_json.app_code",
            },
        },
        "response_mapping_json": {
            "actual_output": "$.data.answer",
            "actual_references": "$.data.references[*].content",
            "actual_structured": {},
            "sut_trace": "$.data",
        },
        "description": "直接请求 Java 后端普通对话接口 POST /ai/chat",
        "created_by": "system",
    },
    {
        "adapter_code": "gov_comp_routing_harness",
        "adapter_name": "政务智能客服-Python路由与汇总组件隔离回放 Harness",
        "eval_level": "COMPONENT",
        "adapter_type": "COMPONENT_HARNESS",
        "endpoint_url": "http://127.0.0.1:8000/internal/eval-harness/replay",
        "record_endpoint_url": "http://127.0.0.1:8000/internal/eval-harness/record",
        "auth_config_json": {
            "header": "X-Eval-Harness-Token",
            "prefix": "",
            "env_name": "ROUTING_EVAL_HARNESS_TOKEN",
        },
        "request_mapping_json": {
            "field_map": {
                "component": "$.sut_input_json.component",
                "query": "$.input_text",
                "turns": "$.conversation_turns_json",
                "sut_context": "$.sut_context_json",
                "overrides": "$.run_overrides",
            }
        },
        "response_mapping_json": {
            "actual_output": "$.data.actual_output",
            "actual_references": "$.data.references",
            "actual_structured": "$.data.structured_decision",
            "sut_trace": "$.data.trace",
        },
        "description": "对接 gov-service-ai-assistant/python 测试态回放网关，支持 FastRouter / SupervisorRouter / TeamRoutingRunner / AnswerQualityEvaluator 隔离消融",
        "created_by": "system",
    },
)

DEFAULT_DIMENSIONS: tuple[dict[str, Any], ...] = (
    {
        "dimension_code": "route_top1_match",
        "version": 1,
        "dimension_name": "路由目标 Top-1 命中率",
        "category": "ROUTING",
        "target_turn_mode": "SINGLE_TURN",
        "evaluator_kind": "DETERMINISTIC",
        "state_fields_json": None,
        "questions_json": None,
        "deterministic_rule_json": {
            "rule_type": "json_exact_or_in",
            "actual_path": "$.actual_agent_id",
            "expected_path": "$.expected_agent_ids",
        },
        "default_threshold": 1.0,
        "default_weight": 1.5,
        "lint_passed": 1,
        "status": "ACTIVE",
        "description": "零成本确定性校验实际选中的智能体稳定路由标识是否落在期望智能体列表中",
        "created_by": "system",
    },
    {
        "dimension_code": "numeric_fact_grounding",
        "version": 1,
        "dimension_name": "电话/金额/时限数字事实核对",
        "category": "GROUNDING",
        "target_turn_mode": "SINGLE_TURN",
        "evaluator_kind": "DETERMINISTIC",
        "state_fields_json": None,
        "questions_json": None,
        "deterministic_rule_json": {
            "rule_type": "numeric_fact_grounding",
            "check_types": ["phone", "amount", "workdays"],
            "grounding_sources": ["references", "expected_output"],
        },
        "default_threshold": 1.0,
        "default_weight": 1.5,
        "lint_passed": 1,
        "status": "ACTIVE",
        "description": "纯代码提取回答中的电话号码、金额与办理工作日数，校验是否真实存在于参考材料或标准答案中",
        "created_by": "system",
    },
    {
        "dimension_code": "rag_faithfulness_s1",
        "version": 1,
        "dimension_name": "知识引用忠实度与零编造 (System One)",
        "category": "GROUNDING",
        "target_turn_mode": "SINGLE_TURN",
        "evaluator_kind": "SYSTEM_ONE",
        "state_fields_json": ["input", "actual_output", "references"],
        "questions_json": [
            {
                "key": "grounded_in_refs",
                "type": "score",
                "weight": 2.0,
                "instructions": "actual_output 中给出的实质性办理结论、条件与流程在多大程度上由 references 支撑？",
                "criteria": [
                    "大量关键结论在 references 中毫无依据或与 references 矛盾",
                    "部分结论有依据，但夹杂了 references 未提及的具体条件或流程",
                    "绝大部分内容有依据，仅有不影响结论的常识性衔接表述",
                    "所有实质性结论、条件与操作细节均完全忠实于 references",
                ],
            },
            {
                "key": "honest_on_missing",
                "type": "choice",
                "weight": 1.5,
                "instructions": "对于用户提问中涉及但 references 未提供的信息，actual_output 如何处理？",
                "criteria": {
                    "fabricated_as_fact": "将 references 中不存在的具体规定或材料当作确定事实写出",
                    "vague_guess": "做了模棱两可的猜测且未提示需核实",
                    "honest_boundary": "明确说明暂未查到或引导通过官方咨询渠道核实",
                    "nothing_missing": "references 已完整覆盖用户所问内容，不存在缺失项",
                },
                "option_scores": {
                    "fabricated_as_fact": 0.0,
                    "vague_guess": 0.4,
                    "honest_boundary": 1.0,
                    "nothing_missing": None,
                },
            },
        ],
        "deterministic_rule_json": None,
        "default_threshold": 0.75,
        "default_weight": 2.0,
        "lint_passed": 1,
        "status": "ACTIVE",
        "description": "基于 System One 原语评估生成回答相对召回知识片段的忠实度与边界诚实性",
        "created_by": "system",
    },
    {
        "dimension_code": "policy_and_action_accuracy_s1",
        "version": 1,
        "dimension_name": "政策结论与办事指引准确性 (System One)",
        "category": "ACCURACY",
        "target_turn_mode": "SINGLE_TURN",
        "evaluator_kind": "SYSTEM_ONE",
        "state_fields_json": ["input", "actual_output", "expected_output", "references"],
        "questions_json": [
            {
                "key": "core_conclusion",
                "type": "score",
                "weight": 2.5,
                "instructions": "对比 expected_output，actual_output 给出的核心办理结论与适用条件是否准确？",
                "criteria": [
                    "核心结论错误，或与 expected_output 直接相反",
                    "核心结论部分正确，但遗漏关键前置限制条件",
                    "核心结论与关键限制条件均与 expected_output 一致",
                ],
            },
            {
                "key": "no_contradiction",
                "type": "noul",
                "weight": 2.0,
                "instructions": "actual_output 中没有任何与 expected_output 相冲突的材料要求、办理时限或收费说明。",
            },
            {
                "key": "channel_completeness",
                "type": "choice",
                "weight": 1.5,
                "instructions": "当用户咨询如何办理时，actual_output 给出的办理渠道与所需材料是否清晰可操作？",
                "criteria": {
                    "missing_both": "未提供具体办理渠道或材料，或信息混乱无法执行",
                    "partial_info": "仅提供材料或渠道其中一项，指引不够完整",
                    "actionable_complete": "清晰完整地给出了所需材料与线上/线下办理渠道",
                    "not_applicable": "用户仅询问概念解释，不涉及具体申办步骤",
                },
                "option_scores": {
                    "missing_both": 0.0,
                    "partial_info": 0.5,
                    "actionable_complete": 1.0,
                    "not_applicable": None,
                },
            },
        ],
        "deterministic_rule_json": None,
        "default_threshold": 0.75,
        "default_weight": 2.5,
        "lint_passed": 1,
        "status": "ACTIVE",
        "description": "对比人工校准的标准答案评估政策结论、前置条件与申办指引的准确性",
        "created_by": "system",
    },
)


def seed_defaults(session: Session, settings: Settings | None = None) -> None:
    cfg = settings or get_settings()

    existing_admin = session.scalar(
        select(AdminUser).where(AdminUser.username == cfg.default_admin_username)
    )
    if existing_admin is None:
        session.add(
            AdminUser(
                username=cfg.default_admin_username,
                display_name=cfg.default_admin_display_name,
                password_hash=hash_password(cfg.default_admin_password),
                role="ADMIN",
                is_enabled=1,
                is_deleted=0,
            )
        )

    has_default_provider = (
        session.scalar(
            select(DecisionProvider).where(
                DecisionProvider.is_default == 1,
                DecisionProvider.is_deleted == 0,
            )
        )
        is not None
    )
    providers_by_code: dict[str, DecisionProvider] = {}
    for item in DEFAULT_PROVIDERS:
        code = item["provider_code"]
        existing_p = session.scalar(
            select(DecisionProvider).where(DecisionProvider.provider_code == code)
        )
        if existing_p is None:
            payload = dict(item)
            if payload.get("is_default") == 1 and has_default_provider:
                payload["is_default"] = 0
            existing_p = DecisionProvider(**payload)
            session.add(existing_p)
            if payload.get("is_default") == 1:
                has_default_provider = True
        providers_by_code[code] = existing_p

    adapters_by_code: dict[str, SutAdapter] = {}
    for item in DEFAULT_ADAPTERS:
        code = item["adapter_code"]
        existing_a = session.scalar(
            select(SutAdapter).where(SutAdapter.adapter_code == code)
        )
        if existing_a is None:
            existing_a = SutAdapter(**item)
            session.add(existing_a)
        adapters_by_code[code] = existing_a

    for item in DEFAULT_DIMENSIONS:
        existing_d = session.scalar(
            select(Dimension).where(
                Dimension.dimension_code == item["dimension_code"],
                Dimension.version == item["version"],
            )
        )
        if existing_d is None:
            session.add(Dimension(**item))

    session.flush()

    default_provider_id = providers_by_code["ollama_local_tev1"].id
    e2e_adapter_id = adapters_by_code["gov_e2e_routing_blocking"].id
    comp_adapter_id = adapters_by_code["gov_comp_routing_harness"].id

    default_suites: tuple[dict[str, Any], ...] = (
        {
            "suite_code": "suite_gov_e2e_golden_v1",
            "suite_name": "政务智能客服端到端黄金验收套件 (v1)",
            "eval_level": "E2E",
            "default_adapter_id": e2e_adapter_id,
            "default_provider_id": default_provider_id,
            "dimensions_json": [
                {
                    "dimension_code": "route_top1_match",
                    "version": 1,
                    "weight": 1.5,
                    "threshold": 1.0,
                    "is_blocker": False,
                },
                {
                    "dimension_code": "numeric_fact_grounding",
                    "version": 1,
                    "weight": 1.5,
                    "threshold": 1.0,
                    "is_blocker": True,
                },
                {
                    "dimension_code": "rag_faithfulness_s1",
                    "version": 1,
                    "weight": 2.0,
                    "threshold": 0.75,
                    "is_blocker": True,
                },
                {
                    "dimension_code": "policy_and_action_accuracy_s1",
                    "version": 1,
                    "weight": 2.5,
                    "threshold": 0.75,
                    "is_blocker": True,
                },
            ],
            "pass_policy": "ALL_BLOCKERS_AND_WEIGHTED",
            "suite_pass_threshold": 0.75,
            "confidence_review_floor": 0.50,
            "enable_semantic_regression": 1,
            "status": "ACTIVE",
            "description": "预置政务智能客服 E2E 黄金集发布验收门禁套件",
            "created_by": "system",
        },
        {
            "suite_code": "suite_gov_comp_routing_v1",
            "suite_name": "政务智能客服路由与汇总组件消融套件 (v1)",
            "eval_level": "COMPONENT",
            "default_adapter_id": comp_adapter_id,
            "default_provider_id": default_provider_id,
            "dimensions_json": [
                {
                    "dimension_code": "route_top1_match",
                    "version": 1,
                    "weight": 2.0,
                    "threshold": 1.0,
                    "is_blocker": True,
                },
                {
                    "dimension_code": "rag_faithfulness_s1",
                    "version": 1,
                    "weight": 2.0,
                    "threshold": 0.75,
                    "is_blocker": False,
                },
            ],
            "pass_policy": "ALL_BLOCKERS_AND_WEIGHTED",
            "suite_pass_threshold": 0.75,
            "confidence_review_floor": 0.50,
            "enable_semantic_regression": 1,
            "status": "ACTIVE",
            "description": "预置政务智能客服内部路由与汇总组件隔离回放调优套件",
            "created_by": "system",
        },
    )

    for s_item in default_suites:
        existing_s = session.scalar(
            select(Suite).where(Suite.suite_code == s_item["suite_code"])
        )
        if existing_s is None:
            session.add(Suite(**s_item))

    session.commit()


def init_db(engine: Engine, *, seed: bool = True, settings: Settings | None = None) -> None:
    Base.metadata.create_all(bind=engine)
    if seed:
        factory = build_session_factory(engine)
        with factory() as session:
            seed_defaults(session, settings=settings)
