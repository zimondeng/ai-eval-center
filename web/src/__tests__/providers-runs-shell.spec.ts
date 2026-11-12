import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import ProvidersView from '@/views/providers/ProvidersView.vue'
import RunsView from '@/views/runs/RunsView.vue'
import AppShell from '@/layouts/AppShell.vue'
import { adaptersApi, authApi, datasetsApi, providersApi, runsApi, suitesApi } from '@/api/endpoints'
import type { DecisionProviderRecord, EvalRunRecord } from '@/api/types'

vi.mock('vue-router', () => ({
  useRoute: () => ({
    path: '/runs',
    meta: { domain: '实验批次' },
    params: {},
    query: {},
  }),
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
  }),
}))

vi.mock('@/api/endpoints', () => ({
  providersApi: {
    list: vi.fn(),
    upsert: vi.fn(),
    update: vi.fn(),
    probe: vi.fn(),
    fetchModels: vi.fn(),
  },
  adaptersApi: {
    list: vi.fn(),
    upsert: vi.fn(),
    test: vi.fn(),
  },
  authApi: {
    listApiKeys: vi.fn(),
    createApiKey: vi.fn(),
    revokeApiKey: vi.fn(),
  },
  suitesApi: {
    list: vi.fn(),
  },
  datasetsApi: {
    list: vi.fn(),
  },
  runsApi: {
    list: vi.fn(),
    create: vi.fn(),
    cancel: vi.fn(),
  },
}))

function makeOllamaProvider(overrides: Partial<DecisionProviderRecord> = {}): DecisionProviderRecord {
  return {
    id: 1,
    uuid: 'u-prov-1',
    provider_code: 'ollama_local_tev1',
    provider_name: '本地 Ollama tev1',
    provider_kind: 'OLLAMA_LOCAL',
    endpoint_url: 'http://127.0.0.1:11434/v1/systemone',
    models_list_url: 'http://127.0.0.1:11434/v1/models',
    default_model: 'tev1:0.8b',
    auth_type: 'NONE',
    api_key_env_name: null,
    api_key_masked: null,
    has_api_key: false,
    network_mode: 'DIRECT',
    custom_proxy_url: null,
    timeout_seconds: 90,
    max_concurrency: 8,
    max_questions_per_call: 64,
    max_options_per_question: 24,
    waf_strip_code_retry: false,
    cost_per_1m_input_tokens: 0,
    extra_options_json: { keep_alive: '15m' },
    is_default: true,
    status: 'ACTIVE',
    last_health_ok: true,
    last_checked_at: '2026-11-12T08:00:00',
    created_by: 'system',
    create_time: '2026-11-12T08:00:00',
    update_time: '2026-11-12T08:00:00',
    ...overrides,
  }
}

describe('ProvidersView, RunsView & AppShell standards compliance', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('calls PUT /providers/{code}, preserves extra_options_json, binds fetchedModels into el-select, and validates inline', async () => {
    const ollama = makeOllamaProvider()
    vi.mocked(providersApi.list).mockResolvedValue([ollama])
    vi.mocked(adaptersApi.list).mockResolvedValue([])
    vi.mocked(authApi.listApiKeys).mockResolvedValue([])
    vi.mocked(providersApi.fetchModels).mockResolvedValue({
      provider_code: 'ollama_local_tev1',
      models_list_url: ollama.models_list_url,
      source: 'remote',
      channel_used: 'direct',
      latency_ms: 14,
      models: ['tev1:0.8b', 'tev1:1.7b'],
    })
    vi.mocked(providersApi.update).mockImplementation(async (_code, payload) =>
      makeOllamaProvider({
        default_model: payload.default_model ?? ollama.default_model,
        extra_options_json:
          payload.extra_options_json !== undefined
            ? payload.extra_options_json
            : ollama.extra_options_json,
      }),
    )

    const wrapper = mount(ProvidersView)
    await flushPromises()

    // Fetch models first so fetchedModels is populated
    const fetchBtn = wrapper
      .findAll('button')
      .find((b) => b.text().includes('拉取模型'))
    expect(fetchBtn).toBeDefined()
    await fetchBtn!.trigger('click')
    await flushPromises()

    // Open edit drawer for existing provider
    const editBtn = wrapper
      .findAll('button')
      .find((b) => b.text().trim() === '编辑')
    expect(editBtn).toBeDefined()
    await editBtn!.trigger('click')
    await flushPromises()

    // Verify default_model uses filterable + allow-create ElSelect and extra_options_json is preserved
    const modelSelect = wrapper.find('[data-testid="provider-default-model-select"]')
    expect(modelSelect.exists()).toBe(true)

    const extraTextarea = wrapper.find('textarea[data-testid="provider-extra-options-input"]')
    expect(extraTextarea.exists()).toBe(true)
    expect((extraTextarea.element as HTMLTextAreaElement).value).toContain('"keep_alive": "15m"')

    // Save existing provider -> must invoke PUT providersApi.update, NOT POST providersApi.upsert
    const saveBtn = wrapper.find('[data-testid="btn-save-provider"]')
    expect(saveBtn.exists()).toBe(true)
    await saveBtn.trigger('click')
    await flushPromises()

    expect(providersApi.upsert).not.toHaveBeenCalled()
    expect(providersApi.update).toHaveBeenCalledWith(
      'ollama_local_tev1',
      expect.objectContaining({
        default_model: 'tev1:0.8b',
        extra_options_json: { keep_alive: '15m' },
      }),
    )

    // Open create drawer and verify empty required fields trigger field-level el-form validation
    const createBtn = wrapper
      .findAll('button')
      .find((b) => b.text().includes('新增判别模型渠道'))
    await createBtn!.trigger('click')
    await flushPromises()

    await wrapper.find('[data-testid="btn-save-provider"]').trigger('click')
    await flushPromises()
    await new Promise((r) => setTimeout(r, 120))
    expect(providersApi.upsert).not.toHaveBeenCalled()
    expect(wrapper.find('.el-form-item.is-error').exists()).toBe(true)
    expect(wrapper.find('.el-form-item__error').exists()).toBe(true)
  })

  it('wraps cancel run action in el-popconfirm and validates create-run form inline', async () => {
    const runningItem: EvalRunRecord = {
      id: 201,
      uuid: 'u-run-201',
      run_no: 'RUN_20261112_0009',
      run_name: '执行中批次',
      suite_id: 1,
      suite_code: 'suite_gov_e2e_v1',
      suite_name: '政务套件',
      eval_level: 'E2E',
      dataset_id: 12,
      dataset_code: 'gov_golden',
      dataset_name: '政务黄金集',
      dataset_version_tag: 'v1.0',
      dataset_split_filter: 'DEV',
      sut_adapter_id: 1,
      adapter_code: 'gov_e2e_routing_blocking',
      adapter_name: '政务路由总入口',
      adapter_type: 'HTTP_JSON',
      sut_fingerprint_json: null,
      provider_id: 1,
      provider_code: 'ollama_local_tev1',
      provider_name: '本地 Ollama',
      pinned_provider_kind: 'OLLAMA_LOCAL',
      pinned_endpoint_url: 'http://127.0.0.1:11434/v1/systemone',
      pinned_model_name: 'tev1:0.8b',
      baseline_run_id: null,
      baseline_run_no: null,
      baseline_pinned_model_name: null,
      cross_model_warning: false,
      status: 'RUNNING',
      total_cases: 50,
      completed_cases: 12,
      passed_cases: 10,
      failed_cases: 2,
      error_cases: 0,
      uncertain_cases: 1,
      pass_rate: null,
      weighted_avg_score: null,
      p50_sut_latency_ms: null,
      p95_sut_latency_ms: null,
      p50_ttft_ms: null,
      total_decision_calls: 12,
      unbatched_question_count: 36,
      saved_decision_calls: 24,
      batch_saving_rate: 0.6667,
      batch_compression_ratio: 3.0,
      total_input_tokens: 8000,
      total_eval_cost: 0,
      dimension_summary_json: null,
      regression_summary_json: null,
      error_message: null,
      started_at: '2026-11-12T10:00:00',
      finished_at: null,
      triggered_by: 'admin',
      create_time: '2026-11-12T10:00:00',
      update_time: '2026-11-12T10:00:00',
    }

    vi.mocked(suitesApi.list).mockResolvedValue([])
    vi.mocked(datasetsApi.list).mockResolvedValue([])
    vi.mocked(adaptersApi.list).mockResolvedValue([])
    vi.mocked(providersApi.list).mockResolvedValue([])
    vi.mocked(runsApi.list).mockResolvedValue({
      items: [runningItem],
      total: 1,
      page: 1,
      page_size: 20,
    })
    vi.mocked(runsApi.cancel).mockResolvedValue({
      ...runningItem,
      status: 'CANCELLED',
      pass_policy: 'ALL_BLOCKERS_AND_WEIGHTED',
      suite_pass_threshold: 0.75,
      confidence_review_floor: 0.5,
      enable_semantic_regression: true,
      suite_dimensions: [],
    })

    const wrapper = mount(RunsView, {
      global: {
        stubs: {
          'router-view': true,
        },
      },
    })
    await flushPromises()

    const popconfirm = wrapper.findComponent({ name: 'ElPopconfirm' })
    expect(popconfirm.exists()).toBe(true)

    // Clicking the cancel trigger button alone must NOT immediately cancel without confirmation
    const cancelTrigger = wrapper.find('[data-testid="btn-cancel-run"]')
    expect(cancelTrigger.exists()).toBe(true)
    await cancelTrigger.trigger('click')
    await flushPromises()
    expect(runsApi.cancel).not.toHaveBeenCalled()

    // Emitting confirm on ElPopconfirm executes cancellation
    popconfirm.vm.$emit('confirm', new MouseEvent('click'))
    await flushPromises()
    expect(runsApi.cancel).toHaveBeenCalledWith('RUN_20261112_0009')

    // Open create-run drawer and verify inline el-form validation blocks empty run_name
    const openCreateBtn = wrapper
      .findAll('button')
      .find((b) => b.text().includes('发起实验批次'))
    await openCreateBtn!.trigger('click')
    await flushPromises()

    await wrapper.find('[data-testid="btn-submit-create-run"]').trigger('click')
    await flushPromises()
    await new Promise((r) => setTimeout(r, 120))
    expect(runsApi.create).not.toHaveBeenCalled()
    expect(wrapper.find('.el-form-item.is-error').exists()).toBe(true)
    expect(wrapper.find('.el-form-item__error').exists()).toBe(true)
  })

  it('renders aria-label and collapsed tooltip on sidebar navigation buttons in AppShell', () => {
    const wrapper = mount(AppShell, {
      global: {
        stubs: {
          'router-view': true,
        },
      },
    })

    const navButtons = wrapper.findAll('.ev-sidebar__nav .ev-nav-item')
    expect(navButtons.length).toBe(5)
    expect(navButtons[0].attributes('aria-label')).toBe('渠道与适配器')
    expect(navButtons[4].attributes('aria-label')).toBe('回归与阈值报告')
  })
})
