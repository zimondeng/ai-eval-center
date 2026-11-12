<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { ArrowLeft, Check, Refresh } from '@element-plus/icons-vue'
import { datasetsApi } from '@/api/endpoints'
import { formatReadableError, formatScore, validateOptionalJsonObject } from '@/api/http'
import type { DatasetSplit, EvalCaseRecord, ReviewStatus } from '@/api/types'
import DualStatusBadge from '@/components/DualStatusBadge.vue'
import EmptyStateBox from '@/components/EmptyStateBox.vue'
import SplitBadge from '@/components/SplitBadge.vue'

const props = defineProps<{
  datasetId: number
  datasetName: string
  evalLevel?: 'E2E' | 'COMPONENT'
}>()

const emit = defineEmits<{
  (e: 'back'): void
  (e: 'updated'): void
}>()

// Exact DESIGN.md §12 copy for pii_masked = 0 curation block
const UNMASKED_GUARD_COPY =
  '当前用例尚未完成脱敏确认（pii_masked = 0），禁止置为「已审核生效」。请先执行脱敏清洗或人工核验无敏感信息。'

const splitFilter = ref<'ALL' | 'DEV' | 'HOLDOUT'>('ALL')
const statusFilter = ref<string>('')
const sortOrder = ref<'uncertainty_desc' | 'id_asc'>('uncertainty_desc')
const keyword = ref('')

const loading = ref(false)
const saving = ref(false)
const cases = ref<EvalCaseRecord[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(100)
const selectedIndex = ref(0)
const curationFormRef = ref<FormInstance>()

const editForm = reactive({
  expected_output: '',
  expected_structured_text: '',
  dataset_split: 'DEV' as DatasetSplit,
  difficulty: 'NORMAL' as 'EASY' | 'NORMAL' | 'HARD' | 'ADVERSARIAL',
  pii_masked: 0,
  review_note: '',
})

const curationRules: FormRules<typeof editForm> = {
  expected_structured_text: [
    { validator: validateOptionalJsonObject, trigger: ['blur', 'change'] },
  ],
  dataset_split: [{ required: true, message: '请选择固定分集归属', trigger: 'change' }],
  difficulty: [{ required: true, message: '请选择样本难度分级', trigger: 'change' }],
}

const splitOptions = [
  { label: 'ALL 全部', value: 'ALL' },
  { label: 'DEV 调优集', value: 'DEV' },
  { label: 'HOLDOUT 留出集', value: 'HOLDOUT' },
]

const activeCase = computed<EvalCaseRecord | null>(() => cases.value[selectedIndex.value] ?? null)

const isApprovalBlocked = computed(() => {
  if (!activeCase.value) return true
  return Number(editForm.pii_masked) === 0
})

const approvalGuardMessage = computed(() => (isApprovalBlocked.value ? UNMASKED_GUARD_COPY : ''))

function syncEditorFromCase(row: EvalCaseRecord | null): void {
  if (!row) {
    editForm.expected_output = ''
    editForm.expected_structured_text = ''
    editForm.dataset_split = 'DEV'
    editForm.difficulty = 'NORMAL'
    editForm.pii_masked = 0
    editForm.review_note = ''
    return
  }
  editForm.expected_output = row.expected_output || ''
  editForm.expected_structured_text = row.expected_structured_json
    ? JSON.stringify(row.expected_structured_json, null, 2)
    : ''
  editForm.dataset_split = row.dataset_split
  editForm.difficulty = row.difficulty
  editForm.pii_masked = Number(row.pii_masked || 0)
  editForm.review_note = row.review_note || ''
}

watch(activeCase, (next) => {
  syncEditorFromCase(next)
  curationFormRef.value?.clearValidate()
})

async function fetchCases(): Promise<void> {
  loading.value = true
  try {
    const res = await datasetsApi.listCases(props.datasetId, {
      split: splitFilter.value === 'ALL' ? undefined : splitFilter.value,
      review_status: statusFilter.value || undefined,
      q: keyword.value.trim() || undefined,
      sort: sortOrder.value,
      page: page.value,
      page_size: pageSize.value,
    })
    cases.value = res.items
    total.value = res.total
    selectedIndex.value = 0
    syncEditorFromCase(res.items[0] ?? null)
  } catch (err) {
    ElMessage.error(formatReadableError(err))
  } finally {
    loading.value = false
  }
}

function onFilterChange(): void {
  page.value = 1
  void fetchCases()
}

function onPageSizeChange(): void {
  page.value = 1
  void fetchCases()
}

function selectCaseIndex(idx: number): void {
  selectedIndex.value = idx
}

async function submitCuration(targetStatus: ReviewStatus): Promise<void> {
  const row = activeCase.value
  if (!row) return

  if (targetStatus === 'APPROVED' && Number(editForm.pii_masked) === 0) {
    return
  }

  if (!curationFormRef.value) return
  const valid = await curationFormRef.value.validate().catch(() => false)
  if (!valid) return

  let structuredObj: Record<string, unknown> | null = null
  if (editForm.expected_structured_text.trim()) {
    structuredObj = JSON.parse(editForm.expected_structured_text) as Record<string, unknown>
  }

  saving.value = true
  try {
    const updated = await datasetsApi.curateCase(row.id, {
      expected_output: editForm.expected_output,
      expected_structured_json: structuredObj,
      dataset_split: editForm.dataset_split,
      difficulty: editForm.difficulty,
      pii_masked: Number(editForm.pii_masked),
      review_status: targetStatus,
      review_note: editForm.review_note.trim() || null,
    })
    cases.value.splice(selectedIndex.value, 1, updated)
    emit('updated')
    if (targetStatus === 'APPROVED') {
      ElMessage.success(`用例 ${row.case_code} 已审核生效`)
      if (selectedIndex.value < cases.value.length - 1) {
        selectedIndex.value += 1
      }
    } else {
      ElMessage.success('修订草稿已保存')
    }
  } catch (err) {
    ElMessage.error(formatReadableError(err))
  } finally {
    saving.value = false
  }
}

function resetFilters(): void {
  splitFilter.value = 'ALL'
  statusFilter.value = ''
  keyword.value = ''
  sortOrder.value = 'uncertainty_desc'
  page.value = 1
  void fetchCases()
}

onMounted(() => {
  void fetchCases()
})
</script>

<template>
  <div class="ev-curation-bench">
    <div class="ev-page-header">
      <div class="ev-bench-title-row">
        <el-button v-if="datasetName" :icon="ArrowLeft" @click="emit('back')">
          返回测试集列表
        </el-button>
        <div>
          <h2 class="ev-page-title">人工真值校准台 · {{ datasetName }}</h2>
          <p class="ev-page-subtitle">
            默认按不确定度降序（<code class="ev-mono">uncertainty_score DESC</code>）优先呈现最需人工定标的边缘样本
          </p>
        </div>
      </div>
    </div>

    <div class="ev-filter-bar">
      <div class="ev-filter-bar__left">
        <el-segmented
          v-model="splitFilter"
          :options="splitOptions"
          @change="onFilterChange"
        />
        <el-select
          v-model="statusFilter"
          placeholder="全部审核状态"
          clearable
          style="width: 165px"
          @change="onFilterChange"
        >
          <el-option label="待审核 (PENDING_REVIEW)" value="PENDING_REVIEW" />
          <el-option label="已审核生效 (APPROVED)" value="APPROVED" />
          <el-option label="草稿 (DRAFT)" value="DRAFT" />
        </el-select>
        <el-select v-model="sortOrder" style="width: 190px" @change="onFilterChange">
          <el-option label="不确定度降序 (优先校准)" value="uncertainty_desc" />
          <el-option label="用例顺序 (ID 升序)" value="id_asc" />
        </el-select>
        <el-input
          v-model="keyword"
          placeholder="搜索用例编码或提问文本"
          clearable
          style="width: 210px"
          @keyup.enter="onFilterChange"
          @clear="onFilterChange"
        />
        <el-button @click="onFilterChange">查询</el-button>
        <el-button @click="resetFilters">重置</el-button>
      </div>
      <div class="ev-filter-bar__right">
        <span class="ev-mono ev-count-tag">共 {{ total }} 条样本</span>
        <el-button :icon="Refresh" @click="fetchCases">刷新</el-button>
      </div>
    </div>

    <el-skeleton v-if="loading" :rows="6" animated class="ev-section-mt" />

    <div v-else-if="cases.length === 0" class="ev-surface ev-section-mt">
      <EmptyStateBox
        :filtered="Boolean(splitFilter !== 'ALL' || statusFilter || keyword)"
        entity-name="待校准测试用例"
        @reset="resetFilters"
      />
    </div>

    <div v-else class="ev-curation-grid ev-section-mt">
      <!-- Left Column: Queue + Read-only Desensitized Context -->
      <section class="ev-surface ev-curation-left">
        <div class="ev-queue-strip">
          <button
            v-for="(item, idx) in cases"
            :key="item.id"
            type="button"
            data-testid="curation-case-item"
            class="ev-queue-chip"
            :class="{ 'is-selected': idx === selectedIndex }"
            @click="selectCaseIndex(idx)"
          >
            <span class="ev-mono">{{ item.case_code }}</span>
            <SplitBadge :split="item.dataset_split" compact />
            <span v-if="item.uncertainty_score !== null" class="ev-mono ev-unc-val">
              U={{ formatScore(item.uncertainty_score, 2) }}
            </span>
          </button>
        </div>

        <template v-if="activeCase">
          <div class="ev-case-meta-bar ev-panel">
            <div class="ev-case-meta-left">
              <span class="ev-mono ev-strong-code">{{ activeCase.case_code }}</span>
              <SplitBadge :split="activeCase.dataset_split" />
              <DualStatusBadge
                :tone="activeCase.pii_masked === 1 ? 'success' : 'danger'"
                :symbol="activeCase.pii_masked === 1 ? '✓' : '✕'"
                :label="activeCase.pii_masked === 1 ? '已脱敏确认' : '未脱敏 (pii_masked=0)'"
              />
              <DualStatusBadge
                v-if="evalLevel === 'COMPONENT'"
                :tone="activeCase.has_snapshot ? 'success' : 'warning'"
                :symbol="activeCase.has_snapshot ? '✓' : '!'"
                :label="activeCase.has_snapshot ? '已冻结组件快照' : '缺少回放快照'"
              />
            </div>
            <span class="ev-mono ev-cell-secondary">
              不确定度: {{ formatScore(activeCase.uncertainty_score, 4) }}
            </span>
          </div>

          <h3 class="ev-sub-title">已脱敏输入提问 (input_text · 只读审阅)</h3>
          <div class="ev-prose ev-panel">{{ activeCase.input_text }}</div>

          <template v-if="activeCase.conversation_turns_json?.length">
            <h3 class="ev-sub-title">多轮对话上下文 (conversation_turns_json)</h3>
            <pre class="ev-code-box ev-mono">{{
              JSON.stringify(activeCase.conversation_turns_json, null, 2)
            }}</pre>
          </template>

          <template v-if="activeCase.reference_contexts_json?.length">
            <h3 class="ev-sub-title">
              冻结参考材料片段 ({{ activeCase.reference_contexts_json.length }} 条)
            </h3>
            <ul class="ev-ref-list">
              <li
                v-for="(refItem, rIdx) in activeCase.reference_contexts_json"
                :key="rIdx"
                class="ev-panel ev-prose"
              >
                {{ refItem }}
              </li>
            </ul>
          </template>
        </template>

        <div data-testid="curation-pagination" class="ev-pagination-row">
          <el-pagination
            v-model:current-page="page"
            v-model:page-size="pageSize"
            :page-sizes="[50, 100, 200]"
            layout="total, sizes, prev, pager, next"
            :total="total"
            @current-change="fetchCases"
            @size-change="onPageSizeChange"
          />
        </div>
      </section>

      <!-- Right Column: Ground Truth Revision & PII Guardrail Approval -->
      <section class="ev-surface ev-curation-right">
        <template v-if="activeCase">
          <div class="ev-right-header">
            <h3 class="ev-section-title">标准答案与结构化目标修订</h3>
            <DualStatusBadge
              :tone="activeCase.review_status === 'APPROVED' ? 'success' : 'warning'"
              :symbol="activeCase.review_status === 'APPROVED' ? '✓' : '!'"
              :label="activeCase.review_status"
            />
          </div>

          <el-form
            ref="curationFormRef"
            :model="editForm"
            :rules="curationRules"
            label-position="top"
            class="ev-curation-form"
          >
            <el-form-item label="标准答案文本 (expected_output)" prop="expected_output">
              <el-input
                v-model="editForm.expected_output"
                type="textarea"
                :rows="6"
                placeholder="录入人工核准的权威标准答案或关键办理要件..."
              />
            </el-form-item>

            <el-form-item
              label="结构化期望目标 JSON (expected_structured_json，可选)"
              prop="expected_structured_text"
            >
              <el-input
                v-model="editForm.expected_structured_text"
                type="textarea"
                :rows="4"
                class="ev-mono"
                placeholder='{"expected_agent_ids": ["cz_gjj_agent"], "expected_pass": true}'
              />
            </el-form-item>

            <div class="ev-grid-2">
              <el-form-item label="固定分集归属 (dataset_split)" prop="dataset_split">
                <el-select v-model="editForm.dataset_split" style="width: 100%">
                  <el-option label="DEV 调优集（允许调参与寻优）" value="DEV" />
                  <el-option label="HOLDOUT 留出验真集（最终验收锁定）" value="HOLDOUT" />
                </el-select>
              </el-form-item>
              <el-form-item label="样本难度分级" prop="difficulty">
                <el-select v-model="editForm.difficulty" style="width: 100%">
                  <el-option label="EASY 基础" value="EASY" />
                  <el-option label="NORMAL 常规" value="NORMAL" />
                  <el-option label="HARD 复杂边界" value="HARD" />
                  <el-option label="ADVERSARIAL 对抗诱导" value="ADVERSARIAL" />
                </el-select>
              </el-form-item>
            </div>

            <el-form-item label="脱敏状态核验 (pii_masked)">
              <div class="ev-pii-check-row ev-panel">
                <el-checkbox
                  :model-value="editForm.pii_masked === 1"
                  @change="(val: unknown) => (editForm.pii_masked = val ? 1 : 0)"
                >
                  已确认本条语料不含真实姓名、身份证号、手机号等敏感个人信息（pii_masked = 1）
                </el-checkbox>
              </div>
            </el-form-item>

            <el-form-item label="校准备注 (review_note)">
              <el-input v-model="editForm.review_note" placeholder="可选记录政策依据来源或修订原因" />
            </el-form-item>

            <div
              v-if="isApprovalBlocked"
              class="ev-unmasked-banner ev-panel"
            >
              {{ UNMASKED_GUARD_COPY }}
            </div>

            <!-- Action Bar: Max 1 Primary Button, pii_masked=0 Disabled + Exact Tooltip -->
            <div class="ev-curation-actions">
              <el-button
                :loading="saving"
                @click="submitCuration('PENDING_REVIEW')"
              >
                暂存修订草稿
              </el-button>

              <el-tooltip
                :content="approvalGuardMessage"
                :disabled="!isApprovalBlocked"
                placement="top"
              >
                <span
                  data-testid="approve-guard-reason"
                  :data-guard-message="approvalGuardMessage"
                  class="ev-tooltip-wrap"
                >
                  <el-button
                    data-testid="btn-approve-case"
                    type="primary"
                    :icon="Check"
                    :loading="saving"
                    :disabled="isApprovalBlocked"
                    @click="submitCuration('APPROVED')"
                  >
                    审核通过并下一条
                  </el-button>
                </span>
              </el-tooltip>
            </div>
          </el-form>
        </template>
      </section>
    </div>
  </div>
</template>

<style scoped>
.ev-section-mt {
  margin-top: 16px;
}

.ev-bench-title-row {
  display: flex;
  align-items: center;
  gap: 14px;
}

.ev-count-tag {
  font-size: var(--ev-fs-label);
  color: var(--ev-text-secondary);
}

.ev-curation-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.ev-queue-strip {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  padding-bottom: 10px;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--ev-border-subtle);
}

.ev-queue-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border-radius: var(--ev-radius-md);
  border: 1px solid var(--ev-border-subtle);
  background-color: var(--ev-bg-panel);
  color: var(--ev-text-secondary);
  cursor: pointer;
  white-space: nowrap;
}

.ev-queue-chip:hover {
  border-color: var(--ev-border-strong);
  color: var(--ev-text-primary);
}

.ev-queue-chip.is-selected {
  background-color: var(--ev-color-primary-subtle);
  border-color: var(--ev-color-primary);
  color: var(--ev-color-primary);
  font-weight: 600;
}

.ev-unc-val {
  font-size: var(--ev-fs-label);
  color: var(--ev-color-warning);
}

.ev-case-meta-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.ev-case-meta-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.ev-strong-code {
  font-weight: 600;
  color: var(--ev-text-primary);
}

.ev-sub-title {
  margin: 14px 0 6px;
  font-size: var(--ev-fs-label);
  font-weight: 600;
  color: var(--ev-text-secondary);
}

.ev-code-box {
  margin: 0;
  padding: 10px 12px;
  background-color: var(--ev-bg-panel);
  border: 1px solid var(--ev-border-subtle);
  border-radius: var(--ev-radius-md);
  max-height: 180px;
  overflow-y: auto;
}

.ev-ref-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ev-right-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.ev-grid-2 {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.ev-pii-check-row {
  width: 100%;
}

.ev-unmasked-banner {
  margin-bottom: 12px;
  background-color: var(--ev-color-danger-subtle);
  border-color: var(--ev-color-danger-border);
  color: var(--ev-color-danger);
  font-size: var(--ev-fs-label);
  line-height: 1.5;
}

.ev-curation-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding-top: 12px;
  border-top: 1px solid var(--ev-border-subtle);
}

.ev-tooltip-wrap {
  display: inline-flex;
}

.ev-cell-secondary {
  font-size: var(--ev-fs-label);
  color: var(--ev-text-secondary);
}

.ev-pagination-row {
  display: flex;
  justify-content: flex-end;
  padding-top: 14px;
  margin-top: 14px;
  border-top: 1px solid var(--ev-border-subtle);
}

@media (max-width: 1023px) {
  .ev-curation-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 767px) {
  .ev-grid-2 {
    grid-template-columns: 1fr;
  }
}
</style>
