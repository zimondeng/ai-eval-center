from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.kernel.scoring import _read_field, parse_choice_answer

SEMANTIC_REGRESSION_TAGS: tuple[str, ...] = (
    "SAME",
    "FACT_DIFFERS",
    "ACTION_DIFFERS",
    "SPECIFICITY_DIFFERS",
)

SEMANTIC_REGRESSION_QUESTION_KEY = "semantic_regression_v1__diff"

SEMANTIC_REGRESSION_CRITERIA: dict[str, str] = {
    "SAME": "核心政策结论、办理条件与操作指引实质一致（仅措辞、排版或语序不同）",
    "FACT_DIFFERS": "关键事实、条件、电话、时限或金额发生实质变化",
    "ACTION_DIFFERS": "建议用户采取的办理渠道、所需材料或下一步动作发生变化",
    "SPECIFICITY_DIFFERS": "无事实冲突，但信息详略程度或限定条件完整性显著变化",
}


def format_namespaced_question_key(
    dimension_code: str,
    version: int,
    question_key: str,
) -> str:
    return f"{dimension_code.strip()}_v{int(version)}__{question_key.strip()}"


def strip_question_to_wire_protocol(question_spec: Mapping[str, Any]) -> dict[str, Any]:
    """仅保留统一 System One 协议字段 (type / instructions / criteria)，剥离内部 weight 与 option_scores。"""
    q_type = str(question_spec.get("type") or "").strip().lower()
    instructions = str(question_spec.get("instructions") or "")
    wire: dict[str, Any] = {
        "type": q_type,
        "instructions": instructions,
    }
    if q_type == "score":
        criteria = question_spec.get("criteria")
        if not isinstance(criteria, Sequence) or isinstance(criteria, (str, bytes)):
            raise ValueError(f"Score 题缺少有效 criteria 数组: {question_spec!r}")
        wire["criteria"] = [str(item) for item in criteria]
    elif q_type == "choice":
        criteria = question_spec.get("criteria")
        if not isinstance(criteria, Mapping):
            raise ValueError(f"Choice 题缺少有效 criteria 字典: {question_spec!r}")
        wire["criteria"] = {str(k): str(v) for k, v in criteria.items()}
    elif q_type != "noul":
        raise ValueError(f"不支持的 System One 题型: {q_type!r}")
    return wire


def build_canonical_state(
    case: Any,
    *,
    actual_output: str | None = None,
    actual_references: Sequence[str] | None = None,
    state_fields: Sequence[str],
) -> dict[str, Any]:
    """按声明的 state_fields 组装标准判别上下文 state 字典。"""
    input_val = _read_field(case, "input_text", "input", default="")
    expected_output_val = _read_field(case, "expected_output", default=None)
    expected_outcome_val = _read_field(case, "expected_outcome", default=None)
    conv_turns_val = _read_field(
        case, "conversation_turns_json", "conversation_turns", default=None
    )
    case_refs = _read_field(
        case, "reference_contexts_json", "references", default=None
    )

    if actual_output is None:
        resolved_actual_out = _read_field(case, "actual_output", default="")
    else:
        resolved_actual_out = actual_output

    if actual_references is not None and len(actual_references) > 0:
        resolved_refs = [str(r) for r in actual_references]
    elif isinstance(case_refs, Sequence) and not isinstance(case_refs, (str, bytes)):
        resolved_refs = [str(r) for r in case_refs]
    else:
        resolved_refs = []

    resolved_turns = (
        list(conv_turns_val)
        if isinstance(conv_turns_val, Sequence) and not isinstance(conv_turns_val, (str, bytes))
        else []
    )

    canonical_pool: dict[str, Any] = {
        "input": "" if input_val is None else str(input_val),
        "actual_output": "" if resolved_actual_out is None else str(resolved_actual_out),
        "expected_output": "" if expected_output_val is None else str(expected_output_val),
        "references": resolved_refs,
        "conversation_turns": resolved_turns,
        "expected_outcome": "" if expected_outcome_val is None else str(expected_outcome_val),
    }

    state: dict[str, Any] = {}
    for field_name in state_fields:
        if field_name in canonical_pool:
            state[field_name] = canonical_pool[field_name]
        else:
            state[field_name] = _read_field(case, field_name, default=None)
    return state


@dataclass(slots=True, frozen=True)
class CompiledQuestionRef:
    namespaced_key: str
    dimension_code: str
    dimension_version: int
    question_key: str
    question_spec: dict[str, Any]


@dataclass(slots=True)
class CompiledBatchCall:
    state_fields: tuple[str, ...]
    state: dict[str, Any]
    questions: dict[str, dict[str, Any]]
    question_refs: dict[str, CompiledQuestionRef] = field(default_factory=dict)


@dataclass(slots=True)
class CompiledBatchPlan:
    batches: list[CompiledBatchCall]

    def unpack_answers(
        self,
        batch_answers_list: Sequence[Mapping[str, Any]],
    ) -> dict[tuple[str, int], dict[str, Any]]:
        if len(batch_answers_list) != len(self.batches):
            raise ValueError(
                f"批次响应数量({len(batch_answers_list)})与编译批次数({len(self.batches)})不一致"
            )
        by_dim: dict[tuple[str, int], dict[str, Any]] = {}
        for batch_call, ans_map in zip(self.batches, batch_answers_list, strict=True):
            for ns_key, q_ref in batch_call.question_refs.items():
                if ns_key not in ans_map:
                    raise ValueError(f"上游响应缺失合批题目: {ns_key}")
                dim_key = (q_ref.dimension_code, q_ref.dimension_version)
                if dim_key not in by_dim:
                    by_dim[dim_key] = {}
                by_dim[dim_key][q_ref.question_key] = ans_map[ns_key]
        return by_dim


class StateBatchCompiler:
    """同上下文多维度合批编译器：按规范化 state_fields 分组，单批最多 max_questions_per_call 题。"""

    def __init__(self, max_questions_per_call: int = 64) -> None:
        if max_questions_per_call < 1:
            raise ValueError("max_questions_per_call 必须 >= 1")
        self.max_questions_per_call = int(max_questions_per_call)

    def compile(
        self,
        *,
        dimensions: Sequence[Any],
        case: Any,
        actual_output: str | None = None,
        actual_references: Sequence[str] | None = None,
        max_questions_override: int | None = None,
    ) -> CompiledBatchPlan:
        chunk_limit = (
            int(max_questions_override)
            if max_questions_override is not None
            else self.max_questions_per_call
        )
        if chunk_limit < 1:
            raise ValueError("合批上限必须 >= 1")

        grouped_order: list[tuple[str, ...]] = []
        grouped_items: dict[tuple[str, ...], list[tuple[str, dict[str, Any], CompiledQuestionRef]]] = {}

        for dim in dimensions:
            eval_kind = str(_read_field(dim, "evaluator_kind", default="SYSTEM_ONE")).upper()
            if eval_kind != "SYSTEM_ONE":
                continue

            dim_code = str(_read_field(dim, "dimension_code")).strip()
            dim_ver = int(_read_field(dim, "version", "dimension_version", default=1))
            raw_fields = _read_field(dim, "state_fields_json", "state_fields", default=None) or []
            norm_fields = tuple(sorted({str(f).strip() for f in raw_fields if str(f).strip()}))
            if not norm_fields:
                raise ValueError(f"SYSTEM_ONE 维度 {dim_code}@v{dim_ver} 的 state_fields 为空")

            raw_questions = _read_field(dim, "questions_json", "questions", default=None) or []
            if not raw_questions:
                continue

            if norm_fields not in grouped_items:
                grouped_order.append(norm_fields)
                grouped_items[norm_fields] = []

            bucket = grouped_items[norm_fields]
            for q_raw in raw_questions:
                if not isinstance(q_raw, Mapping):
                    raise ValueError(f"维度 {dim_code}@v{dim_ver} 包含非法原子题定义")
                q_key = str(q_raw["key"]).strip()
                ns_key = format_namespaced_question_key(dim_code, dim_ver, q_key)
                wire_q = strip_question_to_wire_protocol(q_raw)
                ref = CompiledQuestionRef(
                    namespaced_key=ns_key,
                    dimension_code=dim_code,
                    dimension_version=dim_ver,
                    question_key=q_key,
                    question_spec=dict(q_raw),
                )
                bucket.append((ns_key, wire_q, ref))

        batches: list[CompiledBatchCall] = []
        for state_fields_key in grouped_order:
            items = grouped_items[state_fields_key]
            state_dict = build_canonical_state(
                case,
                actual_output=actual_output,
                actual_references=actual_references,
                state_fields=state_fields_key,
            )
            for start in range(0, len(items), chunk_limit):
                chunk = items[start : start + chunk_limit]
                q_dict: dict[str, dict[str, Any]] = {}
                ref_dict: dict[str, CompiledQuestionRef] = {}
                for ns_key, wire_q, ref in chunk:
                    if ns_key in q_dict:
                        raise ValueError(f"合批命名空间键冲突: {ns_key}")
                    q_dict[ns_key] = wire_q
                    ref_dict[ns_key] = ref
                batches.append(
                    CompiledBatchCall(
                        state_fields=state_fields_key,
                        state=dict(state_dict),
                        questions=q_dict,
                        question_refs=ref_dict,
                    )
                )

        return CompiledBatchPlan(batches=batches)


def build_semantic_regression_question() -> dict[str, Any]:
    """构造双批次四分类语义回归比对 Choice 题 (docs/02 §5.1)。"""
    return {
        "type": "choice",
        "instructions": (
            "对比 baseline_output 与 current_output 对同一用户问题 input 的回答，"
            "判断两者在实质政策结论、办理条件或操作指引上的语义差异类别："
        ),
        "criteria": dict(SEMANTIC_REGRESSION_CRITERIA),
    }


def build_semantic_regression_state(
    *,
    input_text: str,
    baseline_output: str,
    current_output: str,
) -> dict[str, str]:
    return {
        "input": input_text or "",
        "baseline_output": baseline_output or "",
        "current_output": current_output or "",
    }


def parse_semantic_regression_answer(
    raw_answer: Any,
) -> tuple[str, float, dict[str, float]]:
    """解析四分类语义回归响应，返回 (regression_tag, regression_confidence, probabilities)。"""
    probs_map, chosen, conf = parse_choice_answer(raw_answer, SEMANTIC_REGRESSION_CRITERIA)
    if chosen not in SEMANTIC_REGRESSION_TAGS:
        raise ValueError(f"非法的语义回归分类标签: {chosen!r}")
    return chosen, conf, probs_map
