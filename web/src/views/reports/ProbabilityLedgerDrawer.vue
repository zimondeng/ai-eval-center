<script setup lang="ts">
import { computed } from 'vue'
import { ArrowLeft, ArrowRight, WarningFilled } from '@element-plus/icons-vue'
import { formatScore } from '@/api/http'
import SplitBadge from '@/components/SplitBadge.vue'
import DualStatusBadge from '@/components/DualStatusBadge.vue'
import type { AtomicAnswerLedgerItem, EvalRunCaseItem } from '@/api/types'

const props = defineProps<{
  modelValue: boolean
  caseItem: EvalRunCaseItem | null
  hasPrev: boolean
  hasNext: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', open: boolean): void
  (e: 'prev'): void
  (e: 'next'): void
}>()

const WAF_FALLBACK_COPY =
  '上游网关触发代码块拦截（HTTP 403），内核已自动剥离围栏代码块完成重试判别。'

const lowConfidenceMessage = computed(() => {
  const val =
    props.caseItem?.min_confidence !== null && props.caseItem?.min_confidence !== undefined
      ? props.caseItem.min_confidence.toFixed(3)
      : '0.000'
  return `该用例存在原子题判别把握度低于复核线（最低 confidence = ${val}），建议人工核对实际输出与标准答案。`
})

function resolveProbabilityEntries(q: AtomicAnswerLedgerItem): Array<{ key: string; prob: number }> {
  if (q.probabilities && typeof q.probabilities === 'object') {
    const entries = Object.entries(q.probabilities).map(([k, v]) => ({
      key: k,
      prob: Math.max(0, Math.min(1, Number(v ?? 0))),
    }))
    if (entries.length > 0) return entries
  }
  if (typeof q.p_true === 'number' && !Number.isNaN(q.p_true)) {
    const pTrue = Math.max(0, Math.min(1, q.p_true))
    return [
      { key: 'true', prob: pTrue },
      { key: 'false', prob: Number((1 - pTrue).toFixed(4)) },
    ]
  }
  return []
}
</script>

<template>
  <el-drawer
    :model-value="modelValue"
    size="680px"
    direction="rtl"
    destroy-on-close
    aria-label="单条用例原子概率账本抽屉"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <template #header>
      <div v-if="caseItem" class="ev-ledger-head">
        <div class="ev-ledger-head__left">
          <span class="ev-ledger-head__code ev-mono">{{ caseItem.case_code }}</span>
          <SplitBadge :split="caseItem.dataset_split" compact />
          <DualStatusBadge
            :tone="caseItem.overall_passed ? 'success' : 'danger'"
            :label="caseItem.overall_passed ? '门禁通过' : '门禁拦截'"
            :value="formatScore(caseItem.overall_score, 3)"
          />
          <DualStatusBadge
            v-if="caseItem.regression_tag"
            :regression-tag="caseItem.regression_tag"
          />
        </div>

        <div class="ev-ledger-head__nav">
          <el-tooltip content="上一条用例" placement="bottom">
            <el-button
              size="small"
              class="ev-icon-btn"
              aria-label="上一条用例"
              :disabled="!hasPrev"
              @click="emit('prev')"
            >
              <el-icon><ArrowLeft /></el-icon>
            </el-button>
          </el-tooltip>
          <el-tooltip content="下一条用例" placement="bottom">
            <el-button
              size="small"
              class="ev-icon-btn"
              aria-label="下一条用例"
              :disabled="!hasNext"
              @click="emit('next')"
            >
              <el-icon><ArrowRight /></el-icon>
            </el-button>
          </el-tooltip>
        </div>
      </div>
    </template>

    <div v-if="caseItem" class="ev-ledger-body">
      <!-- Low confidence human review alert -->
      <div v-if="caseItem.needs_human_review" class="ev-ledger-warn" role="status">
        <el-icon class="ev-ledger-warn__icon"><WarningFilled /></el-icon>
        <span>{{ lowConfidenceMessage }}</span>
      </div>

      <!-- Execution error detail if SUT/Judge failed -->
      <div v-if="caseItem.error_detail" class="ev-ledger-err" role="alert">
        <span class="ev-mono">[{{ caseItem.exec_status }}]</span>
        <span>{{ caseItem.error_detail }}</span>
      </div>

      <!-- Section 1: Input & Output Diff -->
      <section class="ev-panel ev-ledger-section">
        <h3 class="ev-section-title">用户输入与被测输出对照</h3>
        <div class="ev-ledger-io-block">
          <span class="ev-ledger-label">用户输入 (input_text)</span>
          <div class="ev-prose ev-ledger-text">{{ caseItem.input_text }}</div>
        </div>

        <div class="ev-ledger-diff-grid">
          <div class="ev-ledger-io-block">
            <span class="ev-ledger-label">被测实际输出 (actual_output)</span>
            <div class="ev-prose ev-ledger-text">
              {{ caseItem.actual_output || '（无实际文本输出）' }}
            </div>
          </div>

          <div class="ev-ledger-io-block">
            <span class="ev-ledger-label">标准答案 / 期望输出 (expected_output)</span>
            <div class="ev-prose ev-ledger-text">
              {{ caseItem.expected_output || '（未配置参考答案）' }}
            </div>
          </div>
        </div>

        <div v-if="caseItem.baseline_output" class="ev-ledger-io-block">
          <span class="ev-ledger-label">基线批次输出 (baseline_output)</span>
          <div class="ev-prose ev-ledger-text">{{ caseItem.baseline_output }}</div>
        </div>
      </section>

      <!-- Section 2: Per-Dimension Probability Ledgers -->
      <section class="ev-ledger-list-wrap">
        <h3 class="ev-section-title">分维度原子题概率账本 ({{ caseItem.ledgers.length }})</h3>

        <div
          v-for="ledger in caseItem.ledgers"
          :key="ledger.id || ledger.dimension_code"
          class="ev-panel ev-dim-ledger"
        >
          <div class="ev-dim-ledger__head">
            <div class="ev-dim-ledger__title">
              <span class="ev-mono ev-dim-ledger__code">
                {{ ledger.dimension_code }} · v{{ ledger.dimension_version }}
              </span>
              <span class="ev-dim-ledger__kind ev-mono">{{ ledger.evaluator_kind }}</span>
              <span v-if="ledger.is_blocker" class="ev-dim-ledger__blocker">一票否决</span>
            </div>

            <div class="ev-dim-ledger__score">
              <span class="ev-dim-ledger__threshold ev-mono">
                阈值 {{ formatScore(ledger.applied_threshold, 2) }} · 权重
                {{ formatScore(ledger.applied_weight, 1) }}
              </span>
              <DualStatusBadge
                :tone="ledger.passed ? 'success' : 'danger'"
                :label="ledger.passed ? '达标' : '未达标'"
                :value="formatScore(ledger.raw_dimension_score, 3)"
              />
            </div>
          </div>

          <div v-if="ledger.waf_fallback_used" class="ev-ledger-waf-note" role="status">
            <el-icon><WarningFilled /></el-icon>
            <span>{{ WAF_FALLBACK_COPY }}</span>
          </div>

          <div v-if="ledger.deterministic_reason" class="ev-dim-ledger__det ev-mono">
            确定性规则断言: {{ ledger.deterministic_reason }}
          </div>

          <!-- Atomic Questions -->
          <div
            v-if="ledger.raw_answers_json?.questions?.length"
            class="ev-q-list"
          >
            <div
              v-for="q in ledger.raw_answers_json.questions"
              :key="q.key"
              class="ev-q-item"
              :class="{ 'ev-q-item--na': q.applicable === false }"
            >
              <div class="ev-q-item__top">
                <div class="ev-q-item__id">
                  <span class="ev-mono ev-q-item__key">{{ q.key }}</span>
                  <span class="ev-mono ev-q-item__type">{{ q.type }}</span>
                  <span class="ev-mono ev-q-item__weight">w={{ q.weight }}</span>
                </div>

                <div class="ev-q-item__metrics">
                  <span v-if="q.applicable === false" class="ev-q-na-badge">
                    ! 不适用 · 分母剔除 (applicable = false)
                  </span>
                  <template v-else>
                    <span class="ev-mono ev-q-item__conf">
                      把握度 {{ formatScore(q.confidence, 3) }}
                    </span>
                    <span class="ev-mono ev-q-item__norm">
                      期望分 {{ formatScore(q.normalized_score, 3) }}
                    </span>
                  </template>
                </div>
              </div>

              <div v-if="resolveProbabilityEntries(q).length > 0" class="ev-prob-bars">
                <div
                  v-for="entry in resolveProbabilityEntries(q)"
                  :key="entry.key"
                  class="ev-prob-row"
                >
                  <span class="ev-prob-row__opt ev-mono">{{ entry.key }}</span>
                  <div class="ev-prob-row__track">
                    <div
                      class="ev-prob-row__fill"
                      :class="{ 'ev-prob-row__fill--null': entry.key === 'null' }"
                      :style="{ width: `${(entry.prob * 100).toFixed(1)}%` }"
                    />
                  </div>
                  <span class="ev-prob-row__val ev-mono">
                    {{ (entry.prob * 100).toFixed(1) }}%
                  </span>
                </div>
              </div>
            </div>
          </div>

          <div class="ev-dim-ledger__foot ev-mono">
            <span>判别耗时: {{ ledger.eval_latency_ms ?? '--' }} ms</span>
            <span>输入 Tokens: {{ ledger.input_tokens ?? '--' }}</span>
            <span v-if="ledger.upstream_request_id">
              upstream_id: {{ ledger.upstream_request_id }}
            </span>
          </div>
        </div>
      </section>

      <!-- Section 3: Bottom SUT Telemetry -->
      <footer class="ev-panel ev-ledger-telemetry ev-mono">
        <span>SUT 响应耗时: {{ caseItem.sut_latency_ms ?? '--' }} ms</span>
        <span>首字耗时 (TTFT): {{ caseItem.ttft_ms ?? '--' }} ms</span>
        <span>最低把握度: {{ formatScore(caseItem.min_confidence, 3) }}</span>
        <span>执行状态: {{ caseItem.exec_status }}</span>
      </footer>
    </div>
  </el-drawer>
</template>

<style scoped>
.ev-ledger-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ev-space-sm);
  width: 100%;
  padding-right: 8px;
}

.ev-ledger-head__left {
  display: flex;
  align-items: center;
  gap: var(--ev-space-sm);
  flex-wrap: wrap;
}

.ev-ledger-head__code {
  font-size: var(--ev-fs-title);
  font-weight: 600;
  color: var(--ev-text-primary);
}

.ev-ledger-head__nav {
  display: flex;
  align-items: center;
  gap: 6px;
}

.ev-ledger-body {
  display: flex;
  flex-direction: column;
  gap: var(--ev-space-md);
}

.ev-ledger-warn {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  border-radius: var(--ev-radius-md);
  background-color: var(--ev-color-warning-subtle);
  border: 1px solid var(--ev-color-warning-border);
  color: var(--ev-color-warning);
  font-size: var(--ev-fs-body);
}

.ev-ledger-warn__icon {
  margin-top: 2px;
  flex-shrink: 0;
}

.ev-ledger-err {
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  border-radius: var(--ev-radius-md);
  background-color: var(--ev-color-danger-subtle);
  border: 1px solid var(--ev-color-danger-border);
  color: var(--ev-color-danger);
  font-size: var(--ev-fs-body);
}

.ev-ledger-section {
  display: flex;
  flex-direction: column;
  gap: var(--ev-space-sm);
}

.ev-ledger-io-block {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.ev-ledger-label {
  font-size: var(--ev-fs-label);
  font-weight: 600;
  color: var(--ev-text-secondary);
}

.ev-ledger-text {
  padding: 8px 10px;
  border-radius: var(--ev-radius-sm);
  background-color: var(--ev-bg-card);
  font-size: var(--ev-fs-body);
  max-height: 180px;
  overflow-y: auto;
}

.ev-ledger-diff-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--ev-space-sm);
}

.ev-ledger-list-wrap {
  display: flex;
  flex-direction: column;
  gap: var(--ev-space-sm);
}

.ev-dim-ledger {
  display: flex;
  flex-direction: column;
  gap: var(--ev-space-sm);
}

.ev-dim-ledger__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ev-space-sm);
  flex-wrap: wrap;
}

.ev-dim-ledger__title {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.ev-dim-ledger__code {
  font-weight: 600;
  color: var(--ev-text-primary);
}

.ev-dim-ledger__kind {
  font-size: var(--ev-fs-label);
  color: var(--ev-text-tertiary);
}

.ev-dim-ledger__blocker {
  padding: 1px 6px;
  border-radius: var(--ev-radius-sm);
  font-size: var(--ev-fs-label);
  font-weight: 600;
  color: var(--ev-color-danger);
  background-color: var(--ev-color-danger-subtle);
}

.ev-dim-ledger__score {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ev-dim-ledger__threshold {
  font-size: var(--ev-fs-label);
  color: var(--ev-text-secondary);
}

.ev-ledger-waf-note {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: var(--ev-radius-sm);
  background-color: var(--ev-color-warning-subtle);
  color: var(--ev-color-warning);
  font-size: var(--ev-fs-label);
}

.ev-dim-ledger__det {
  padding: 6px 10px;
  border-radius: var(--ev-radius-sm);
  background-color: var(--ev-bg-card);
  color: var(--ev-text-secondary);
  font-size: var(--ev-fs-label);
}

.ev-q-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ev-q-item {
  padding: 8px 10px;
  border-radius: var(--ev-radius-sm);
  background-color: var(--ev-bg-card);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.ev-q-item--na {
  opacity: 0.78;
}

.ev-q-item__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.ev-q-item__id,
.ev-q-item__metrics {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ev-q-item__key {
  font-weight: 600;
  color: var(--ev-text-primary);
}

.ev-q-item__type,
.ev-q-item__weight,
.ev-q-item__conf {
  font-size: var(--ev-fs-label);
  color: var(--ev-text-tertiary);
}

.ev-q-item__norm {
  font-weight: 600;
  color: var(--ev-color-primary);
}

.ev-q-na-badge {
  font-size: var(--ev-fs-label);
  font-weight: 600;
  color: var(--ev-color-neutral);
  background-color: var(--ev-color-neutral-subtle);
  padding: 1px 6px;
  border-radius: var(--ev-radius-sm);
}

.ev-prob-bars {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.ev-prob-row {
  display: grid;
  grid-template-columns: 56px 1fr 56px;
  align-items: center;
  gap: 8px;
}

.ev-prob-row__opt {
  font-size: var(--ev-fs-label);
  color: var(--ev-text-secondary);
}

.ev-prob-row__track {
  height: 7px;
  border-radius: var(--ev-radius-sm);
  background-color: var(--ev-color-neutral-subtle);
  overflow: hidden;
}

.ev-prob-row__fill {
  height: 100%;
  background-color: var(--ev-color-primary);
  border-radius: var(--ev-radius-sm);
  transition: width 150ms var(--ev-ease-out);
}

.ev-prob-row__fill--null {
  background-color: var(--ev-text-tertiary);
}

.ev-prob-row__val {
  font-size: var(--ev-fs-label);
  color: var(--ev-text-primary);
  text-align: right;
}

.ev-dim-ledger__foot {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  font-size: var(--ev-fs-label);
  color: var(--ev-text-tertiary);
  padding-top: 4px;
}

.ev-ledger-telemetry {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 12px;
  font-size: var(--ev-fs-label);
  color: var(--ev-text-secondary);
}

@media (max-width: 767px) {
  .ev-ledger-diff-grid {
    grid-template-columns: 1fr;
  }
}
</style>
