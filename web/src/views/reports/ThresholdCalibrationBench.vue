<script setup lang="ts">
import { computed, ref } from 'vue'
import { Aim, Filter, RefreshLeft } from '@element-plus/icons-vue'
import { useReportStore, type EditableDimensionThreshold } from '@/stores/reportStore'
import SplitBadge from '@/components/SplitBadge.vue'

const props = defineProps<{
  activeDimensionCode: string
  filteringFlipped: boolean
}>()

const emit = defineEmits<{
  (e: 'update:activeDimensionCode', code: string): void
  (e: 'toggle-flipped-filter', caseIds: number[]): void
  (e: 'reset-flipped-filter'): void
}>()

const HOLDOUT_GUARD_COPY =
  '当前批次基于 HOLDOUT（留出验真集）执行。为防止测试集过拟合，自动阈值寻优仅允许在 DEV（调优集）上运行。'
const DEV_RECOMMEND_TIP =
  '基于 DEV 调优集人工标注真值计算最优 Youden J 切点，零模型调用即时预览'

const reportStore = useReportStore()
const metricMode = ref<'youden_j' | 'f1'>('youden_j')

const flippedCaseIds = computed(() => reportStore.recalcResult?.flipped_case_ids ?? [])
const flippedCount = computed(() => flippedCaseIds.value.length)

function formatPercent(rate: number | null | undefined): string {
  if (rate === null || rate === undefined || Number.isNaN(rate)) return '--'
  return `${(rate * 100).toFixed(1)}%`
}

function getDimensionPassRate(dimCode: string): number | null {
  if (reportStore.recalcResult?.dimension_pass_rates?.[dimCode] !== undefined) {
    return reportStore.recalcResult.dimension_pass_rates[dimCode]
  }
  const summary = reportStore.currentRun?.dimension_summary_json?.[dimCode]
  return summary ? summary.pass_rate : null
}

function onSuiteThresholdChange(): void {
  reportStore.scheduleRecalculate()
}

function onSuiteInputNumberChange(val: number | undefined): void {
  if (typeof val === 'number' && !Number.isNaN(val)) {
    reportStore.suitePassThreshold = Math.min(1, Math.max(0, val))
    reportStore.scheduleRecalculate()
  }
}

function onDimControlChange(dim: EditableDimensionThreshold): void {
  emit('update:activeDimensionCode', dim.dimension_code)
  reportStore.scheduleRecalculate()
}

function onDimThresholdInput(dim: EditableDimensionThreshold, val: number | undefined): void {
  if (typeof val === 'number' && !Number.isNaN(val)) {
    dim.threshold = Math.min(1, Math.max(0, val))
    onDimControlChange(dim)
  }
}

function onDimWeightInput(dim: EditableDimensionThreshold, val: number | undefined): void {
  if (typeof val === 'number' && !Number.isNaN(val)) {
    dim.weight = Math.max(0, val)
    onDimControlChange(dim)
  }
}

async function onResetClick(): Promise<void> {
  await reportStore.resetThresholdsToOriginal()
  emit('reset-flipped-filter')
}

async function onRecommendClick(): Promise<void> {
  if (!reportStore.isDevSplit) return
  await reportStore.recommendOptimalThresholds(metricMode.value)
}

function onToggleFlipped(): void {
  if (flippedCount.value === 0 && !props.filteringFlipped) return
  emit('toggle-flipped-filter', flippedCaseIds.value)
}
</script>

<template>
  <section class="ev-surface ev-calib-bench" aria-label="零成本实时阈值校准工作台">
    <div class="ev-calib-bench__header">
      <div class="ev-calib-bench__title-group">
        <h2 class="ev-section-title">零成本实时阈值校准台</h2>
        <SplitBadge v-if="reportStore.currentRun" :split="reportStore.currentRun.dataset_split_filter" compact />
        <span
          v-if="reportStore.recalcResult"
          data-testid="recalc-elapsed-badge"
          class="ev-recalc-pill ev-mono"
        >
          内存重算 {{ reportStore.recalcResult.compute_elapsed_ms }} ms · 0 次模型调用
        </span>
        <span v-else class="ev-calib-bench__hint">
          拖动滑块基于已落盘原子概率账本实时重算门禁通过率（0 次模型调用）
        </span>
      </div>

      <div class="ev-calib-bench__actions">
        <el-button
          data-testid="btn-filter-flipped"
          size="small"
          :class="{ 'ev-calib-bench__flipped-active': filteringFlipped }"
          :disabled="flippedCount === 0 && !filteringFlipped"
          @click="onToggleFlipped"
        >
          <el-icon><Filter /></el-icon>
          <span>筛选跳变用例 (</span>
          <span class="ev-mono">{{ flippedCount }}</span>
          <span>)</span>
        </el-button>

        <el-button
          data-testid="btn-reset-thresholds"
          size="small"
          :disabled="!reportStore.isModifiedFromOriginal && !reportStore.recalcResult"
          @click="onResetClick"
        >
          <el-icon><RefreshLeft /></el-icon>
          <span>重置默认阈值</span>
        </el-button>

        <el-select
          v-if="reportStore.isDevSplit"
          v-model="metricMode"
          size="small"
          class="ev-calib-bench__metric-select"
          aria-label="寻优目标指标"
        >
          <el-option label="Youden J 最优切点" value="youden_j" />
          <el-option label="F1 最优切点" value="f1" />
        </el-select>

        <el-tooltip
          :content="reportStore.isDevSplit ? DEV_RECOMMEND_TIP : HOLDOUT_GUARD_COPY"
          placement="top"
        >
          <span
            data-testid="holdout-guard-reason"
            class="ev-calib-bench__guard-wrap"
            :data-guard-message="reportStore.isDevSplit ? '' : HOLDOUT_GUARD_COPY"
          >
            <el-button
              data-testid="btn-recommend-thresholds"
              type="primary"
              size="small"
              :disabled="!reportStore.isDevSplit"
              :loading="reportStore.recommending"
              @click="onRecommendClick"
            >
              <el-icon><Aim /></el-icon>
              <span>推荐最优阈值</span>
            </el-button>
          </span>
        </el-tooltip>
      </div>
    </div>

    <div class="ev-panel ev-calib-bench__body">
      <div class="ev-calib-row ev-calib-row--suite">
        <div class="ev-calib-row__meta">
          <span class="ev-calib-row__name">套件综合门禁线</span>
          <span class="ev-calib-row__code ev-mono">suite_pass_threshold</span>
          <span class="ev-calib-row__policy ev-mono">
            策略: {{ reportStore.currentRun?.pass_policy ?? 'ALL_BLOCKERS_AND_WEIGHTED' }}
          </span>
        </div>

        <div class="ev-calib-row__controls">
          <input
            v-model.number="reportStore.suitePassThreshold"
            data-testid="suite-threshold-native-range"
            type="range"
            min="0"
            max="1"
            step="0.01"
            class="ev-range-input"
            aria-label="套件综合门禁阈值滑块"
            @input="onSuiteThresholdChange"
          />
          <el-input-number
            :model-value="reportStore.suitePassThreshold"
            size="small"
            :min="0"
            :max="1"
            :step="0.01"
            :precision="2"
            controls-position="right"
            class="ev-calib-row__num ev-mono"
            aria-label="套件综合门禁阈值数值"
            @change="onSuiteInputNumberChange"
          />
        </div>

        <div class="ev-calib-row__stat">
          <span class="ev-calib-row__stat-label">综合通过率</span>
          <span class="ev-calib-row__stat-val ev-mono">
            {{
              formatPercent(
                reportStore.recalcResult
                  ? reportStore.recalcResult.recalculated_pass_rate
                  : reportStore.currentRun?.pass_rate,
              )
            }}
          </span>
        </div>
      </div>

      <div
        v-for="dim in reportStore.dimensionControls"
        :key="dim.dimension_code"
        class="ev-calib-row"
        :class="{ 'ev-calib-row--selected': activeDimensionCode === dim.dimension_code }"
        @click="emit('update:activeDimensionCode', dim.dimension_code)"
      >
        <div class="ev-calib-row__meta">
          <span class="ev-calib-row__name">{{ dim.dimension_name }}</span>
          <span class="ev-calib-row__code ev-mono">{{ dim.dimension_code }} · v{{ dim.version }}</span>
          <label class="ev-calib-row__blocker" @click.stop>
            <el-switch
              v-model="dim.is_blocker"
              size="small"
              aria-label="一票否决开关"
              @change="onDimControlChange(dim)"
            />
            <span>一票否决</span>
          </label>
          <label class="ev-calib-row__weight" @click.stop>
            <span>权重</span>
            <el-input-number
              :model-value="dim.weight"
              size="small"
              :min="0"
              :max="10"
              :step="0.1"
              :precision="1"
              controls-position="right"
              class="ev-calib-row__weight-num ev-mono"
              aria-label="维度权重"
              @change="(v: number | undefined) => onDimWeightInput(dim, v)"
            />
          </label>
        </div>

        <div class="ev-calib-row__controls" @click.stop>
          <input
            v-model.number="dim.threshold"
            :data-testid="`dim-threshold-range-${dim.dimension_code}`"
            type="range"
            min="0"
            max="1"
            step="0.01"
            class="ev-range-input"
            :aria-label="`${dim.dimension_name}阈值滑块`"
            @input="onDimControlChange(dim)"
          />
          <el-input-number
            :model-value="dim.threshold"
            size="small"
            :min="0"
            :max="1"
            :step="0.01"
            :precision="2"
            controls-position="right"
            class="ev-calib-row__num ev-mono"
            :aria-label="`${dim.dimension_name}阈值数值`"
            @change="(v: number | undefined) => onDimThresholdInput(dim, v)"
          />
        </div>

        <div class="ev-calib-row__stat">
          <span class="ev-calib-row__stat-label">维度达标率</span>
          <span class="ev-calib-row__stat-val ev-mono">
            {{ formatPercent(getDimensionPassRate(dim.dimension_code)) }}
          </span>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.ev-calib-bench {
  display: flex;
  flex-direction: column;
  gap: var(--ev-space-md);
}

.ev-calib-bench__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ev-space-md);
  flex-wrap: wrap;
}

.ev-calib-bench__title-group {
  display: flex;
  align-items: center;
  gap: var(--ev-space-sm);
  flex-wrap: wrap;
}

.ev-calib-bench__hint {
  font-size: var(--ev-fs-label);
  color: var(--ev-text-tertiary);
}

.ev-recalc-pill {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: var(--ev-radius-sm);
  font-size: var(--ev-fs-label);
  color: var(--ev-color-primary);
  background-color: var(--ev-color-primary-subtle);
  border: 1px solid var(--ev-color-primary-border);
}

.ev-calib-bench__actions {
  display: flex;
  align-items: center;
  gap: var(--ev-space-sm);
  flex-wrap: wrap;
}

.ev-calib-bench__flipped-active {
  color: var(--ev-color-primary);
  border-color: var(--ev-color-primary);
  background-color: var(--ev-color-primary-subtle);
}

.ev-calib-bench__metric-select {
  width: 152px;
}

.ev-calib-bench__guard-wrap {
  display: inline-flex;
}

.ev-calib-bench__body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ev-calib-row {
  display: grid;
  grid-template-columns: minmax(280px, 1.4fr) minmax(240px, 1.3fr) 128px;
  align-items: center;
  gap: var(--ev-space-md);
  padding: 8px 10px;
  border-radius: var(--ev-radius-sm);
  cursor: pointer;
  transition: background-color 150ms var(--ev-ease-out);
}

.ev-calib-row:hover {
  background-color: var(--ev-bg-card);
}

.ev-calib-row--suite {
  cursor: default;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--ev-border-subtle);
}

.ev-calib-row--selected {
  background-color: var(--ev-color-primary-subtle);
}

.ev-calib-row__meta {
  display: flex;
  align-items: center;
  gap: var(--ev-space-sm);
  flex-wrap: wrap;
}

.ev-calib-row__name {
  font-size: var(--ev-fs-body);
  font-weight: 600;
  color: var(--ev-text-primary);
}

.ev-calib-row__code {
  font-size: var(--ev-fs-label);
  color: var(--ev-text-secondary);
}

.ev-calib-row__policy {
  font-size: var(--ev-fs-label);
  color: var(--ev-text-tertiary);
}

.ev-calib-row__blocker,
.ev-calib-row__weight {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--ev-fs-label);
  color: var(--ev-text-secondary);
}

.ev-calib-row__weight-num {
  width: 84px;
}

.ev-calib-row__controls {
  display: flex;
  align-items: center;
  gap: var(--ev-space-sm);
}

.ev-range-input {
  flex: 1;
  min-width: 120px;
  accent-color: var(--ev-color-primary);
  cursor: pointer;
}

.ev-calib-row__num {
  width: 102px;
}

.ev-calib-row__stat {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}

.ev-calib-row__stat-label {
  font-size: var(--ev-fs-label);
  color: var(--ev-text-tertiary);
}

.ev-calib-row__stat-val {
  font-size: var(--ev-fs-body);
  font-weight: 600;
  color: var(--ev-text-primary);
  min-width: 58px;
  text-align: right;
}

@media (max-width: 1023px) {
  .ev-calib-row {
    grid-template-columns: 1fr;
  }

  .ev-calib-row__stat {
    flex-direction: row;
    justify-content: space-between;
    align-items: center;
  }
}
</style>
