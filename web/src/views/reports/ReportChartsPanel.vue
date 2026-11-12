<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import * as echarts from 'echarts'
import { prefersReducedMotion, resolveChartPalette } from '@/styles/chartPalette'
import { formatRegressionTag, REGRESSION_TAG_META } from '@/components/DualStatusBadge.vue'
import type {
  CompareRunsResult,
  EvalRunDetail,
  RegressionSummary,
  SemanticRegressionTag,
} from '@/api/types'
import type { EditableDimensionThreshold } from '@/stores/reportStore'

const props = defineProps<{
  run: EvalRunDetail
  compareReport: CompareRunsResult | null
  dimensionControls: EditableDimensionThreshold[]
  activeDimensionCode: string
  selectedRegressionTag: SemanticRegressionTag | null
}>()

const emit = defineEmits<{
  (e: 'update:activeDimensionCode', code: string): void
  (e: 'select-regression-tag', tag: SemanticRegressionTag | null): void
}>()

const histDomRef = ref<HTMLElement | null>(null)
const deltaDomRef = ref<HTMLElement | null>(null)
const regDomRef = ref<HTMLElement | null>(null)

// DESIGN.md §13.3: Hold ECharts instances in shallowRef — never deep ref!
const histChart = shallowRef<echarts.ECharts | null>(null)
const deltaChart = shallowRef<echarts.ECharts | null>(null)
const regChart = shallowRef<echarts.ECharts | null>(null)

let resizeObs: ResizeObserver | null = null

const isCompleted = computed(() => props.run.status === 'COMPLETED')

const contextSubtitle = computed(
  () =>
    `${props.run.dataset_split_filter} · n = ${props.run.completed_cases} · 锁定模型: ${props.run.pinned_model_name}`,
)

const activeDimSummary = computed(() => {
  const map = props.run.dimension_summary_json ?? {}
  if (props.activeDimensionCode && map[props.activeDimensionCode]) {
    return map[props.activeDimensionCode]
  }
  const firstKey = Object.keys(map)[0]
  return firstKey ? map[firstKey] : null
})

const activeDimThreshold = computed(() => {
  const code = activeDimSummary.value?.dimension_code
  if (!code) return props.run.suite_pass_threshold ?? 0.75
  const ctrl = props.dimensionControls.find((d) => d.dimension_code === code)
  return ctrl ? ctrl.threshold : (activeDimSummary.value?.threshold ?? 0.75)
})

const deltaRows = computed(() => {
  if (props.compareReport?.dimension_deltas?.length) {
    return props.compareReport.dimension_deltas.filter(
      (d) => d.delta_avg_score !== null && d.delta_avg_score !== undefined,
    )
  }
  const map = props.run.dimension_summary_json ?? {}
  return Object.values(map)
    .filter((d) => d.delta_avg_score !== null && d.delta_avg_score !== undefined)
    .map((d) => ({
      dimension_code: d.dimension_code,
      dimension_name: d.dimension_name,
      delta_avg_score: d.delta_avg_score,
      current_avg_score: d.avg_score,
      baseline_avg_score: d.baseline_avg_score,
    }))
})

const effectiveRegression = computed<RegressionSummary | null>(() => {
  const reg = props.compareReport?.regression_summary ?? props.run.regression_summary_json
  if (!reg || !reg.total_compared || reg.total_compared <= 0) return null
  return reg
})

interface RegressionMetaItem {
  tag: SemanticRegressionTag
  label: string
  count: number
  percent: string
  toneClass: string
}

const regressionItems = computed<RegressionMetaItem[]>(() => {
  const reg = effectiveRegression.value
  if (!reg) return []
  const total = Math.max(1, reg.total_compared)
  const defs: Array<{ tag: SemanticRegressionTag; label: string; toneClass: string }> = [
    { tag: 'SAME', label: REGRESSION_TAG_META.SAME.fullText, toneClass: 'ev-reg-pill--same' },
    {
      tag: 'SPECIFICITY_DIFFERS',
      label: REGRESSION_TAG_META.SPECIFICITY_DIFFERS.fullText,
      toneClass: 'ev-reg-pill--spec',
    },
    {
      tag: 'ACTION_DIFFERS',
      label: REGRESSION_TAG_META.ACTION_DIFFERS.fullText,
      toneClass: 'ev-reg-pill--action',
    },
    {
      tag: 'FACT_DIFFERS',
      label: REGRESSION_TAG_META.FACT_DIFFERS.fullText,
      toneClass: 'ev-reg-pill--fact',
    },
  ]
  return defs.map((item) => {
    const count = Number(reg[item.tag] ?? 0)
    return {
      ...item,
      count,
      percent: `${((count / total) * 100).toFixed(1)}%`,
    }
  })
})

function renderHistogram(): void {
  if (!isCompleted.value || !activeDimSummary.value || !histDomRef.value) {
    return
  }
  if (!histChart.value) {
    histChart.value = echarts.init(histDomRef.value)
  }
  const palette = resolveChartPalette()
  const reduced = prefersReducedMotion()
  const counts = activeDimSummary.value.histogram_counts ?? []
  const threshold = Number(activeDimThreshold.value)

  const barData = Array.from({ length: 10 }, (_, idx) => {
    const center = Number((idx * 0.1 + 0.05).toFixed(2))
    const val = Number(counts[idx] ?? 0)
    const binUpper = (idx + 1) * 0.1
    const isBelow = binUpper <= threshold + 1e-6
    return {
      value: [center, val],
      itemStyle: {
        color: isBelow ? palette.dangerSolid : palette.primary,
        opacity: isBelow ? 0.82 : 0.9,
        borderRadius: [3, 3, 0, 0],
      },
    }
  })

  histChart.value.setOption(
    {
      animation: !reduced,
      animationDuration: 200,
      animationDurationUpdate: 150,
      grid: { left: 44, right: 20, top: 28, bottom: 34 },
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const arr = Array.isArray(params) ? params : [params]
          const first = arr[0] as { data?: { value?: [number, number] } } | undefined
          const pair = first?.data?.value
          if (!pair) return ''
          const low = Math.max(0, pair[0] - 0.05).toFixed(1)
          const high = Math.min(1, pair[0] + 0.05).toFixed(1)
          return `区间 [${low}, ${high}] 期望分<br/>用例数: <b>${pair[1]} 条</b>`
        },
      },
      xAxis: {
        type: 'value',
        min: 0,
        max: 1,
        interval: 0.2,
        name: '[0, 1] 期望分',
        nameLocation: 'middle',
        nameGap: 22,
        axisLabel: { color: palette.textSecondary },
        splitLine: { lineStyle: { color: palette.borderSubtle, type: 'dashed' } },
      },
      yAxis: {
        type: 'value',
        minInterval: 1,
        name: '用例数 (条)',
        axisLabel: { color: palette.textSecondary },
        splitLine: { lineStyle: { color: palette.borderSubtle } },
      },
      series: [
        {
          name: '连续期望分分布',
          type: 'bar',
          barWidth: '72%',
          data: barData,
          markArea: {
            silent: true,
            itemStyle: {
              color: palette.dangerSubtle,
              opacity: 0.72,
            },
            data: [[{ xAxis: 0 }, { xAxis: threshold }]],
          },
          markLine: {
            symbol: ['none', 'none'],
            silent: true,
            lineStyle: {
              color: palette.dangerSolid,
              width: 2,
              type: 'dashed',
            },
            label: {
              formatter: `阈值 ${threshold.toFixed(2)}`,
              color: palette.dangerSolid,
              fontWeight: 600,
            },
            data: [{ xAxis: threshold }],
          },
        },
      ],
    },
    { lazyUpdate: true },
  )
}

function renderDeltaChart(): void {
  if (!isCompleted.value || deltaRows.value.length === 0 || !deltaDomRef.value) {
    return
  }
  if (!deltaChart.value) {
    deltaChart.value = echarts.init(deltaDomRef.value)
  }
  const palette = resolveChartPalette()
  const reduced = prefersReducedMotion()
  const categories = deltaRows.value.map((d) => d.dimension_name)
  const values = deltaRows.value.map((d) => {
    const val = Number(d.delta_avg_score ?? 0)
    return {
      value: Number(val.toFixed(4)),
      itemStyle: {
        color: val >= 0 ? palette.successSolid : palette.dangerSolid,
        borderRadius: val >= 0 ? [0, 3, 3, 0] : [3, 0, 0, 3],
      },
    }
  })

  deltaChart.value.setOption(
    {
      animation: !reduced,
      animationDuration: 200,
      animationDurationUpdate: 150,
      grid: { left: 112, right: 28, top: 20, bottom: 32 },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: unknown) => {
          const arr = Array.isArray(params) ? params : [params]
          const first = arr[0] as { name?: string; value?: number } | undefined
          if (!first) return ''
          const v = Number(first.value ?? 0)
          const sign = v >= 0 ? '+' : ''
          return `${first.name}<br/>期望分变化: <b>${sign}${v.toFixed(3)}</b>`
        },
      },
      xAxis: {
        type: 'value',
        name: '期望分 Delta',
        nameLocation: 'middle',
        nameGap: 20,
        axisLabel: {
          color: palette.textSecondary,
          formatter: (val: number) => (val > 0 ? `+${val.toFixed(2)}` : val.toFixed(2)),
        },
        splitLine: { lineStyle: { color: palette.borderSubtle, type: 'dashed' } },
      },
      yAxis: {
        type: 'category',
        data: categories,
        axisLabel: { color: palette.textPrimary },
        axisLine: { lineStyle: { color: palette.borderSubtle } },
      },
      series: [
        {
          name: '维度得分 Delta',
          type: 'bar',
          barMaxWidth: 22,
          data: values,
          markLine: {
            silent: true,
            symbol: ['none', 'none'],
            lineStyle: { color: palette.textTertiary, width: 1 },
            data: [{ xAxis: 0 }],
          },
        },
      ],
    },
    { lazyUpdate: true },
  )
}

function renderRegressionChart(): void {
  if (!isCompleted.value || !effectiveRegression.value || !regDomRef.value) {
    return
  }
  if (!regChart.value) {
    regChart.value = echarts.init(regDomRef.value)
    regChart.value.on('click', (params: { seriesName?: string }) => {
      const tag = params?.seriesName as SemanticRegressionTag | undefined
      if (!tag) return
      toggleRegressionTag(tag)
    })
  }
  const palette = resolveChartPalette()
  const reduced = prefersReducedMotion()
  const reg = effectiveRegression.value
  const total = Math.max(1, reg.total_compared)

  const tagSpecs: Array<{
    tag: SemanticRegressionTag
    label: string
    color: string
  }> = [
    { tag: 'SAME', label: REGRESSION_TAG_META.SAME.fullText, color: palette.successSolid },
    {
      tag: 'SPECIFICITY_DIFFERS',
      label: REGRESSION_TAG_META.SPECIFICITY_DIFFERS.fullText,
      color: palette.splitDev,
    },
    {
      tag: 'ACTION_DIFFERS',
      label: REGRESSION_TAG_META.ACTION_DIFFERS.fullText,
      color: palette.warningSolid,
    },
    {
      tag: 'FACT_DIFFERS',
      label: REGRESSION_TAG_META.FACT_DIFFERS.fullText,
      color: palette.dangerSolid,
    },
  ]

  regChart.value.setOption(
    {
      animation: !reduced,
      animationDuration: 200,
      animationDurationUpdate: 150,
      grid: { left: 16, right: 20, top: 20, bottom: 32, containLabel: true },
      tooltip: {
        trigger: 'item',
        formatter: (p: { seriesName?: string; value?: number }) => {
          const code = p.seriesName ?? ''
          const pct = Number(p.value ?? 0)
          const found = tagSpecs.find((t) => t.tag === code)
          const rawCount = reg[code as SemanticRegressionTag] ?? 0
          return `${found?.label ?? formatRegressionTag(code)}<br/>占比: <b>${pct.toFixed(1)}%</b> (${rawCount} 条)`
        },
      },
      xAxis: {
        type: 'value',
        min: 0,
        max: 100,
        axisLabel: {
          color: palette.textSecondary,
          formatter: '{value}%',
        },
        splitLine: { lineStyle: { color: palette.borderSubtle, type: 'dashed' } },
      },
      yAxis: {
        type: 'category',
        data: ['语义回归占比'],
        axisLabel: { color: palette.textSecondary },
      },
      series: tagSpecs.map((spec) => ({
        name: spec.tag,
        type: 'bar',
        stack: 'total',
        barWidth: 28,
        cursor: 'pointer',
        itemStyle: {
          color: spec.color,
          opacity:
            !props.selectedRegressionTag || props.selectedRegressionTag === spec.tag ? 0.95 : 0.35,
        },
        data: [Number((((reg[spec.tag] ?? 0) / total) * 100).toFixed(2))],
      })),
    },
    { lazyUpdate: true },
  )
}

function toggleRegressionTag(tag: SemanticRegressionTag): void {
  const next = props.selectedRegressionTag === tag ? null : tag
  emit('select-regression-tag', next)
}

function refreshAllCharts(): void {
  renderHistogram()
  renderDeltaChart()
  renderRegressionChart()
}

watch(
  () => [
    props.run,
    props.compareReport,
    props.activeDimensionCode,
    activeDimThreshold.value,
    props.selectedRegressionTag,
  ],
  async () => {
    await nextTick()
    refreshAllCharts()
  },
  { deep: false },
)

onMounted(async () => {
  await nextTick()
  refreshAllCharts()
  if (typeof ResizeObserver !== 'undefined') {
    resizeObs = new ResizeObserver(() => {
      histChart.value?.resize()
      deltaChart.value?.resize()
      regChart.value?.resize()
    })
    if (histDomRef.value) resizeObs.observe(histDomRef.value)
    if (deltaDomRef.value) resizeObs.observe(deltaDomRef.value)
    if (regDomRef.value) resizeObs.observe(regDomRef.value)
  }
})

onBeforeUnmount(() => {
  resizeObs?.disconnect()
  histChart.value?.dispose()
  deltaChart.value?.dispose()
  regChart.value?.dispose()
  histChart.value = null
  deltaChart.value = null
  regChart.value = null
})
</script>

<template>
  <section class="ev-charts-grid" aria-label="分数分布与语义回归可视化">
    <!-- Chart 1: Continuous Score Histogram -->
    <article class="ev-surface ev-chart-card">
      <header class="ev-chart-card__header">
        <div>
          <h3 class="ev-section-title">连续期望分分布与门禁切点</h3>
          <p class="ev-chart-card__sub ev-mono">{{ contextSubtitle }}</p>
        </div>
        <el-select
          v-if="dimensionControls.length > 0"
          :model-value="activeDimSummary?.dimension_code ?? activeDimensionCode"
          size="small"
          class="ev-chart-card__select"
          aria-label="选择展示分布的评价维度"
          @update:model-value="(v: string) => emit('update:activeDimensionCode', v)"
        >
          <el-option
            v-for="d in dimensionControls"
            :key="d.dimension_code"
            :label="`${d.dimension_name} (${d.dimension_code})`"
            :value="d.dimension_code"
          />
        </el-select>
      </header>

      <div v-if="!isCompleted" class="ev-chart-empty">
        批次执行中，待全部原子题聚合后生成连续期望分分布图
      </div>
      <div v-else-if="!activeDimSummary" class="ev-chart-empty">
        当前批次暂无维度分数直方图汇总数据
      </div>
      <div v-else ref="histDomRef" class="ev-chart-canvas" />
    </article>

    <!-- Chart 2: Dual-Run Dimension Score Delta -->
    <article class="ev-surface ev-chart-card">
      <header class="ev-chart-card__header">
        <div>
          <h3 class="ev-section-title">双批次维度期望分涨跌 (Delta)</h3>
          <p class="ev-chart-card__sub ev-mono">{{ contextSubtitle }}</p>
        </div>
      </header>

      <div v-if="!isCompleted" class="ev-chart-empty">
        批次执行中，待全部原子题聚合后生成基线涨跌对比图
      </div>
      <div v-else-if="deltaRows.length === 0" class="ev-chart-empty">
        未选择对比基线批次。可在报告顶部选择基线批次查看各维度涨跌幅度。
      </div>
      <div v-else ref="deltaDomRef" class="ev-chart-canvas" />
    </article>

    <!-- Chart 3: Interactive 4-Way Semantic Regression Stacked Bar -->
    <article class="ev-surface ev-chart-card ev-chart-card--wide">
      <header class="ev-chart-card__header">
        <div>
          <h3 class="ev-section-title">四分类语义回归分布（点击分块或标签联动过滤下方明细）</h3>
          <p class="ev-chart-card__sub ev-mono">
            {{ contextSubtitle }}
            <template v-if="effectiveRegression">
              · 已比对样本 {{ effectiveRegression.total_compared }} 条
            </template>
          </p>
        </div>
        <el-button
          v-if="selectedRegressionTag"
          size="small"
          @click="emit('select-regression-tag', null)"
        >
          清除回归过滤 ({{ formatRegressionTag(selectedRegressionTag) }})
        </el-button>
      </header>

      <div v-if="!isCompleted" class="ev-chart-empty">
        批次执行中，待全部原子题聚合后生成四分类语义回归分布
      </div>
      <div v-else-if="!effectiveRegression" class="ev-chart-empty">
        当前未关联基线批次或未启用双批次语义回归判别
      </div>
      <template v-else>
        <div ref="regDomRef" class="ev-chart-canvas ev-chart-canvas--compact" />
        <div class="ev-reg-pills" role="group" aria-label="四分类语义回归过滤标签">
          <button
            v-for="item in regressionItems"
            :key="item.tag"
            type="button"
            class="ev-reg-pill"
            :class="[
              item.toneClass,
              { 'ev-reg-pill--active': selectedRegressionTag === item.tag },
            ]"
            @click="toggleRegressionTag(item.tag)"
          >
            <span class="ev-reg-pill__label">{{ item.label }}</span>
            <span class="ev-mono">{{ item.count }} 条 ({{ item.percent }})</span>
          </button>
        </div>
      </template>
    </article>
  </section>
</template>

<style scoped>
.ev-charts-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--ev-space-lg);
}

.ev-chart-card {
  display: flex;
  flex-direction: column;
  gap: var(--ev-space-sm);
}

.ev-chart-card--wide {
  grid-column: 1 / -1;
}

.ev-chart-card__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--ev-space-sm);
  flex-wrap: wrap;
}

.ev-chart-card__sub {
  margin: 2px 0 0;
  font-size: var(--ev-fs-label);
  color: var(--ev-text-tertiary);
}

.ev-chart-card__select {
  width: 220px;
}

.ev-chart-canvas {
  width: 100%;
  height: 240px;
}

.ev-chart-canvas--compact {
  height: 118px;
}

.ev-chart-empty {
  min-height: 180px;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 20px;
  font-size: var(--ev-fs-body);
  color: var(--ev-text-tertiary);
  background-color: var(--ev-bg-panel);
  border-radius: var(--ev-radius-md);
}

.ev-reg-pills {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ev-space-sm);
  padding-top: 4px;
}

.ev-reg-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: var(--ev-radius-md);
  border: 1px solid var(--ev-border-subtle);
  background-color: var(--ev-bg-panel);
  color: var(--ev-text-primary);
  font-family: var(--ev-font-sans);
  font-size: var(--ev-fs-label);
  cursor: pointer;
  transition: border-color 150ms var(--ev-ease-out), background-color 150ms var(--ev-ease-out);
}

.ev-reg-pill__label {
  font-weight: 600;
}

.ev-reg-pill--same {
  border-left: 3px solid var(--ev-color-success-solid);
}

.ev-reg-pill--spec {
  border-left: 3px solid var(--ev-color-split-dev);
}

.ev-reg-pill--action {
  border-left: 3px solid var(--ev-color-warning-solid);
}

.ev-reg-pill--fact {
  border-left: 3px solid var(--ev-color-danger-solid);
}

.ev-reg-pill--active {
  border-color: var(--ev-color-primary);
  background-color: var(--ev-color-primary-subtle);
  color: var(--ev-color-primary);
}

@media (max-width: 1023px) {
  .ev-charts-grid {
    grid-template-columns: 1fr;
  }
}
</style>
