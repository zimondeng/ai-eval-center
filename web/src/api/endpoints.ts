import { apiRequest } from './http'
import type {
  AdapterTestPayload,
  AdapterTestResult,
  AdapterUpsertPayload,
  AdminUserProfile,
  ApiKeyCreatePayload,
  ApiKeyRecord,
  CaseCuratePayload,
  CompareRunsResult,
  DatasetCreatePayload,
  DatasetImportPayload,
  DatasetImportResult,
  DatasetRecord,
  DecisionProviderRecord,
  DimensionCreatePayload,
  DimensionDetailResult,
  DimensionRecord,
  EvalCaseRecord,
  EvalRunCaseItem,
  EvalRunDetail,
  EvalRunRecord,
  LintReport,
  LoginResponseData,
  MysqlIngestPayload,
  MysqlIngestResult,
  PaginatedResult,
  ProviderModelsResult,
  ProviderProbeResult,
  ProviderUpsertPayload,
  RecalculatePayload,
  RecalculateResult,
  RecommendThresholdsResult,
  RecordSnapshotsPayload,
  RecordSnapshotsResult,
  RunCreatePayload,
  SuiteRecord,
  SuiteUpsertPayload,
  SutAdapterRecord,
} from './types'

export const authApi = {
  login(username: string, password: string) {
    return apiRequest<LoginResponseData>('/auth/login', {
      method: 'POST',
      body: { username, password },
      skipAuth: true,
    })
  },
  me() {
    return apiRequest<AdminUserProfile>('/auth/me')
  },
  listApiKeys() {
    return apiRequest<ApiKeyRecord[]>('/api-keys')
  },
  createApiKey(payload: ApiKeyCreatePayload) {
    return apiRequest<ApiKeyRecord>('/api-keys', {
      method: 'POST',
      body: payload,
    })
  },
  revokeApiKey(keyId: number) {
    return apiRequest<ApiKeyRecord>(`/api-keys/${keyId}/revoke`, {
      method: 'POST',
    })
  },
}

export const providersApi = {
  list(status?: string) {
    return apiRequest<DecisionProviderRecord[]>('/providers', {
      query: { status },
    })
  },
  upsert(payload: ProviderUpsertPayload) {
    return apiRequest<DecisionProviderRecord>('/providers', {
      method: 'POST',
      body: payload,
    })
  },
  probe(providerCode: string, modelOverride?: string | null) {
    return apiRequest<ProviderProbeResult>(`/providers/${encodeURIComponent(providerCode)}/test`, {
      method: 'POST',
      body: modelOverride ? { model_override: modelOverride } : {},
    })
  },
  fetchModels(providerCode: string) {
    return apiRequest<ProviderModelsResult>(`/providers/${encodeURIComponent(providerCode)}/models`)
  },
}

export const adaptersApi = {
  list(params?: { eval_level?: string; adapter_type?: string; status?: string }) {
    return apiRequest<SutAdapterRecord[]>('/adapters', { query: params })
  },
  upsert(payload: AdapterUpsertPayload) {
    return apiRequest<SutAdapterRecord>('/adapters', {
      method: 'POST',
      body: payload,
    })
  },
  test(adapterCode: string, payload: AdapterTestPayload) {
    return apiRequest<AdapterTestResult>(`/adapters/${encodeURIComponent(adapterCode)}/test`, {
      method: 'POST',
      body: payload,
    })
  },
}

export const dimensionsApi = {
  list(params?: {
    category?: string
    status?: string
    evaluator_kind?: string
    latest_only?: boolean
  }) {
    return apiRequest<DimensionRecord[]>('/dimensions', { query: params })
  },
  detail(dimensionCode: string) {
    return apiRequest<DimensionDetailResult>(`/dimensions/${encodeURIComponent(dimensionCode)}`)
  },
  lint(payload: { state_fields_json: string[]; questions_json: unknown[]; max_options?: number }) {
    return apiRequest<LintReport>('/dimensions/lint', {
      method: 'POST',
      body: payload,
    })
  },
  createVersion(payload: DimensionCreatePayload) {
    return apiRequest<DimensionRecord>('/dimensions', {
      method: 'POST',
      body: payload,
    })
  },
}

export const suitesApi = {
  list(params?: { eval_level?: string; status?: string }) {
    return apiRequest<SuiteRecord[]>('/suites', { query: params })
  },
  create(payload: SuiteUpsertPayload) {
    return apiRequest<SuiteRecord>('/suites', {
      method: 'POST',
      body: payload,
    })
  },
  update(suiteCode: string, payload: SuiteUpsertPayload) {
    return apiRequest<SuiteRecord>(`/suites/${encodeURIComponent(suiteCode)}`, {
      method: 'PUT',
      body: payload,
    })
  },
}

export const datasetsApi = {
  list(params?: { eval_level?: string; status?: string; q?: string }) {
    return apiRequest<DatasetRecord[]>('/datasets', { query: params })
  },
  detail(datasetId: number) {
    return apiRequest<DatasetRecord>(`/datasets/${datasetId}`)
  },
  create(payload: DatasetCreatePayload) {
    return apiRequest<DatasetRecord>('/datasets', {
      method: 'POST',
      body: payload,
    })
  },
  importCases(datasetId: number, payload: DatasetImportPayload) {
    return apiRequest<DatasetImportResult>(`/datasets/${datasetId}/import`, {
      method: 'POST',
      body: payload,
    })
  },
  ingestFromMysql(datasetId: number, payload: MysqlIngestPayload) {
    return apiRequest<MysqlIngestResult>(`/datasets/${datasetId}/ingest-from-mysql`, {
      method: 'POST',
      body: payload,
    })
  },
  recordComponentSnapshots(datasetId: number, payload: RecordSnapshotsPayload) {
    return apiRequest<RecordSnapshotsResult>(
      `/datasets/${datasetId}/record-component-snapshots`,
      {
        method: 'POST',
        body: payload,
      },
    )
  },
  listCases(
    datasetId: number,
    params?: {
      split?: string
      review_status?: string
      pii_masked?: number | null
      has_snapshot?: boolean | null
      q?: string
      sort?: string
      page?: number
      page_size?: number
    },
  ) {
    return apiRequest<PaginatedResult<EvalCaseRecord>>(`/datasets/${datasetId}/cases`, {
      query: params,
    })
  },
  curateCase(caseId: number, payload: CaseCuratePayload) {
    return apiRequest<EvalCaseRecord>(`/cases/${caseId}/curate`, {
      method: 'PUT',
      body: payload,
    })
  },
  deleteCase(caseId: number) {
    return apiRequest<{ deleted_case_id: number }>(`/cases/${caseId}`, {
      method: 'DELETE',
    })
  },
}

export const runsApi = {
  list(params?: {
    suite_code?: string
    dataset_id?: number | null
    dataset_split_filter?: string
    status?: string
    page?: number
    page_size?: number
  }) {
    return apiRequest<PaginatedResult<EvalRunRecord>>('/runs', { query: params })
  },
  create(payload: RunCreatePayload) {
    return apiRequest<EvalRunDetail>('/runs', {
      method: 'POST',
      body: payload,
    })
  },
  detail(runNo: string) {
    return apiRequest<EvalRunDetail>(`/runs/${encodeURIComponent(runNo)}`)
  },
  cancel(runNo: string) {
    return apiRequest<EvalRunDetail>(`/runs/${encodeURIComponent(runNo)}/cancel`, {
      method: 'POST',
    })
  },
  listCases(
    runNo: string,
    params?: {
      passed?: number | boolean | null
      needs_human_review?: number | boolean | null
      regression_tag?: string | null
      exec_status?: string | null
      case_ids?: string | null
      q?: string | null
      page?: number
      page_size?: number
    },
  ) {
    return apiRequest<PaginatedResult<EvalRunCaseItem>>(
      `/runs/${encodeURIComponent(runNo)}/cases`,
      { query: params },
    )
  },
  compare(
    runNo: string,
    baselineRunNo: string,
    params?: { regression_tag?: string; only_changed?: boolean },
  ) {
    return apiRequest<CompareRunsResult>(
      `/runs/${encodeURIComponent(runNo)}/compare/${encodeURIComponent(baselineRunNo)}`,
      { query: params },
    )
  },
  recalculate(runNo: string, payload: RecalculatePayload) {
    return apiRequest<RecalculateResult>(`/runs/${encodeURIComponent(runNo)}/recalculate`, {
      method: 'POST',
      body: payload,
    })
  },
  recommendThresholds(runNo: string, metric: 'youden_j' | 'f1' = 'youden_j') {
    return apiRequest<RecommendThresholdsResult>(
      `/runs/${encodeURIComponent(runNo)}/recommend-thresholds`,
      {
        method: 'POST',
        body: { metric },
      },
    )
  },
}
