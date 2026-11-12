<script setup lang="ts">
import { computed } from 'vue'

export type DualBadgeTone = 'success' | 'warning' | 'danger' | 'neutral'
export type DualBadgeSymbol = '✓' | '✕' | '!' | '↑' | '↓'

const props = withDefaults(
  defineProps<{
    tone?: DualBadgeTone
    symbol?: DualBadgeSymbol
    label: string
    value?: string | number | null
  }>(),
  {
    tone: 'neutral',
    symbol: undefined,
    value: undefined,
  },
)

const resolvedSymbol = computed<DualBadgeSymbol>(() => {
  if (props.symbol) return props.symbol
  if (props.tone === 'success') return '✓'
  if (props.tone === 'danger') return '✕'
  return '!'
})

const hasValue = computed(
  () => props.value !== undefined && props.value !== null && String(props.value).length > 0,
)
</script>

<template>
  <span class="ev-dual-badge" :class="`ev-dual-badge--${tone}`">
    <span data-testid="dual-symbol" class="ev-dual-badge__symbol" aria-hidden="true">{{
      resolvedSymbol
    }}</span>
    <span data-testid="dual-label" class="ev-dual-badge__label">{{ label }}</span>
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
