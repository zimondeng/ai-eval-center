export interface ApiEnvelope<T> {
  code: number
  message: string
  request_id: string
  data: T
}

export interface PaginatedResult<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

/* ---------------- Auth & API Keys ---------------- */
export interface AdminUserProfile {
  id: number
  uuid: string
  username: string
  display_name: string
  role: string
  is_enabled: boolean
  last_login_at: string | null
  create_time: string | null
  principal_type?: string
  principal_id?: number
  identifier?: string
}

export interface LoginResponseData {
  access_token: string
  token_type: string
  expires_in: number
  user: AdminUserProfile
}

export interface ApiKeyRecord {
  id: number
  uuid: string
  key_name: string
  key_prefix: string
  rate_limit_rpm: number
  status: 'ACTIVE' | 'REVOKED' | string
  expires_at: string | null
  last_used_at: string | null
  created_by: string | null
  create_time: string | null
  update_time: string | null
  api_key_plain?: string
}

export interface ApiKeyCreatePayload {
  key_name: string
  rate_limit_rpm?: number
  expires_in_days?: number | null
}

/* ---------------- Decision Providers ---------------- */
export type ProviderKind = 'OLLAMA_LOCAL' | 'DECISION_RELAY' | 'TYPESAFE_OFFICIAL'
export type ProviderAuthType = 'NONE' | 'BEARER' | 'ENV_REF'
export type ProviderNetworkMode = 'DIRECT' | 'PROXY_FALLBACK' | 'CUSTOM_PROXY'
export type ProviderStatus = 'ACTIVE' | 'DISABLED'

export interface DecisionProviderRecord {
  id: number
  uuid: string
  provider_code: string
  provider_name: string
  provider_kind: ProviderKind
  endpoint_url: string
  models_list_url: string | null
  default_model: string
  auth_type: ProviderAuthType
  api_key_env_name: string | null
  api_key_masked: string | null
  has_api_key: boolean
  network_mode: ProviderNetworkMode
  custom_proxy_url: string | null
  timeout_seconds: number
  max_concurrency: number
  max_questions_per_call: number
  max_options_per_question: number
  waf_strip_code_retry: boolean
  cost_per_1m_input_tokens: number
  extra_options_json: Record<string, unknown> | null
  is_default: boolean
  status: ProviderStatus
  last_health_ok: boolean | null
  last_checked_at: string | null
  created_by: string | null
  create_time: string | null
  update_time: string | null
}

export interface ProviderUpsertPayload {
  provider_code: string
  provider_name: string
  provider_kind: ProviderKind
  endpoint_url: string
  models_list_url?: string | null
  default_model: string
  auth_type?: ProviderAuthType | null
  api_key_plain?: string | null
  api_key_env_name?: string | null
  network_mode?: ProviderNetworkMode | null
  custom_proxy_url?: string | null
  timeout_seconds?: number
  max_concurrency?: number
  max_questions_per_call?: number
  max_options_per_question?: number
  waf_strip_code_retry?: boolean
  cost_per_1m_input_tokens?: number
  extra_options_json?: Record<string, unknown> | null
  is_default?: boolean
  status?: ProviderStatus
}

export type ProviderUpdatePayload = Partial<ProviderUpsertPayload>

export interface ProviderProbeResult {
  ok: boolean
  provider_code: string
  provider_kind: ProviderKind
  endpoint_url: string
  model: string
  effective_network_mode: ProviderNetworkMode
  channel_used: string | null
  used_proxy: string | null
  attempts: number
  latency_ms: number
  status_code: number | null
  upstream_request_id: string | null
  probability_sample: {
    question_key: string
    p_true: number
    confidence: number
  } | null
  usage: Record<string, unknown> | null
  last_health_ok: boolean
  last_checked_at: string | null
  error: string | null
}

export interface ProviderModelsResult {
  provider_code: string
  models_list_url: string | null
  source: string
  channel_used: string | null
  latency_ms: number
  models: string[]
}

/* ---------------- SUT Adapters ---------------- */
export type EvalLevel = 'E2E' | 'COMPONENT'
export type AdapterType = 'HTTP_JSON' | 'HTTP_SSE' | 'COMPONENT_HARNESS'
export type AdapterStatus = 'ACTIVE' | 'DISABLED'

export interface SutAdapterRecord {
  id: number
  uuid: string
  adapter_code: string
  adapter_name: string
  eval_level: EvalLevel
  adapter_type: AdapterType
  endpoint_url: string
  record_endpoint_url: string | null
  auth_config_json: Record<string, unknown> | null
  request_mapping_json: Record<string, unknown>
  response_mapping_json: Record<string, unknown>
  timeout_seconds: number
  max_concurrency: number
  status: AdapterStatus
  description: string | null
  created_by: string | null
  create_time: string | null
  update_time: string | null
}

export interface AdapterUpsertPayload {
  adapter_code: string
  adapter_name: string
  eval_level: EvalLevel
  adapter_type: AdapterType
  endpoint_url: string
  record_endpoint_url?: string | null
  auth_config_json?: Record<string, unknown> | null
  request_mapping_json: Record<string, unknown>
  response_mapping_json: Record<string, unknown>
  timeout_seconds?: number
  max_concurrency?: number
  status?: AdapterStatus
  description?: string | null
}

export interface AdapterTestPayload {
  input_text: string
  conversation_turns_json?: Record<string, unknown>[] | null
  sut_input_json?: Record<string, unknown> | null
  sut_context_json?: Record<string, unknown> | null
  run_overrides?: Record<string, unknown> | null
}

export interface AdapterTestResult {
  adapter_code: string
  adapter_type: AdapterType
  eval_level: EvalLevel
  request_preview: Record<string, unknown>
  actual_output: string | null
  actual_references: string[]
  actual_structured: Record<string, unknown> | null
  sut_trace: unknown
  sut_latency_ms: number | null
  ttft_ms: number | null
}

/* ---------------- Dimensions, Linter & Suites ---------------- */
export type DimensionCategory =
  | 'GROUNDING'
  | 'RELEVANCY'
  | 'ACCURACY'
  | 'ROUTING'
  | 'SYNTHESIS'
  | 'SAFETY'
  | 'PERFORMANCE'

export type EvaluatorKind = 'SYSTEM_ONE' | 'DETERMINISTIC'
export type DimensionStatus = 'DRAFT' | 'ACTIVE' | 'DEPRECATED'
export type QuestionType = 'noul' | 'score' | 'choice'

export interface AtomicQuestionSpec {
  key: string
  type: QuestionType
  weight: number
  instructions: string
  criteria?: string[] | Record<string, string>
  option_scores?: Record<string, number | null>
}

export interface LintIssue {
  severity: 'ERROR' | 'WARN'
  code: string
  question_key?: string | null
  message: string
}

export interface LintReport {
  passed: boolean
  issues: LintIssue[]
}

export interface DimensionRecord {
  id: number
  uuid: string
  dimension_code: string
  version: number
  dimension_name: string
  category: DimensionCategory
  target_turn_mode: 'SINGLE_TURN' | 'MULTI_TURN' | 'BOTH'
  evaluator_kind: EvaluatorKind
  state_fields_json: string[] | null
  questions_json: AtomicQuestionSpec[] | null
  deterministic_rule_json: Record<string, unknown> | null
  default_threshold: number
  strict_mode: boolean
  default_weight: number
  lint_passed: boolean
  lint_report_json: LintReport | null
  status: DimensionStatus
  description: string | null
  created_by: string | null
  create_time: string | null
  update_time: string | null
}

export interface DimensionDetailResult {
  dimension_code: string
  latest_version: number
  latest: DimensionRecord
  versions: DimensionRecord[]
}

export interface DimensionCreatePayload {
  dimension_code: string
  dimension_name: string
  category: DimensionCategory
  target_turn_mode: 'SINGLE_TURN' | 'MULTI_TURN' | 'BOTH'
  evaluator_kind: EvaluatorKind
  state_fields_json?: string[] | null
  questions_json?: AtomicQuestionSpec[] | null
  deterministic_rule_json?: Record<string, unknown> | null
  default_threshold: number
  strict_mode: boolean
  default_weight: number
  status: DimensionStatus
  description?: string | null
}

export interface SuiteDimensionBinding {
  dimension_code: string
  version: number
  weight: number
  threshold: number
  is_blocker: boolean
}

export type SuitePassPolicy = 'ALL_DIMENSIONS' | 'ALL_BLOCKERS_AND_WEIGHTED'

export interface SuiteRecord {
  id: number
  uuid: string
  suite_code: string
  suite_name: string
  eval_level: EvalLevel
  default_adapter_id: number | null
  default_provider_id: number | null
  default_model_override: string | null
  dimensions_json: SuiteDimensionBinding[]
  pass_policy: SuitePassPolicy
  suite_pass_threshold: number
  confidence_review_floor: number
  enable_semantic_regression: boolean
  status: 'ACTIVE' | 'ARCHIVED'
  description: string | null
  created_by: string | null
  create_time: string | null
  update_time: string | null
}

export interface SuiteUpsertPayload {
  suite_code: string
  suite_name: string
  eval_level: EvalLevel
  default_adapter_id?: number | null
  default_provider_id?: number | null
  default_model_override?: string | null
  dimensions_json: SuiteDimensionBinding[]
  pass_policy: SuitePassPolicy
  suite_pass_threshold: number
  confidence_review_floor: number
  enable_semantic_regression: boolean
  status: 'ACTIVE' | 'ARCHIVED'
  description?: string | null
}

/* ---------------- Datasets & Cases ---------------- */
export type DatasetSplit = 'DEV' | 'HOLDOUT'
export type ReviewStatus = 'DRAFT' | 'PENDING_REVIEW' | 'APPROVED' | 'DISABLED'

export interface DatasetRecord {
  id: number
  uuid: string
  dataset_code: string
  version_tag: string
  dataset_name: string
  eval_level: EvalLevel
  desensitize_policy_snapshot: string | null
  dev_case_count: number
  holdout_case_count: number
  total_case_count: number
  pending_review_count: number
  status: 'DRAFT' | 'PUBLISHED' | 'ARCHIVED'
  description: string | null
  created_by: string | null
  create_time: string | null
  update_time: string | null
}

export interface DatasetCreatePayload {
  dataset_code: string
  version_tag: string
  dataset_name: string
  eval_level: EvalLevel
  desensitize_policy_snapshot?: string | null
  status?: 'DRAFT' | 'PUBLISHED' | 'ARCHIVED'
  description?: string | null
}

export interface EvalCaseRecord {
  id: number
  uuid: string
  dataset_id: number
  case_code: string
  dataset_split: DatasetSplit
  source_kind: string
  source_ref_json: Record<string, unknown> | null
  turn_mode: 'SINGLE_TURN' | 'MULTI_TURN'
  input_text: string
  conversation_turns_json: Record<string, unknown>[] | null
  expected_output: string | null
  expected_outcome: string | null
  reference_contexts_json: string[] | null
  expected_structured_json: Record<string, unknown> | null
  sut_input_json: Record<string, unknown> | null
  sut_context_json: Record<string, unknown> | null
  has_snapshot: boolean
  tags_json: string[] | null
  difficulty: 'EASY' | 'NORMAL' | 'HARD' | 'ADVERSARIAL'
  pii_masked: number
  desensitize_summary_json: Record<string, unknown> | null
  uncertainty_score: number | null
  review_status: ReviewStatus
  reviewed_by: string | null
  reviewed_at: string | null
  review_note: string | null
  create_time: string | null
  update_time: string | null
}

export interface CaseCuratePayload {
  input_text?: string
  expected_output?: string | null
  expected_outcome?: string | null
  reference_contexts_json?: string[] | null
  expected_structured_json?: Record<string, unknown> | null
  sut_input_json?: Record<string, unknown> | null
  sut_context_json?: Record<string, unknown> | null
  dataset_split?: DatasetSplit
  difficulty?: 'EASY' | 'NORMAL' | 'HARD' | 'ADVERSARIAL'
  pii_masked?: number | boolean
  uncertainty_score?: number | null
  review_status?: ReviewStatus
  review_note?: string | null
}

export interface DatasetImportPayload {
  jsonl_text?: string | null
  cases?: Record<string, unknown>[] | null
  auto_desensitize: boolean
  desensitize_policy_code: string
  default_pii_masked?: number | boolean | null
  dev_ratio: number
}

export interface DatasetImportResult {
  dataset_id: number
  imported_count: number
  skipped_count: number
  dev_imported_count: number
  holdout_imported_count: number
  dev_case_count: number
  holdout_case_count: number
  desensitize_policy_snapshot: string | null
}

export interface MysqlIngestPayload {
  source_dsn_env: string
  preset_extractor: string
  filter: {
    start_time?: string | null
    end_time?: string | null
    mining_signals?: string[] | null
    limit: number
  }
  desensitize_policy_code: string
  dev_ratio: number
}

export interface MysqlIngestResult {
  dataset_id: number
  ingested_count: number
  skipped_count: number
  dev_ingested_count: number
  holdout_ingested_count: number
  desensitize_policy_snapshot: string | null
}

export interface RecordSnapshotsPayload {
  adapter_code?: string
  adapter_id?: number
  case_ids?: number[] | null
  split?: DatasetSplit | null
  only_missing: boolean
}

export interface RecordSnapshotsResult {
  dataset_id: number
  adapter_code: string
  total_targeted: number
  recorded_count: number
  cases: EvalCaseRecord[]
}

/* ---------------- Runs, Reports & Calibration ---------------- */
export type SemanticRegressionTag =
  | 'SAME'
  | 'SPECIFICITY_DIFFERS'
  | 'ACTION_DIFFERS'
  | 'FACT_DIFFERS'

export type RunStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'

export interface DimensionSummaryItem {
  dimension_id: number
  dimension_code: string
  dimension_name: string
  version: number
  evaluator_kind: EvaluatorKind
  avg_score: number | null
  pass_rate: number
  passed_cases: number
  evaluated_cases: number
  threshold: number
  weight: number
  is_blocker: boolean
  histogram_counts: number[]
  unbatched_question_count: number
  baseline_avg_score: number | null
  delta_avg_score: number | null
  baseline_pass_rate: number | null
  delta_pass_rate: number | null
}

export interface RegressionSummary {
  SAME: number
  SPECIFICITY_DIFFERS: number
  ACTION_DIFFERS: number
  FACT_DIFFERS: number
  total_compared: number
}

export interface EvalRunRecord {
  id: number
  uuid: string
  run_no: string
  run_name: string
  suite_id: number
  suite_code: string | null
  suite_name: string | null
  eval_level: EvalLevel | null
  dataset_id: number
  dataset_code: string | null
  dataset_name: string | null
  dataset_version_tag: string | null
  dataset_split_filter: 'DEV' | 'HOLDOUT' | 'ALL'
  sut_adapter_id: number
  adapter_code: string | null
  adapter_name: string | null
  adapter_type: AdapterType | null
  sut_fingerprint_json: Record<string, unknown> | null
  provider_id: number
  provider_code: string | null
  provider_name: string | null
  pinned_provider_kind: ProviderKind
  pinned_endpoint_url: string
  pinned_model_name: string
  baseline_run_id: number | null
  baseline_run_no: string | null
  baseline_pinned_model_name: string | null
  cross_model_warning: boolean
  status: RunStatus
  total_cases: number
  completed_cases: number
  passed_cases: number
  failed_cases: number
  error_cases: number
  uncertain_cases: number
  pass_rate: number | null
  weighted_avg_score: number | null
  p50_sut_latency_ms: number | null
  p95_sut_latency_ms: number | null
  p50_ttft_ms: number | null
  total_decision_calls: number
  unbatched_question_count: number
  saved_decision_calls: number
  batch_saving_rate: number
  batch_compression_ratio: number
  total_input_tokens: number
  total_eval_cost: number
  dimension_summary_json: Record<string, DimensionSummaryItem> | null
  regression_summary_json: RegressionSummary | null
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  triggered_by: string | null
  create_time: string | null
  update_time: string | null
}

export interface EvalRunDetail extends EvalRunRecord {
  pass_policy: SuitePassPolicy
  suite_pass_threshold: number
  confidence_review_floor: number
  enable_semantic_regression: boolean
  suite_dimensions: SuiteDimensionBinding[]
}

export interface RunCreatePayload {
  run_name: string
  suite_code: string
  dataset_id: number
  dataset_split_filter: 'DEV' | 'HOLDOUT' | 'ALL'
  sut_adapter_code?: string | null
  provider_code?: string | null
  model_override?: string | null
  baseline_run_id?: number | null
  sut_fingerprint: Record<string, unknown>
  run_overrides?: Record<string, unknown> | null
  run_sync?: boolean
}

export interface AtomicAnswerLedgerItem {
  key: string
  type: QuestionType
  weight: number
  applicable: boolean
  normalized_score: number | null
  confidence: number | null
  p_true?: number
  raw_weighted_level?: number
  chosen?: string
  probabilities?: Record<string, number>
}

export interface ProbabilityLedgerItem {
  id: number
  dimension_id: number
  dimension_code: string
  dimension_version: number
  evaluator_kind: EvaluatorKind
  raw_dimension_score: number | null
  applied_threshold: number
  applied_weight: number
  is_blocker: boolean
  passed: boolean | null
  min_question_confidence: number | null
  deterministic_reason: string | null
  raw_answers_json: {
    questions?: AtomicAnswerLedgerItem[]
    [key: string]: unknown
  } | null
  waf_fallback_used: boolean
  upstream_request_id: string | null
  eval_latency_ms: number | null
  input_tokens: number | null
}

export interface EvalRunCaseItem {
  id: number
  run_id: number
  case_id: number
  case_code: string
  dataset_split: DatasetSplit
  turn_mode: string
  difficulty: string
  input_text: string
  conversation_turns_json: Record<string, unknown>[] | null
  expected_output: string | null
  expected_structured_json: Record<string, unknown> | null
  reference_contexts_json: string[] | null
  exec_status: 'SUCCESS' | 'SUT_ERROR' | 'JUDGE_ERROR' | 'SKIPPED' | string
  actual_output: string | null
  baseline_output: string | null
  actual_references_json: string[] | null
  actual_structured_json: Record<string, unknown> | null
  sut_trace_json: unknown
  sut_latency_ms: number | null
  ttft_ms: number | null
  overall_score: number | null
  overall_passed: boolean | null
  min_confidence: number | null
  needs_human_review: boolean
  regression_tag: SemanticRegressionTag | null
  regression_confidence: number | null
  error_detail: string | null
  ledgers: ProbabilityLedgerItem[]
}

export interface DimensionOverrideInput {
  threshold?: number
  weight?: number
  is_blocker?: boolean
  strict_mode?: boolean
}

export interface RecalculatePayload {
  pass_policy?: SuitePassPolicy
  suite_pass_threshold?: number
  confidence_review_floor?: number
  dimension_overrides: Record<string, DimensionOverrideInput>
}

export interface RecalculateResult {
  recalculated_pass_rate: number
  delta_pass_rate: number
  recalculated_weighted_avg_score: number
  delta_weighted_avg_score: number
  passed_cases: number
  failed_cases: number
  dimension_pass_rates: Record<string, number>
  flipped_case_ids: number[]
  compute_elapsed_ms: number
}

export interface RecommendThresholdsResult {
  run_no: string
  dataset_split_filter: string
  metric: 'youden_j' | 'f1'
  label_source: string
  labeled_cases: number
  suite_pass_threshold: number
  dimension_overrides: Record<
    string,
    {
      threshold: number
      weight: number
      is_blocker: boolean
      best_youden_j: number | null
      best_f1: number | null
    }
  >
  best_youden_j: number | null
  best_f1: number | null
  preview: RecalculateResult
}

export interface CompareDimensionDelta {
  dimension_code: string
  dimension_name: string
  evaluator_kind: EvaluatorKind | null
  threshold: number | null
  weight: number | null
  is_blocker: boolean
  current_avg_score: number | null
  baseline_avg_score: number | null
  delta_avg_score: number | null
  current_pass_rate: number | null
  baseline_pass_rate: number | null
  delta_pass_rate: number | null
}

export interface CompareCaseDiffItem {
  case_id: number
  case_code: string
  dataset_split: DatasetSplit | null
  input_text: string
  expected_output: string | null
  regression_tag: SemanticRegressionTag | null
  regression_confidence: number | null
  current_actual_output: string | null
  baseline_actual_output: string | null
  current_overall_score: number | null
  baseline_overall_score: number | null
  delta_overall_score: number | null
  current_passed: boolean | null
  baseline_passed: boolean | null
  gate_flipped: boolean
  current_exec_status: string
  baseline_exec_status: string
  dimension_score_diffs: Record<
    string,
    {
      current_score: number | null
      baseline_score: number | null
      delta_score: number | null
      current_passed: boolean | null
      baseline_passed: boolean | null
    }
  >
}

export interface CompareRunsResult {
  cross_model_warning: boolean
  cross_model_warning_message: string | null
  current_run: EvalRunDetail
  baseline_run: EvalRunDetail
  kpi_deltas: {
    current_pass_rate: number | null
    baseline_pass_rate: number | null
    delta_pass_rate: number | null
    current_weighted_avg_score: number | null
    baseline_weighted_avg_score: number | null
    delta_weighted_avg_score: number | null
    current_p50_sut_latency_ms: number | null
    baseline_p50_sut_latency_ms: number | null
    delta_p50_sut_latency_ms: number | null
    current_p95_sut_latency_ms: number | null
    baseline_p95_sut_latency_ms: number | null
    delta_p95_sut_latency_ms: number | null
    current_p50_ttft_ms: number | null
    baseline_p50_ttft_ms: number | null
    delta_p50_ttft_ms: number | null
  }
  dimension_deltas: CompareDimensionDelta[]
  regression_summary: RegressionSummary
  case_diffs: CompareCaseDiffItem[]
}
