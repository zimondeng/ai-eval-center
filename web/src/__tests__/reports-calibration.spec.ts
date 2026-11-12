import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import ReportsView from '@/views/reports/ReportsView.vue'
import { runsApi, suitesApi } from '@/api/endpoints'
import type { CompareRunsResult, EvalRunCaseItem, EvalRunDetail, SuiteRecord } from '@/api/types'

const routerPushMock = vi.fn()
const routerReplaceMock = vi.fn()

vi.mock('vue-router', () => ({
  useRoute: () => ({
    params: { runNo: 'RUN_20261112_0001' },
    query: {},
  }),
  useRouter: () => ({
    push: routerPushMock,
    replace: routerReplaceMock,
  }),
}))

vi.mock('echarts', () => ({
  init: vi.fn(() => ({
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
    on: vi.fn(),
    off: vi.fn(),
  })),
}))

vi.mock('@/api/endpoints', () => ({
  runsApi: {
    list: vi.fn(),
    create: vi.fn(),
    detail: vi.fn(),
    listCases: vi.fn(),
    compare: vi.fn(),
    recalculate: vi.fn(),
    recommendThresholds: vi.fn(),
  },
  suitesApi: {
    detail: vi.fn(),
    update: vi.fn(),
  },
}))

const HOLDOUT_GUARD_COPY =
  '当前批次基于 HOLDOUT（留出验真集）执行。为防止测试集过拟合，自动阈值寻优仅允许在 DEV（调优集）上运行。'

function makeRunDetail(overrides: Partial<EvalRunDetail> = {}): EvalRunDetail {
  return {
    id: 101,
    uuid: 'u-run-101',
    run_no: 'RUN_20261112_0001',
    run_name: '政务客服留出集门禁验收',
    suite_id: 1,
    suite_code: 'suite_gov_e2e_v1',
    suite_name: '政务端到端套件',
    eval_level: 'E2E',
    dataset_id: 12,
    dataset_code: 'gov_golden',
    dataset_name: '政务黄金集',
    dataset_version_tag: 'v1.0',
    dataset_split_filter: 'HOLDOUT',
    sut_adapter_id: 1,
    adapter_code: 'gov_e2e_routing_blocking',
    adapter_name: '政务路由总入口',
    adapter_type: 'HTTP_JSON',
    sut_fingerprint_json: { git_sha: '846e4b9b', prompt_version: 'supervisor_v2' },
    provider_id: 1,
    provider_code: 'ollama_local_tev1',
    provider_name: '本地 Ollama tev1',
    pinned_provider_kind: 'OLLAMA_LOCAL',
    pinned_endpoint_url: 'http://127.0.0.1:11434/v1/systemone',
    pinned_model_name: 'tev1:0.8b',
    baseline_run_id: 99,
    baseline_run_no: 'RUN_20261110_0008',
    baseline_pinned_model_name: 'typesafe/jev-1.14.0',
    cross_model_warning: true,
    status: 'COMPLETED',
    total_cases: 200,
    completed_cases: 200,
    passed_cases: 176,
    failed_cases: 24,
    error_cases: 0,
    uncertain_cases: 5,
    pass_rate: 0.88,
    weighted_avg_score: 0.854,
    p50_sut_latency_ms: 420,
    p95_sut_latency_ms: 980,
    p50_ttft_ms: 190,
    total_decision_calls: 200,
    unbatched_question_count: 600,
    saved_decision_calls: 400,
    batch_saving_rate: 0.6667,
    batch_compression_ratio: 3.0,
    total_input_tokens: 128000,
    total_eval_cost: 0.0256,
    dimension_summary_json: {
      rag_faithfulness_s1: {
        dimension_id: 3,
        dimension_code: 'rag_faithfulness_s1',
        dimension_name: '知识引用忠实度',
        version: 1,
        evaluator_kind: 'SYSTEM_ONE',
        avg_score: 0.86,
        pass_rate: 0.9,
        passed_cases: 180,
        evaluated_cases: 200,
        threshold: 0.75,
        weight: 2.0,
        is_blocker: true,
        histogram_counts: [0, 2, 3, 5, 10, 15, 25, 45, 55, 40],
        unbatched_question_count: 400,
        baseline_avg_score: 0.82,
        delta_avg_score: 0.04,
        baseline_pass_rate: 0.85,
        delta_pass_rate: 0.05,
      },
    },
    regression_summary_json: {
      SAME: 150,
      SPECIFICITY_DIFFERS: 32,
      ACTION_DIFFERS: 12,
      FACT_DIFFERS: 6,
      total_compared: 200,
    },
    error_message: null,
    started_at: '2026-11-12T09:00:00',
    finished_at: '2026-11-12T09:05:00',
    triggered_by: 'admin',
    create_time: '2026-11-12T09:00:00',
    update_time: '2026-11-12T09:05:00',
    pass_policy: 'ALL_BLOCKERS_AND_WEIGHTED',
    suite_pass_threshold: 0.75,
    confidence_review_floor: 0.5,
    enable_semantic_regression: true,
    suite_dimensions: [
      {
        dimension_code: 'rag_faithfulness_s1',
        version: 1,
        weight: 2.0,
        threshold: 0.75,
        is_blocker: true,
      },
    ],
    ...overrides,
  }
}

function makeRunCaseItem(overrides: Partial<EvalRunCaseItem> = {}): EvalRunCaseItem {
  return {
    id: 2001,
    run_id: 101,
    case_id: 501,
    case_code: 'case_fact_diff_501',
    dataset_split: 'HOLDOUT',
    turn_mode: 'SINGLE_TURN',
    difficulty: 'NORMAL',
    input_text: '常州公积金异地转移怎么办理？',
    conversation_turns_json: null,
    expected_output: '可通过全国住房公积金小程序线上申请转移接续。',
    expected_structured_json: null,
    reference_contexts_json: null,
    exec_status: 'SUCCESS',
    actual_output: '必须携带纸质材料前往柜台现场办理。',
    baseline_output: '可通过全国住房公积金小程序线上办理。',
    actual_references_json: null,
    actual_structured_json: null,
    sut_trace_json: null,
    sut_latency_ms: 410,
    ttft_ms: 180,
    overall_score: 0.42,
    overall_passed: false,
    min_confidence: 0.88,
    needs_human_review: false,
    regression_tag: 'FACT_DIFFERS',
    regression_confidence: 0.94,
    error_detail: null,
    ledgers: [],
    ...overrides,
  }
}

describe('ReportsView: cross_model_warning, HOLDOUT guardrail, el-slider, suite lock & pagination', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  it('renders loading skeleton initially, KPI cost card, dual-coded regression tags, and HOLDOUT guardrail', async () => {
    const holdoutRun = makeRunDetail({ dataset_split_filter: 'HOLDOUT' })
    const baseRun = makeRunDetail({
      id: 99,
      run_no: 'RUN_20261110_0008',
      pinned_model_name: 'typesafe/jev-1.14.0',
      baseline_run_id: null,
      baseline_run_no: null,
    })

    const compareData: CompareRunsResult = {
      cross_model_warning: true,
      cross_model_warning_message: null,
      current_run: holdoutRun,
      baseline_run: baseRun,
      kpi_deltas: {
        current_pass_rate: 0.88,
        baseline_pass_rate: 0.85,
        delta_pass_rate: 0.03,
        current_weighted_avg_score: 0.854,
        baseline_weighted_avg_score: 0.83,
        delta_weighted_avg_score: 0.024,
        current_p50_sut_latency_ms: 420,
        baseline_p50_sut_latency_ms: 410,
        delta_p50_sut_latency_ms: 10,
        current_p95_sut_latency_ms: 980,
        baseline_p95_sut_latency_ms: 960,
        delta_p95_sut_latency_ms: 20,
        current_p50_ttft_ms: 190,
        baseline_p50_ttft_ms: 185,
        delta_p50_ttft_ms: 5,
      },
      dimension_deltas: [
        {
          dimension_code: 'rag_faithfulness_s1',
          dimension_name: '知识引用忠实度',
          evaluator_kind: 'SYSTEM_ONE',
          threshold: 0.75,
          weight: 2.0,
          is_blocker: true,
          current_avg_score: 0.86,
          baseline_avg_score: 0.82,
          delta_avg_score: 0.04,
          current_pass_rate: 0.9,
          baseline_pass_rate: 0.85,
          delta_pass_rate: 0.05,
        },
      ],
      regression_summary: holdoutRun.regression_summary_json!,
      case_diffs: [],
    }

    let resolveDetail!: (val: EvalRunDetail) => void
    const detailPromise = new Promise<EvalRunDetail>((resolve) => {
      resolveDetail = resolve
    })

    vi.mocked(runsApi.list).mockResolvedValue({
      items: [holdoutRun],
      total: 1,
      page: 1,
      page_size: 50,
    })
    vi.mocked(runsApi.detail).mockReturnValueOnce(detailPromise)
    vi.mocked(runsApi.compare).mockResolvedValue(compareData)
    vi.mocked(runsApi.listCases).mockResolvedValue({
      items: [makeRunCaseItem()],
      total: 250,
      page: 1,
      page_size: 100,
    })

    const wrapper = mount(ReportsView)
    await flushPromises()

    // Skeleton must show while loadingReport is active
    expect(wrapper.find('[data-testid="report-skeleton"]').exists()).toBe(true)

    resolveDetail(holdoutRun)
    await flushPromises()

    // 1. Verify exact cross_model_warning copy from DESIGN.md §12
    const warningBanner = wrapper.find('[data-testid="cross-model-warning-banner"]')
    expect(warningBanner.exists()).toBe(true)
    expect(warningBanner.text()).toContain(
      '标尺变更提示：当前批次判别模型（tev1:0.8b）与基线批次（typesafe/jev-1.14.0）不一致。不同判别模型的原始概率标尺存在差异，涨跌分值仅供参考，请重点审阅「语义回归差异」明细。',
    )

    // 2. KPI cost card displays total_eval_cost + total_input_tokens
    const costCard = wrapper.find('[data-testid="kpi-eval-cost"]')
    expect(costCard.exists()).toBe(true)
    expect(costCard.text()).toContain('估算成本')
    expect(costCard.text()).toContain('0.0256')
    expect(costCard.text()).toContain('128,000')

    // 3. Dual-coded regression labels rendered in chart pills & case table
    expect(wrapper.text()).toContain('✓ 实质一致 (SAME)')
    expect(wrapper.text()).toContain('~ 详略差异 (SPECIFICITY_DIFFERS)')
    expect(wrapper.text()).toContain('! 指引变更 (ACTION_DIFFERS)')
    expect(wrapper.text()).toContain('✕ 事实退化 (FACT_DIFFERS)')

    // 4. Verify HOLDOUT anti-overfitting guardrail disables recommend & suite lock buttons
    const recBtn = wrapper.find('[data-testid="btn-recommend-thresholds"]')
    expect(recBtn.exists()).toBe(true)
    expect(recBtn.attributes('disabled')).toBeDefined()

    const lockBtn = wrapper.find('[data-testid="btn-lock-suite-thresholds"]')
    expect(lockBtn.exists()).toBe(true)
    expect(lockBtn.attributes('disabled')).toBeDefined()

    const guardReason = wrapper.find('[data-testid="holdout-guard-reason"]')
    expect(guardReason.attributes('data-guard-message')).toBe(HOLDOUT_GUARD_COPY)

    await recBtn.trigger('click')
    await flushPromises()
    expect(runsApi.recommendThresholds).not.toHaveBeenCalled()

    // 5. Cases table pagination renders and triggers page 2 fetch
    const pagination = wrapper.find('[data-testid="report-cases-pagination"]')
    expect(pagination.exists()).toBe(true)
    const elPagination = wrapper.findComponent({ name: 'ElPagination' })
    expect(elPagination.exists()).toBe(true)
    elPagination.vm.$emit('update:current-page', 2)
    elPagination.vm.$emit('current-change', 2)
    await flushPromises()

    expect(runsApi.listCases).toHaveBeenLastCalledWith(
      'RUN_20261112_0001',
      expect.objectContaining({
        page: 2,
        page_size: 100,
      }),
    )
  })

  it('uses el-slider + el-input-number on DEV split, locks calibrated thresholds to suite, and launches HOLDOUT verification run', async () => {
    vi.useFakeTimers()
    const devRun = makeRunDetail({
      dataset_split_filter: 'DEV',
      baseline_run_id: null,
      baseline_run_no: null,
      baseline_pinned_model_name: null,
      cross_model_warning: false,
    })

    const suiteRecord: SuiteRecord = {
      id: 1,
      uuid: 'u-suite-1',
      suite_code: 'suite_gov_e2e_v1',
      suite_name: '政务端到端套件',
      eval_level: 'E2E',
      default_adapter_id: 1,
      default_provider_id: 1,
      default_model_override: 'tev1:0.8b',
      dimensions_json: [
        {
          dimension_code: 'rag_faithfulness_s1',
          version: 1,
          weight: 2.0,
          threshold: 0.75,
          is_blocker: true,
        },
      ],
      pass_policy: 'ALL_BLOCKERS_AND_WEIGHTED',
      suite_pass_threshold: 0.75,
      confidence_review_floor: 0.5,
      enable_semantic_regression: true,
      status: 'ACTIVE',
      description: '端到端验收门禁',
      created_by: 'admin',
      create_time: '2026-11-12T08:00:00',
      update_time: '2026-11-12T08:00:00',
    }

    const holdoutCreated = makeRunDetail({
      id: 102,
      run_no: 'RUN_20261112_0002',
      run_name: '政务客服留出集门禁验收 · HOLDOUT 验真',
      dataset_split_filter: 'HOLDOUT',
      cross_model_warning: false,
      baseline_run_id: null,
      baseline_run_no: null,
    })

    vi.mocked(runsApi.list).mockResolvedValue({
      items: [devRun],
      total: 1,
      page: 1,
      page_size: 50,
    })
    vi.mocked(runsApi.detail).mockResolvedValue(devRun)
    vi.mocked(runsApi.listCases).mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 100,
    })
    vi.mocked(runsApi.recalculate).mockResolvedValue({
      recalculated_pass_rate: 0.835,
      delta_pass_rate: -0.045,
      recalculated_weighted_avg_score: 0.854,
      delta_weighted_avg_score: 0.0,
      passed_cases: 167,
      failed_cases: 33,
      dimension_pass_rates: { rag_faithfulness_s1: 0.855 },
      flipped_case_ids: [1024, 1088, 1102],
      compute_elapsed_ms: 6,
    })
    vi.mocked(runsApi.recommendThresholds).mockResolvedValue({
      run_no: 'RUN_20261112_0001',
      dataset_split_filter: 'DEV',
      metric: 'youden_j',
      label_source: 'ground_truth',
      labeled_cases: 40,
      suite_pass_threshold: 0.78,
      dimension_overrides: {
        rag_faithfulness_s1: {
          threshold: 0.82,
          weight: 2.0,
          is_blocker: true,
          best_youden_j: 0.74,
          best_f1: 0.89,
        },
      },
      best_youden_j: 0.76,
      best_f1: 0.91,
      preview: {
        recalculated_pass_rate: 0.842,
        delta_pass_rate: -0.038,
        recalculated_weighted_avg_score: 0.854,
        delta_weighted_avg_score: 0.0,
        passed_cases: 168,
        failed_cases: 32,
        dimension_pass_rates: { rag_faithfulness_s1: 0.868 },
        flipped_case_ids: [1024, 1088],
        compute_elapsed_ms: 5,
      },
    })
    vi.mocked(suitesApi.detail).mockResolvedValue(suiteRecord)
    vi.mocked(suitesApi.update).mockImplementation(async (_code, payload) => ({
      ...suiteRecord,
      ...payload,
    }))
    vi.mocked(runsApi.create).mockResolvedValue(holdoutCreated)

    const wrapper = mount(ReportsView)
    await flushPromises()

    // Raw <input type="range"> must be completely eliminated in favor of <el-slider>
    expect(wrapper.find('input[type="range"]').exists()).toBe(false)
    const sliders = wrapper.findAllComponents({ name: 'ElSlider' })
    expect(sliders.length).toBeGreaterThanOrEqual(2)

    // Trigger suite threshold change via ElSlider and advance 80ms throttle timer
    sliders[0].vm.$emit('update:modelValue', 0.8)
    vi.advanceTimersByTime(100)
    await flushPromises()

    expect(runsApi.recalculate).toHaveBeenCalledWith(
      'RUN_20261112_0001',
      expect.objectContaining({
        suite_pass_threshold: 0.8,
      }),
    )

    const elapsedBadge = wrapper.find('[data-testid="recalc-elapsed-badge"]')
    expect(elapsedBadge.exists()).toBe(true)
    expect(elapsedBadge.text()).toContain('内存重算 6 ms · 0 次模型调用')

    const flippedBtn = wrapper.find('[data-testid="btn-filter-flipped"]')
    expect(flippedBtn.text()).toContain('3')

    // Recommend optimal thresholds on DEV split
    const recBtn = wrapper.find('[data-testid="btn-recommend-thresholds"]')
    expect(recBtn.attributes('disabled')).toBeUndefined()
    await recBtn.trigger('click')
    await flushPromises()

    expect(runsApi.recommendThresholds).toHaveBeenCalledWith('RUN_20261112_0001', 'youden_j')
    expect(wrapper.text()).toContain('84.2%')

    // Lock calibrated thresholds back to Suite via PUT /api/v1/suites/{suite_code}
    const lockBtn = wrapper.find('[data-testid="btn-lock-suite-thresholds"]')
    expect(lockBtn.exists()).toBe(true)
    expect(lockBtn.attributes('disabled')).toBeUndefined()
    await lockBtn.trigger('click')
    await flushPromises()

    expect(suitesApi.update).toHaveBeenCalledWith(
      'suite_gov_e2e_v1',
      expect.objectContaining({
        suite_code: 'suite_gov_e2e_v1',
        suite_pass_threshold: 0.78,
        dimensions_json: [
          expect.objectContaining({
            dimension_code: 'rag_faithfulness_s1',
            version: 1,
            threshold: 0.82,
            weight: 2.0,
            is_blocker: true,
          }),
        ],
      }),
    )

    // Launch HOLDOUT verification batch directly from bench
    const holdoutBtn = wrapper.find('[data-testid="btn-launch-holdout-run"]')
    expect(holdoutBtn.exists()).toBe(true)
    await holdoutBtn.trigger('click')
    await flushPromises()

    expect(runsApi.create).toHaveBeenCalledWith(
      expect.objectContaining({
        suite_code: 'suite_gov_e2e_v1',
        dataset_id: 12,
        dataset_split_filter: 'HOLDOUT',
      }),
    )
  })
})
