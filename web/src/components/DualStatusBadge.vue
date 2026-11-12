<script lang="ts">
import type { SemanticRegressionTag } from '@/api/types'

export type DualBadgeTone = 'success' | 'info' | 'warning' | 'danger' | 'neutral'
export type DualBadgeSymbol = '✓' | '~' | '✕' | '!' | '↑' | '↓'

export interface RegressionTagMeta {
  tag: SemanticRegressionTag
  symbol: DualBadgeSymbol
  zhLabel: string
  labelWithCode: string
  fullText: string
  tone: DualBadgeTone
}

export const REGRESSION_TAG_META: Record<SemanticRegressionTag, RegressionTagMeta> = {
  SAME: {
    tag: 'SAME',
    symbol: '✓',
    zhLabel: '实质一致',
    labelWithCode: '实质一致 (SAME)',
    fullText: '✓ 实质一致 (SAME)',
    tone: 'success',
  },
  SPECIFICITY_DIFFERS: {
    tag: 'SPECIFICITY_DIFFERS',
    symbol: '~',
    zhLabel: '详略差异',
    labelWithCode: '详略差异 (SPECIFICITY_DIFFERS)',
    fullText: '~ 详略差异 (SPECIFICITY_DIFFERS)',
    tone: 'info',
  },
  ACTION_DIFFERS: {
    tag: 'ACTION_DIFFERS',
    symbol: '!',
    zhLabel: '指引变更',
    labelWithCode: '指引变更 (ACTION_DIFFERS)',
    fullText: '! 指引变更 (ACTION_DIFFERS)',
    tone: 'warning',
  },
  FACT_DIFFERS: {
    tag: 'FACT_DIFFERS',
    symbol: '✕',
    zhLabel: '事实退化',
    labelWithCode: '事实退化 (FACT_DIFFERS)',
    fullText: '✕ 事实退化 (FACT_DIFFERS)',
    tone: 'danger',
  },
}

export const REGRESSION_TAG_OPTIONS: RegressionTagMeta[] = [
  REGRESSION_TAG_META.SAME,
  REGRESSION_TAG_META.SPECIFICITY_DIFFERS,
  REGRESSION_TAG_META.ACTION_DIFFERS,
  REGRESSION_TAG_META.FACT_DIFFERS,
]

export function formatRegressionTag(tag: SemanticRegressionTag | string | null | undefined): string {
  if (!tag) return '—'
  const meta = REGRESSION_TAG_META[tag as SemanticRegressionTag]
  return meta ? meta.fullText : String(tag)
}
</script>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    tone?: DualBadgeTone
    symbol?: DualBadgeSymbol
    label?: string
    value?: string | number | null
    regressionTag?: SemanticRegressionTag | null
  }>(),
  {
    tone: undefined,
    symbol: undefined,
    label: '',
    value: undefined,
    regressionTag: undefined,
  },
)

const regressionMeta = computed<RegressionTagMeta | null>(() => {
  if (!props.regressionTag) return null
  return REGRESSION_TAG_META[props.regressionTag] ?? null
})

const resolvedTone = computed<DualBadgeTone>(() => {
  if (props.tone) return props.tone
  if (regressionMeta.value) return regressionMeta.value.tone
  return 'neutral'
})

const resolvedSymbol = computed<DualBadgeSymbol>(() => {
  if (props.symbol) return props.symbol
  if (regressionMeta.value) return regressionMeta.value.symbol
  if (resolvedTone.value === 'success') return '✓'
  if (resolvedTone.value === 'info') return '~'
  if (resolvedTone.value === 'danger') return '✕'
  return '!'
})

const resolvedLabel = computed<string>(() => {
  if (props.label) return props.label
  if (regressionMeta.value) return regressionMeta.value.labelWithCode
  return ''
})

const hasValue = computed(
  () => props.value !== undefined && props.value !== null && String(props.value).length > 0,
)
</script>

<template>
  <span class="ev-dual-badge" :class="`ev-dual-badge--${resolvedTone}`">
    <span data-testid="dual-symbol" class="ev-dual-badge__symbol" aria-hidden="true">{{
      resolvedSymbol
    }}</span>
    <span data-testid="dual-label" class="ev-dual-badge__label">{{ resolvedLabel }}</span>
    <span v-if="hasValue" data-testid="dual-value" class="ev-dual-badge__value ev-mono">{{
      value
    }}</span>
  </span>
</template>

<style scoped>
.ev-dual-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  border-radius: var(--ev-radius-sm);
  font-size: var(--ev-fs-label);
  font-weight: 500;
  line-height: 1.4;
  border: 1px solid transparent;
  white-space: nowrap;
}

.ev-dual-badge__symbol {
  font-weight: 700;
}

.ev-dual-badge__value {
  font-size: var(--ev-fs-label);
  padding-left: 2px;
}

.ev-dual-badge--success {
  color: var(--ev-color-success);
  background-color: var(--ev-color-success-subtle);
  border-color: var(--ev-color-success-border);
}

.ev-dual-badge--info {
  color: var(--ev-color-split-dev);
  background-color: var(--ev-color-split-dev-subtle);
  border-color: var(--ev-color-split-dev-border);
}

.ev-dual-badge--warning {
  color: var(--ev-color-warning);
  background-color: var(--ev-color-warning-subtle);
  border-color: var(--ev-color-warning-border);
}

.ev-dual-badge--danger {
  color: var(--ev-color-danger);
  background-color: var(--ev-color-danger-subtle);
  border-color: var(--ev-color-danger-border);
}

.ev-dual-badge--neutral {
  color: var(--ev-color-neutral);
  background-color: var(--ev-color-neutral-subtle);
  border-color: var(--ev-color-neutral-border);
}
</style>
