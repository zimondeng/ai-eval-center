import { computed, ref, shallowRef } from 'vue'
import { defineStore } from 'pinia'
import { runsApi } from '@/api/endpoints'
import type {
  CompareRunsResult,
  DimensionOverrideInput,
  EvalRunCaseItem,
  EvalRunDetail,
  RecalculateResult,
} from '@/api/types'

export interface EditableDimensionThreshold {
  dimension_code: string
  dimension_name: string
  version: number
  threshold: number
  weight: number
  is_blocker: boolean
  original_threshold: number
  original_weight: number
  original_blocker: boolean
}

export const useReportStore = defineStore('report', () => {
  const currentRun = ref<EvalRunDetail | null>(null)
  // DESIGN.md §13.3: Use shallowRef for 500+ run_cases & raw_answers_json ledgers
  const runCases = shallowRef<EvalRunCaseItem[]>([])
  const runCasesTotal = ref(0)
  const compareReport = shallowRef<CompareRunsResult | null>(null)
  const activeDrawerCase = shallowRef<EvalRunCaseItem | null>(null)

  const loadingReport = ref(false)
  const loadingCases = ref(false)
  const recommending = ref(false)
  const errorMessage = ref<string | null>(null)

  // Real-time zero-cost threshold calibration state
  const suitePassThreshold = ref(0.75)
  const originalSuitePassThreshold = ref(0.75)
  const dimensionControls = ref<EditableDimensionThreshold[]>([])
  const recalcResult = ref<RecalculateResult | null>(null)

  let throttleTimer: ReturnType<typeof setTimeout> | null = null
  let pendingRecalcAfterFlight = false
  let inFlightRecalc = false

  const isDevSplit = computed(() => currentRun.value?.dataset_split_filter === 'DEV')

  const isModifiedFromOriginal = computed(() => {
    if (Math.abs(suitePassThreshold.value - originalSuitePassThreshold.value) > 1e-6) {
      return true
    }
    return dimensionControls.value.some(
      (d) =>
        Math.abs(d.threshold - d.original_threshold) > 1e-6 ||
        Math.abs(d.weight - d.original_weight) > 1e-6 ||
        d.is_blocker !== d.original_blocker,
    )
  })

  function hydrateCalibrationControls(detail: EvalRunDetail): void {
    const baseSuiteT = Number(detail.suite_pass_threshold ?? 0.75)
    suitePassThreshold.value = baseSuiteT
    originalSuitePassThreshold.value = baseSuiteT
    recalcResult.value = null

    const summaryMap = detail.dimension_summary_json ?? {}
    const controls: EditableDimensionThreshold[] = []
    for (const binding of detail.suite_dimensions ?? []) {
      const sumEntry = summaryMap[binding.dimension_code]
      const t = Number(sumEntry?.threshold ?? binding.threshold ?? 0.75)
      const w = Number(sumEntry?.weight ?? binding.weight ?? 1.0)
      const b = Boolean(sumEntry?.is_blocker ?? binding.is_blocker ?? false)
      controls.push({
        dimension_code: binding.dimension_code,
        dimension_name: sumEntry?.dimension_name ?? binding.dimension_code,
        version: Number(binding.version ?? 1),
        threshold: t,
        weight: w,
        is_blocker: b,
        original_threshold: t,
        original_weight: w,
        original_blocker: b,
      })
    }
    dimensionControls.value = controls
  }

  async function loadRunReport(runNo: string, baselineOverrideNo?: string | null): Promise<void> {
    loadingReport.value = true
    errorMessage.value = null
    try {
      const detail = await runsApi.detail(runNo)
      currentRun.value = detail
      hydrateCalibrationControls(detail)

      const targetBaselineNo = baselineOverrideNo || detail.baseline_run_no
      if (targetBaselineNo) {
        compareReport.value = await runsApi.compare(runNo, targetBaselineNo)
      } else {
        compareReport.value = null
      }
    } catch (err) {
      errorMessage.value = err instanceof Error ? err.message : '加载实验批次报告失败'
      throw err
    } finally {
      loadingReport.value = false
    }
  }

  async function loadRunCases(
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
  ): Promise<void> {
    loadingCases.value = true
    try {
      const res = await runsApi.listCases(runNo, {
        page: 1,
        page_size: 100,
        ...params,
      })
      runCases.value = res.items
      runCasesTotal.value = res.total
    } finally {
      loadingCases.value = false
    }
  }

  function buildOverridesPayload(): Record<string, DimensionOverrideInput> {
    const out: Record<string, DimensionOverrideInput> = {}
    for (const item of dimensionControls.value) {
      out[item.dimension_code] = {
        threshold: item.threshold,
        weight: item.weight,
        is_blocker: item.is_blocker,
      }
    }
    return out
  }

  async function executeRecalculateNow(): Promise<void> {
    const run = currentRun.value
    if (!run) return
    if (inFlightRecalc) {
      pendingRecalcAfterFlight = true
      return
    }
    inFlightRecalc = true
    try {
      const result = await runsApi.recalculate(run.run_no, {
        suite_pass_threshold: suitePassThreshold.value,
        dimension_overrides: buildOverridesPayload(),
      })
      recalcResult.value = result
    } finally {
      inFlightRecalc = false
      if (pendingRecalcAfterFlight) {
        pendingRecalcAfterFlight = false
        void executeRecalculateNow()
      }
    }
  }

  // DESIGN.md §9 & §13.3: 80ms throttled in-memory recalculate with ZERO loading mask flash
  function scheduleRecalculate(): void {
    if (!currentRun.value) return
    if (throttleTimer) {
      return
    }
    throttleTimer = setTimeout(() => {
      throttleTimer = null
      void executeRecalculateNow()
    }, 80)
  }

  async function resetThresholdsToOriginal(): Promise<void> {
    if (throttleTimer) {
      clearTimeout(throttleTimer)
      throttleTimer = null
    }
    suitePassThreshold.value = originalSuitePassThreshold.value
    for (const item of dimensionControls.value) {
      item.threshold = item.original_threshold
      item.weight = item.original_weight
      item.is_blocker = item.original_blocker
    }
    recalcResult.value = null
  }

  async function recommendOptimalThresholds(metric: 'youden_j' | 'f1' = 'youden_j'): Promise<void> {
    const run = currentRun.value
    if (!run || run.dataset_split_filter !== 'DEV') {
      return
    }
    recommending.value = true
    try {
      const rec = await runsApi.recommendThresholds(run.run_no, metric)
      suitePassThreshold.value = rec.suite_pass_threshold
      for (const item of dimensionControls.value) {
        const recDim = rec.dimension_overrides[item.dimension_code]
        if (recDim) {
          item.threshold = recDim.threshold
          item.weight = recDim.weight
          item.is_blocker = recDim.is_blocker
        }
      }
      recalcResult.value = rec.preview
    } finally {
      recommending.value = false
    }
  }

  function openCaseDrawer(row: EvalRunCaseItem): void {
    activeDrawerCase.value = row
  }

  function closeCaseDrawer(): void {
    activeDrawerCase.value = null
  }

  return {
    currentRun,
    runCases,
    runCasesTotal,
    compareReport,
    activeDrawerCase,
    loadingReport,
    loadingCases,
    recommending,
    errorMessage,
    suitePassThreshold,
    originalSuitePassThreshold,
    dimensionControls,
    recalcResult,
    isDevSplit,
    isModifiedFromOriginal,
    hydrateCalibrationControls,
    loadRunReport,
    loadRunCases,
    executeRecalculateNow,
    scheduleRecalculate,
    resetThresholdsToOriginal,
    recommendOptimalThresholds,
    openCaseDrawer,
    closeCaseDrawer,
  }
})
