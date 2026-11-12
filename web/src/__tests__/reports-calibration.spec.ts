import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import ReportsView from '@/views/reports/ReportsView.vue'
import { runsApi } from '@/api/endpoints'
import type { CompareRunsResult, EvalRunDetail } from '@/api/types'

vi.mock('vue-router', () => ({
  useRoute: () => ({
    params: { runNo: 'RUN_20261112_0001' },
    query: {},
  }),
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
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
    detail: vi.fn(),
    listCases: vi.fn(),
    compare: vi.fn(),
    recalculate: vi.fn(),
    recommendThresholds: vi.fn(),
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
    total_eval_cost: 0.0,
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

describe('ReportsView: cross_model_warning, HOLDOUT guardrail & zero-cost recalculation', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  it('renders persistent cross_model_warning banner and disables threshold recommendation on HOLDOUT split', async () => {
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

    vi.mocked(runsApi.list).mockResolvedValue({
      items: [holdoutRun],
      total: 1,
      page: 1,
      page_size: 50,
    })
    vi.mocked(runsApi.detail).mockResolvedValue(holdoutRun)
    vi.mocked(runsApi.compare).mockResolvedValue(compareData)
    vi.mocked(runsApi.listCases).mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 100,
    })

    const wrapper = mount(ReportsView)
    await flushPromises()

    // 1. Verify exact cross_model_warning copy from DESIGN.md §12
    const warningBanner = wrapper.find('[data-testid="cross-model-warning-banner"]')
    expect(warningBanner.exists()).toBe(true)
    expect(warningBanner.text()).toContain(
      '标尺变更提示：当前批次判别模型（tev1:0.8b）与基线批次（typesafe/jev-1.14.0）不一致。不同判别模型的原始概率标尺存在差异，涨跌分值仅供参考，请重点审阅「语义回归差异」明细。',
    )

    // 2. Verify HOLDOUT anti-overfitting guardrail disables recommend button + surfaces exact tooltip copy
    const recBtn = wrapper.find('[data-testid="btn-recommend-thresholds"]')
    expect(recBtn.exists()).toBe(true)
    expect(recBtn.attributes('disabled')).toBeDefined()

    const guardReason = wrapper.find('[data-testid="holdout-guard-reason"]')
    expect(guardReason.attributes('data-guard-message')).toBe(HOLDOUT_GUARD_COPY)

    await recBtn.trigger('click')
    await flushPromises()
    expect(runsApi.recommendThresholds).not.toHaveBeenCalled()
  })

  it('updates pass rate, flipped cases and zero-model-call elapsed badge on DEV threshold recalculation & recommendation', async () => {
    vi.useFakeTimers()
    const devRun = makeRunDetail({
      dataset_split_filter: 'DEV',
      baseline_run_id: null,
      baseline_run_no: null,
      baseline_pinned_model_name: null,
      cross_model_warning: false,
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

    const wrapper = mount(ReportsView)
    await flushPromises()

    // Trigger threshold change via store/bench and advance 80ms throttle timer
    const suiteInput = wrapper.find('[data-testid="suite-threshold-native-range"]')
    expect(suiteInput.exists()).toBe(true)
    await suiteInput.setValue('0.80')
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

    // Recommend optimal thresholds is enabled on DEV split
    const recBtn = wrapper.find('[data-testid="btn-recommend-thresholds"]')
    expect(recBtn.attributes('disabled')).toBeUndefined()
    await recBtn.trigger('click')
    await flushPromises()

    expect(runsApi.recommendThresholds).toHaveBeenCalledWith('RUN_20261112_0001', 'youden_j')
    expect(wrapper.text()).toContain('84.2%')
  })
})
