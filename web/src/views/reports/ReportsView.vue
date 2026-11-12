<script setup lang="ts">
import { computed, onMounted, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Download, Refresh, Search, WarningFilled } from '@element-plus/icons-vue'
import { runsApi } from '@/api/endpoints'
import { useReportStore } from '@/stores/reportStore'
import SplitBadge from '@/components/SplitBadge.vue'
import DualStatusBadge from '@/components/DualStatusBadge.vue'
import EmptyStateBox from '@/components/EmptyStateBox.vue'
import ThresholdCalibrationBench from './ThresholdCalibrationBench.vue'
import ReportChartsPanel from './ReportChartsPanel.vue'
import ProbabilityLedgerDrawer from './ProbabilityLedgerDrawer.vue'
import type { EvalRunCaseItem, EvalRunRecord, SemanticRegressionTag } from '@/api/types'

const route = useRoute()
const router = useRouter()
const reportStore = useReportStore()

const runOptions = shallowRef<EvalRunRecord[]>([])
const selectedRunNo = ref<string>('')
const selectedBaselineNo = ref<string>('')
const activeDimensionCode = ref<string>('')

// Case table filters
const passedFilter = ref<'ALL' | 'PASSED' | 'FAILED'>('ALL')
const onlyHumanReview = ref(false)
const selectedRegressionTag = ref<SemanticRegressionTag | null>(null)
const flippedIdsFilter = ref<number[]>([])
const searchQuery = ref('')
const currentPage = ref(1)
const pageSize = ref(100)
const drawerOpen = ref(false)

const currentRun = computed(() => reportStore.currentRun)

const hasCrossModelWarning = computed(() =>
  Boolean(reportStore.compareReport?.cross_model_warning || currentRun.value?.cross_model_warning),
)

const crossModelWarningCopy = computed(() => {
  const curModel = currentRun.value?.pinned_model_name || '--'
  const baseModel =
    reportStore.compareReport?.baseline_run?.pinned_model_name ||
    currentRun.value?.baseline_pinned_model_name ||
    '--'
  return `标尺变更提示：当前批次判别模型（${curModel}）与基线批次（${baseModel}）不一致。不同判别模型的原始概率标尺存在差异，涨跌分值仅供参考，请重点审阅「语义回归差异」明细。`
})

const fingerprintEntries = computed(() => {
  const fp = currentRun.value?.sut_fingerprint_json
  if (!fp || typeof fp !== 'object') return []
  return Object.entries(fp).map(([k, v]) => ({
    key: k,
    val: typeof v === 'string' ? v : JSON.stringify(v),
  }))
})

const baselineCandidateRuns = computed(() =>
  runOptions.value.filter((r) => r.run_no !== selectedRunNo.value),
)

const displayPassRate = computed(() => {
  if (reportStore.recalcResult) {
    return reportStore.recalcResult.recalculated_pass_rate
  }
  return currentRun.value?.pass_rate ?? null
})

const displayPassedCases = computed(() => {
  if (reportStore.recalcResult) {
    return reportStore.recalcResult.passed_cases
  }
  return currentRun.value?.passed_cases ?? 0
})

const displayWeightedScore = computed(() => {
  if (reportStore.recalcResult) {
    return reportStore.recalcResult.recalculated_weighted_avg_score
  }
  return currentRun.value?.weighted_avg_score ?? null
})

const passRateDeltaInfo = computed(() => {
  if (reportStore.recalcResult) {
    const d = reportStore.recalcResult.delta_pass_rate
    return {
      prefix: '重算',
      delta: d,
      formatted: `${d >= 0 ? '+' : ''}${(d * 100).toFixed(1)}%`,
    }
  }
  const baseDelta = reportStore.compareReport?.kpi_deltas?.delta_pass_rate
  if (baseDelta !== null && baseDelta !== undefined) {
    return {
      prefix: '较基线',
      delta: baseDelta,
      formatted: `${baseDelta >= 0 ? '+' : ''}${(baseDelta * 100).toFixed(1)}%`,
    }
  }
  return null
})

const hasActiveCaseFilter = computed(
  () =>
    passedFilter.value !== 'ALL' ||
    onlyHumanReview.value ||
    selectedRegressionTag.value !== null ||
    flippedIdsFilter.value.length > 0 ||
    searchQuery.value.trim().length > 0,
)

const activeDrawerIndex = computed(() => {
  const target = reportStore.activeDrawerCase
  if (!target) return -1
  return reportStore.runCases.findIndex((c) => c.id === target.id)
})

function formatPercent(val: number | null | undefined): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '--'
  return `${(val * 100).toFixed(1)}%`
}

function formatScore(val: number | null | undefined, digits = 3): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '--'
  return val.toFixed(digits)
}

async function fetchCases(): Promise<void> {
  if (!selectedRunNo.value) return
  const passedParam =
    passedFilter.value === 'PASSED' ? 1 : passedFilter.value === 'FAILED' ? 0 : undefined
  await reportStore.loadRunCases(selectedRunNo.value, {
    passed: passedParam,
    needs_human_review: onlyHumanReview.value ? 1 : undefined,
    regression_tag: selectedRegressionTag.value ?? undefined,
    case_ids: flippedIdsFilter.value.length > 0 ? flippedIdsFilter.value.join(',') : undefined,
    q: searchQuery.value.trim() || undefined,
    page: currentPage.value,
    page_size: pageSize.value,
  })
}

async function loadSelectedRun(runNo: string, baselineNo?: string | null): Promise<void> {
  if (!runNo) return
  selectedRunNo.value = runNo
  await reportStore.loadRunReport(runNo, baselineNo)
  selectedBaselineNo.value =
    baselineNo !== undefined
      ? baselineNo || ''
      : reportStore.currentRun?.baseline_run_no || ''
  activeDimensionCode.value = reportStore.dimensionControls[0]?.dimension_code ?? ''
  flippedIdsFilter.value = []
  currentPage.value = 1
  await fetchCases()
}

async function initView(): Promise<void> {
  const listRes = await runsApi.list({ page: 1, page_size: 50 })
  runOptions.value = listRes.items

  const paramRunNo = typeof route.params.runNo === 'string' ? route.params.runNo : ''
  const queryBase = typeof route.query.baseline === 'string' ? route.query.baseline : undefined
  const targetRunNo = paramRunNo || listRes.items[0]?.run_no || ''
  if (targetRunNo) {
    await loadSelectedRun(targetRunNo, queryBase)
  }
}

async function onRunSwitch(nextRunNo: string): Promise<void> {
  if (!nextRunNo) return
  router.replace({ name: 'report-detail', params: { runNo: nextRunNo } })
  await loadSelectedRun(nextRunNo)
}

async function onBaselineSwitch(nextBaseNo: string): Promise<void> {
  if (!selectedRunNo.value) return
  await reportStore.loadRunReport(selectedRunNo.value, nextBaseNo || null)
}

function onToggleFlippedFilter(caseIds: number[]): void {
  if (flippedIdsFilter.value.length > 0) {
    flippedIdsFilter.value = []
  } else {
    flippedIdsFilter.value = [...caseIds]
  }
  currentPage.value = 1
  void fetchCases()
}

function onResetFlippedFilter(): void {
  if (flippedIdsFilter.value.length > 0) {
    flippedIdsFilter.value = []
    currentPage.value = 1
    void fetchCases()
  }
}

function onSelectRegressionTag(tag: SemanticRegressionTag | null): void {
  selectedRegressionTag.value = tag
  currentPage.value = 1
  void fetchCases()
}

function resetAllCaseFilters(): void {
  passedFilter.value = 'ALL'
  onlyHumanReview.value = false
  selectedRegressionTag.value = null
  flippedIdsFilter.value = []
  searchQuery.value = ''
  currentPage.value = 1
  void fetchCases()
}

function openLedgerForRow(row: EvalRunCaseItem): void {
  reportStore.openCaseDrawer(row)
  drawerOpen.value = true
}

function stepDrawerCase(offset: -1 | 1): void {
  const nextIdx = activeDrawerIndex.value + offset
  if (nextIdx >= 0 && nextIdx < reportStore.runCases.length) {
    reportStore.openCaseDrawer(reportStore.runCases[nextIdx])
  }
}

function exportReportJson(): void {
  if (!currentRun.value) return
  const payload = {
    exported_at: new Date().toISOString(),
    run: currentRun.value,
    recalculated_preview: reportStore.recalcResult,
    compare_report: reportStore.compareReport,
  }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${currentRun.value.run_no}-report.json`
  link.click()
  URL.revokeObjectURL(url)
}

watch(
  () => route.params.runNo,
  (newRunNo) => {
    if (typeof newRunNo === 'string' && newRunNo && newRunNo !== selectedRunNo.value) {
      void loadSelectedRun(newRunNo)
    }
  },
)

onMounted(() => {
  void initView()
})
</script>

<template>
  <div class="ev-page">
    <!-- Top Page Bar: Run Selector + Baseline Selector + Export -->
    <header class="ev-page-header">
      <div>
        <h1 class="ev-page-title">回归对比与零成本阈值校准报告</h1>
        <p class="ev-page-subtitle">
          原子概率账本落盘后支持零模型调用实时重算门禁切点、双批次语义回归对照与单题概率溯源
        </p>
      </div>

      <div class="ev-report-toolbar">
        <el-select
          :model-value="selectedRunNo"
          placeholder="选择实验批次"
          size="default"
          class="ev-report-toolbar__run-select"
          aria-label="选择当前实验批次"
          @change="onRunSwitch"
        >
          <el-option
            v-for="item in runOptions"
            :key="item.run_no"
            :label="`${item.run_no} · ${item.run_name} (${item.dataset_split_filter})`"
            :value="item.run_no"
          />
        </el-select>

        <el-select
          v-model="selectedBaselineNo"
          clearable
          placeholder="对比基线批次（可选）"
          size="default"
          class="ev-report-toolbar__base-select"
          aria-label="选择对比基线批次"
          @change="onBaselineSwitch"
        >
          <el-option
            v-for="base in baselineCandidateRuns"
            :key="base.run_no"
            :label="`基线: ${base.run_no} (${base.pinned_model_name})`"
            :value="base.run_no"
          />
        </el-select>

        <el-button :disabled="!currentRun" @click="exportReportJson">
          <el-icon><Download /></el-icon>
          <span>导出报告 JSON</span>
        </el-button>

        <el-button
          class="ev-icon-btn"
          aria-label="刷新当前批次报告"
          :disabled="!selectedRunNo"
          @click="loadSelectedRun(selectedRunNo, selectedBaselineNo || null)"
        >
          <el-icon><Refresh /></el-icon>
        </el-button>
      </div>
    </header>

    <!-- Error state -->
    <div v-if="reportStore.errorMessage" class="ev-report-error" role="alert">
      <span>{{ reportStore.errorMessage }}</span>
    </div>

    <!-- Empty state if no run exists at all -->
    <section v-if="!currentRun && !reportStore.loadingReport" class="ev-surface">
      <EmptyStateBox
        entity-name="实验批次报告"
        description="暂无可展示的实验批次报告。请先前往「实验批次」模块发起一次评测运行。"
        action-label="前往实验批次"
        @action="router.push('/runs')"
      />
    </section>

    <template v-else-if="currentRun">
      <!-- Persistent Cross-Model Warning Banner (DESIGN.md §11 & §12) -->
      <div
        v-if="hasCrossModelWarning"
        data-testid="cross-model-warning-banner"
        class="ev-cross-model-banner"
        role="alert"
      >
        <el-icon class="ev-cross-model-banner__icon"><WarningFilled /></el-icon>
        <span class="ev-cross-model-banner__text">{{ crossModelWarningCopy }}</span>
      </div>

      <!-- Run Contract & Fingerprint Header -->
      <section class="ev-surface ev-contract-bar" aria-label="实验批次契约快照">
        <div class="ev-contract-bar__main">
          <span class="ev-contract-bar__no ev-mono">{{ currentRun.run_no }}</span>
          <span class="ev-contract-bar__name">{{ currentRun.run_name }}</span>
          <SplitBadge :split="currentRun.dataset_split_filter" />
          <DualStatusBadge
            :tone="
              currentRun.status === 'COMPLETED'
                ? 'success'
                : currentRun.status === 'FAILED'
                  ? 'danger'
                  : 'warning'
            "
            :label="currentRun.status"
          />
        </div>

        <div class="ev-contract-bar__meta ev-mono">
          <span>套件: {{ currentRun.suite_code }}</span>
          <span>数据集: {{ currentRun.dataset_code }}@{{ currentRun.dataset_version_tag }}</span>
          <span>适配器: {{ currentRun.adapter_code }}</span>
          <span>锁定判别模型: {{ currentRun.pinned_model_name }}</span>
          <span
            v-for="fp in fingerprintEntries"
            :key="fp.key"
            class="ev-contract-bar__fp"
          >
            {{ fp.key }}={{ fp.val }}
          </span>
        </div>
      </section>

      <!-- KPI Metric Cards (tabular-nums, units adjacent, dual-coded deltas) -->
      <section class="ev-kpi-grid" aria-label="实验核心指标">
        <div class="ev-surface ev-kpi-card">
          <span class="ev-kpi-card__label">门禁综合通过率</span>
          <div class="ev-kpi-card__row">
            <span class="ev-kpi-card__val ev-mono">{{ formatPercent(displayPassRate) }}</span>
            <DualStatusBadge
              v-if="passRateDeltaInfo"
              :tone="passRateDeltaInfo.delta >= 0 ? 'success' : 'danger'"
              :symbol="passRateDeltaInfo.delta >= 0 ? '↑' : '↓'"
              :label="passRateDeltaInfo.prefix"
              :value="passRateDeltaInfo.formatted"
            />
          </div>
          <span class="ev-kpi-card__sub ev-mono">
            {{ displayPassedCases }} / {{ currentRun.completed_cases }} 条用例达标
          </span>
        </div>

        <div class="ev-surface ev-kpi-card">
          <span class="ev-kpi-card__label">加权综合期望分 [0, 1]</span>
          <div class="ev-kpi-card__row">
            <span class="ev-kpi-card__val ev-mono">{{ formatScore(displayWeightedScore, 3) }}</span>
          </div>
          <span class="ev-kpi-card__sub ev-mono">
            复核线 confidence &lt; {{ formatScore(currentRun.confidence_review_floor, 2) }} · 待复核
            {{ currentRun.uncertain_cases }} 条
          </span>
        </div>

        <div class="ev-surface ev-kpi-card">
          <span class="ev-kpi-card__label">被测系统响应耗时 (SUT Latency)</span>
          <div class="ev-kpi-card__row">
            <span class="ev-kpi-card__val ev-mono">
              {{ currentRun.p50_sut_latency_ms ?? '--' }}
              <small class="ev-kpi-card__unit">ms (P50)</small>
            </span>
          </div>
          <span class="ev-kpi-card__sub ev-mono">
            P95: {{ currentRun.p95_sut_latency_ms ?? '--' }} ms · P50 TTFT:
            {{ currentRun.p50_ttft_ms ?? '--' }} ms
          </span>
        </div>

        <div class="ev-surface ev-kpi-card">
          <span class="ev-kpi-card__label">同状态多问合批效率</span>
          <div class="ev-kpi-card__row">
            <span class="ev-kpi-card__val ev-mono">
              {{ currentRun.batch_compression_ratio.toFixed(1) }}x
            </span>
            <DualStatusBadge
              tone="success"
              symbol="↑"
              label="节省调用"
              :value="formatPercent(currentRun.batch_saving_rate)"
            />
          </div>
          <span class="ev-kpi-card__sub ev-mono">
            请求 {{ currentRun.total_decision_calls }} 次（原
            {{ currentRun.unbatched_question_count }} 题）·
            {{ currentRun.total_input_tokens }} tokens
          </span>
        </div>
      </section>

      <!-- Zero-Cost Real-Time Threshold Calibration Bench -->
      <ThresholdCalibrationBench
        v-model:active-dimension-code="activeDimensionCode"
        :filtering-flipped="flippedIdsFilter.length > 0"
        @toggle-flipped-filter="onToggleFlippedFilter"
        @reset-flipped-filter="onResetFlippedFilter"
      />

      <!-- ECharts Panel -->
      <ReportChartsPanel
        v-model:active-dimension-code="activeDimensionCode"
        :run="currentRun"
        :compare-report="reportStore.compareReport"
        :dimension-controls="reportStore.dimensionControls"
        :selected-regression-tag="selectedRegressionTag"
        @select-regression-tag="onSelectRegressionTag"
      />

      <!-- Case Attribution Table -->
      <section class="ev-surface ev-cases-card" aria-label="用例归因明细表">
        <div class="ev-cases-card__head">
          <div>
            <h2 class="ev-section-title">
              用例归因与概率账本明细
              <span class="ev-cases-card__count ev-mono">({{ reportStore.runCasesTotal }} 条)</span>
            </h2>
          </div>
        </div>

        <div class="ev-filter-bar">
          <div class="ev-filter-bar__left">
            <el-segmented
              v-model="passedFilter"
              :options="[
                { label: '全部结果', value: 'ALL' },
                { label: '门禁通过', value: 'PASSED' },
                { label: '门禁拦截', value: 'FAILED' },
              ]"
              aria-label="按门禁通过状态筛选"
              @change="
                () => {
                  currentPage = 1
                  fetchCases()
                }
              "
            />

            <el-checkbox
              v-model="onlyHumanReview"
              @change="
                () => {
                  currentPage = 1
                  fetchCases()
                }
              "
            >
              仅看低把握度待复核
            </el-checkbox>

            <el-select
              v-model="selectedRegressionTag"
              clearable
              placeholder="语义回归分类"
              size="small"
              class="ev-cases-card__reg-select"
              aria-label="按语义回归分类筛选"
              @change="
                () => {
                  currentPage = 1
                  fetchCases()
                }
              "
            >
              <el-option label="SAME · 语义等价" value="SAME" />
              <el-option label="SPECIFICITY_DIFFERS · 详略差异" value="SPECIFICITY_DIFFERS" />
              <el-option label="ACTION_DIFFERS · 动作分歧" value="ACTION_DIFFERS" />
              <el-option label="FACT_DIFFERS · 事实冲突" value="FACT_DIFFERS" />
            </el-select>

            <span v-if="flippedIdsFilter.length > 0" class="ev-flipped-chip ev-mono">
              仅看阈值跳变用例 ({{ flippedIdsFilter.length }} 条)
            </span>
          </div>

          <div class="ev-filter-bar__right">
            <el-input
              v-model="searchQuery"
              clearable
              size="small"
              placeholder="搜索用例编号或问题文本"
              class="ev-cases-card__search"
              aria-label="搜索用例编号或输入文本"
              @keyup.enter="
                () => {
                  currentPage = 1
                  fetchCases()
                }
              "
              @clear="
                () => {
                  currentPage = 1
                  fetchCases()
                }
              "
            >
              <template #prefix>
                <el-icon><Search /></el-icon>
              </template>
            </el-input>

            <el-button v-if="hasActiveCaseFilter" size="small" @click="resetAllCaseFilters">
              重置
            </el-button>
          </div>
        </div>

        <EmptyStateBox
          v-if="reportStore.runCases.length === 0 && !reportStore.loadingCases"
          :filtered="hasActiveCaseFilter"
          entity-name="用例评测记录"
          @reset="resetAllCaseFilters"
        />

        <el-table
          v-else
          :data="reportStore.runCases"
          row-key="id"
          size="small"
          class="ev-cases-table"
          @row-click="openLedgerForRow"
        >
          <el-table-column label="用例编号 / 分集" min-width="180">
            <template #default="{ row }">
              <div class="ev-case-id-cell">
                <span class="ev-mono ev-case-id-cell__code">{{ row.case_code }}</span>
                <SplitBadge :split="row.dataset_split" compact />
              </div>
            </template>
          </el-table-column>

          <el-table-column label="用户输入摘要" min-width="260" show-overflow-tooltip>
            <template #default="{ row }">
              <span>{{ row.input_text }}</span>
            </template>
          </el-table-column>

          <el-table-column label="综合期望分" width="118" align="right">
            <template #default="{ row }">
              <span class="ev-num-right">{{ formatScore(row.overall_score, 3) }}</span>
            </template>
          </el-table-column>

          <el-table-column label="门禁与复核状态" width="210">
            <template #default="{ row }">
              <div class="ev-case-status-cell">
                <DualStatusBadge
                  :tone="row.overall_passed ? 'success' : 'danger'"
                  :label="row.overall_passed ? '通过' : '拦截'"
                />
                <DualStatusBadge
                  v-if="row.needs_human_review"
                  tone="warning"
                  symbol="!"
                  label="待复核"
                  :value="formatScore(row.min_confidence, 2)"
                />
              </div>
            </template>
          </el-table-column>

          <el-table-column label="语义回归" width="168">
            <template #default="{ row }">
              <span v-if="row.regression_tag" class="ev-mono ev-reg-cell-tag">
                {{ row.regression_tag }}
              </span>
              <span v-else class="ev-text-muted">--</span>
            </template>
          </el-table-column>

          <el-table-column label="耗时 (SUT / TTFT)" width="150" align="right">
            <template #default="{ row }">
              <span class="ev-num-right">
                {{ row.sut_latency_ms ?? '--' }} / {{ row.ttft_ms ?? '--' }} ms
              </span>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="104" fixed="right" align="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click.stop="openLedgerForRow(row)">
                概率账本
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <!-- Right-Side 680px Probability Ledger Drawer -->
      <ProbabilityLedgerDrawer
        v-model="drawerOpen"
        :case-item="reportStore.activeDrawerCase"
        :has-prev="activeDrawerIndex > 0"
        :has-next="activeDrawerIndex >= 0 && activeDrawerIndex < reportStore.runCases.length - 1"
        @prev="stepDrawerCase(-1)"
        @next="stepDrawerCase(1)"
      />
    </template>
  </div>
</template>

<style scoped>
.ev-report-toolbar {
  display: flex;
  align-items: center;
  gap: var(--ev-space-sm);
  flex-wrap: wrap;
}

.ev-report-toolbar__run-select {
  width: 280px;
}

.ev-report-toolbar__base-select {
  width: 240px;
}

.ev-report-error {
  padding: 12px 16px;
  border-radius: var(--ev-radius-md);
  background-color: var(--ev-color-danger-subtle);
  border: 1px solid var(--ev-color-danger-border);
  color: var(--ev-color-danger);
  font-size: var(--ev-fs-body);
}

.ev-cross-model-banner {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 16px;
  border-radius: var(--ev-radius-lg);
  background-color: var(--ev-color-warning-subtle);
  border: 1px solid var(--ev-color-warning-border);
  color: var(--ev-color-warning);
  font-size: var(--ev-fs-body);
  line-height: 1.55;
}

.ev-cross-model-banner__icon {
  font-size: 18px;
  margin-top: 2px;
  flex-shrink: 0;
}

.ev-cross-model-banner__text {
  font-weight: 500;
}

.ev-contract-bar {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ev-contract-bar__main {
  display: flex;
  align-items: center;
  gap: var(--ev-space-sm);
  flex-wrap: wrap;
}

.ev-contract-bar__no {
  font-size: var(--ev-fs-title);
  font-weight: 600;
  color: var(--ev-color-primary);
}

.ev-contract-bar__name {
  font-size: var(--ev-fs-title);
  font-weight: 600;
  color: var(--ev-text-primary);
}

.ev-contract-bar__meta {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  font-size: var(--ev-fs-label);
  color: var(--ev-text-secondary);
}

.ev-contract-bar__fp {
  padding: 1px 6px;
  border-radius: var(--ev-radius-sm);
  background-color: var(--ev-bg-panel);
  color: var(--ev-text-primary);
}

.ev-kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--ev-space-lg);
}

.ev-kpi-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.ev-kpi-card__label {
  font-size: var(--ev-fs-label);
  font-weight: 600;
  color: var(--ev-text-secondary);
}

.ev-kpi-card__row {
  display: flex;
  align-items: baseline;
  gap: var(--ev-space-sm);
  flex-wrap: wrap;
}

.ev-kpi-card__val {
  font-size: var(--ev-fs-kpi);
  font-weight: 600;
  line-height: 1.25;
  color: var(--ev-text-primary);
}

.ev-kpi-card__unit {
  font-size: var(--ev-fs-label);
  font-weight: 500;
  color: var(--ev-text-tertiary);
}

.ev-kpi-card__sub {
  font-size: var(--ev-fs-label);
  color: var(--ev-text-tertiary);
}

.ev-cases-card {
  display: flex;
  flex-direction: column;
  gap: var(--ev-space-md);
}

.ev-cases-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.ev-cases-card__count {
  font-size: var(--ev-fs-body);
  color: var(--ev-text-secondary);
  margin-left: 6px;
}

.ev-cases-card__reg-select {
  width: 210px;
}

.ev-cases-card__search {
  width: 230px;
}

.ev-flipped-chip {
  padding: 2px 8px;
  border-radius: var(--ev-radius-sm);
  background-color: var(--ev-color-primary-subtle);
  color: var(--ev-color-primary);
  font-size: var(--ev-fs-label);
}

.ev-cases-table :deep(.el-table__row) {
  cursor: pointer;
}

.ev-case-id-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}

.ev-case-id-cell__code {
  font-weight: 600;
}

.ev-case-status-cell {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.ev-reg-cell-tag {
  font-size: var(--ev-fs-label);
  padding: 1px 6px;
  border-radius: var(--ev-radius-sm);
  background-color: var(--ev-bg-panel);
  color: var(--ev-text-secondary);
}

.ev-text-muted {
  color: var(--ev-text-tertiary);
}

@media (max-width: 1439px) {
  .ev-kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .ev-kpi-grid {
    grid-template-columns: 1fr;
  }

  .ev-report-toolbar__run-select,
  .ev-report-toolbar__base-select {
    width: 100%;
  }
}
</style>
