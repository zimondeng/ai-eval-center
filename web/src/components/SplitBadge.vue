<script setup lang="ts">
import { computed } from 'vue'
import { Lock } from '@element-plus/icons-vue'

const props = withDefaults(
  defineProps<{
    split?: string | null
    compact?: boolean
  }>(),
  {
    split: 'HOLDOUT',
    compact: false,
  },
)

const normalized = computed(() => (props.split || 'ALL').trim().toUpperCase())

const isHoldout = computed(() => normalized.value === 'HOLDOUT')
const isDev = computed(() => normalized.value === 'DEV')

const roleText = computed(() => {
  if (isDev.value) return '调优集'
  if (isHoldout.value) return '留出集'
  return '全部分集'
})

const variantClass = computed(() => {
  if (isDev.value) return 'ev-split-badge--dev'
  if (isHoldout.value) return 'ev-split-badge--holdout'
  return 'ev-split-badge--all'
})
</script>

<template>
  <span class="ev-split-badge" :class="variantClass">
    <el-icon v-if="isHoldout" data-testid="holdout-lock-icon" class="ev-split-badge__icon">
      <Lock />
    </el-icon>
    <span class="ev-split-badge__code ev-mono">{{ normalized }}</span>
    <span v-if="!compact" class="ev-split-badge__role">{{ roleText }}</span>
  </span>
</template>

<style scoped>
.ev-split-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: var(--ev-radius-sm);
  font-size: var(--ev-fs-label);
  font-weight: 600;
  line-height: 1.4;
  border: 1px solid transparent;
  white-space: nowrap;
}

.ev-split-badge__icon {
  font-size: 12px;
}

.ev-split-badge__code {
  font-size: var(--ev-fs-label);
  font-weight: 600;
}

.ev-split-badge__role {
  font-weight: 500;
}

.ev-split-badge--dev {
  color: var(--ev-color-split-dev);
  background-color: var(--ev-color-split-dev-subtle);
  border-color: var(--ev-color-split-dev-border);
}

.ev-split-badge--holdout {
  color: var(--ev-color-split-holdout);
  background-color: var(--ev-color-split-holdout-subtle);
  border-color: var(--ev-color-split-holdout-border);
}

.ev-split-badge--all {
  color: var(--ev-color-neutral);
  background-color: var(--ev-color-neutral-subtle);
  border-color: var(--ev-color-neutral-border);
}
</style>
