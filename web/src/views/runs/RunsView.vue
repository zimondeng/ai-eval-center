<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { DataAnalysis, Plus, Refresh, VideoPause } from '@element-plus/icons-vue'
import {
  adaptersApi,
  datasetsApi,
  providersApi,
  runsApi,
  suitesApi,
} from '@/api/endpoints'
import {
  formatPercent,
  formatReadableError,
  formatScore,
  validateOptionalJsonObject,
} from '@/api/http'
import type {
  DatasetRecord,
  DecisionProviderRecord,
  EvalRunRecord,
  SuiteRecord,
  SutAdapterRecord,
} from '@/api/types'
import DualStatusBadge from '@/components/DualStatusBadge.vue'
import EmptyStateBox from '@/components/EmptyStateBox.vue'
import SplitBadge from '@/components/SplitBadge.vue'

const router = useRouter()

const loading = ref(false)
const errorBanner = ref<string | null>(null)
const runs = ref<EvalRunRecord[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

const suites = ref<SuiteRecord[]>([])
const datasets = ref<DatasetRecord[]>([])
const adapters = ref<SutAdapterRecord[]>([])
const providers = ref<DecisionProviderRecord[]>([])

/* ---- Filter Bar State ---- */
const splitFilter = ref<'ALL' | 'DEV' | 'HOLDOUT'>('ALL')
const filterSuiteCode = ref('')
const filterStatus = ref('')

const splitSegmentedOptions = [
  { label: 'ALL 全部分集', value: 'ALL' },
  { label: 'DEV 调优集', value: 'DEV' },
  { label: 'HOLDOUT 留出集', value: 'HOLDOUT' },
]

/* ---- Initiate Run Drawer ---- */
const createDrawerOpen = ref(false)
const creatingRun = ref(false)
const runFormRef = ref<FormInstance>()
const runForm = reactive({
  run_name: '',
  suite_code: '',
  dataset_id: null as number | null,
  dataset_split_filter: 'DEV' as 'DEV' | 'HOLDOUT' | 'ALL',
  sut_adapter_code: '',
  provider_code: '',
  model_override: '',
  baseline_run_id: null as number | null,
  git_sha: '',
  prompt_version: '',
  component_name: '',
  config_overrides_text: '{}',
})

const runRules: FormRules = {
  run_name: [
    {
      required: true,
      validator: (_rule, val: string, cb) =>
        !val?.trim() ? cb(new Error('请填写实验批次名称')) : cb(),
      trigger: 'blur',
    },
  ],
  suite_code: [{ required: true, message: '请选择质量门禁套件', trigger: 'change' }],
  dataset_id: [{ required: true, message: '请选择目标测试集', trigger: 'change' }],
  dataset_split_filter: [{ required: true, message: '请选择测试集分集范围', trigger: 'change' }],
  config_overrides_text: [{ validator: validateOptionalJsonObject, trigger: 'blur' }],
}

const selectedSuite = computed(() =>
  suites.value.find((s) => s.suite_code === runForm.suite_code) ?? null,
)

const compatibleDatasets = computed(() => {
  if (!selectedSuite.value) return datasets.value
  return datasets.value.filter((d) => d.eval_level === selectedSuite.value?.eval_level)
})

const compatibleAdapters = computed(() => {
  if (!selectedSuite.value) return adapters.value
  return adapters.value.filter((a) => a.eval_level === selectedSuite.value?.eval_level)
})

function onSuiteSelected(code: string): void {
  const s = suites.value.find((x) => x.suite_code === code)
  if (!s) return
  const defAdapter = adapters.value.find((a) => a.id === s.default_adapter_id)
  if (defAdapter) runForm.sut_adapter_code = defAdapter.adapter_code
  const defProvider = providers.value.find((p) => p.id === s.default_provider_id)
  if (defProvider) runForm.provider_code = defProvider.provider_code
  runForm.model_override = s.default_model_override || ''
  const matchingDs = datasets.value.find((d) => d.eval_level === s.eval_level)
  if (matchingDs) runForm.dataset_id = matchingDs.id
}

async function fetchRuns(): Promise<void> {
  loading.value = true
  errorBanner.value = null
  try {
    const res = await runsApi.list({
      dataset_split_filter: splitFilter.value === 'ALL' ? undefined : splitFilter.value,
      suite_code: filterSuiteCode.value || undefined,
      status: filterStatus.value || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    runs.value = res.items
    total.value = res.total
  } catch (err) {
    errorBanner.value = formatReadableError(err)
  } finally {
    loading.value = false
  }
}

async function loadCatalogAndRuns(): Promise<void> {
  try {
    const [s, ds, ad, pv] = await Promise.all([
      suitesApi.list({ status: 'ACTIVE' }),
      datasetsApi.list(),
      adaptersApi.list({ status: 'ACTIVE' }),
      providersApi.list('ACTIVE'),
    ])
    suites.value = s
    datasets.value = ds
    adapters.value = ad
    providers.value = pv
  } catch {
    // non-fatal catalog preload failure
  }
  await fetchRuns()
}

function resetFilters(): void {
  splitFilter.value = 'ALL'
  filterSuiteCode.value = ''
  filterStatus.value = ''
  page.value = 1
  void fetchRuns()
}

function openInitiateDrawer(): void {
  const firstSuite = suites.value[0]
  runForm.run_name = ''
  runForm.suite_code = firstSuite?.suite_code || ''
  runForm.dataset_id = null
  runForm.sut_adapter_code = ''
  runForm.provider_code = ''
  runForm.model_override = ''
  runForm.dataset_split_filter = 'DEV'
  runForm.baseline_run_id = null
  runForm.git_sha = ''
  runForm.prompt_version = ''
  runForm.component_name = ''
  runForm.config_overrides_text = '{}'
  if (firstSuite) {
    onSuiteSelected(firstSuite.suite_code)
  }
  createDrawerOpen.value = true
  runFormRef.value?.clearValidate()
}

async function handleCreateRun(): Promise<void> {
  if (runFormRef.value) {
    const ok = await runFormRef.value.validate().catch(() => false)
    if (!ok) return
  }
  if (!runForm.dataset_id) return

  const overridesObj = runForm.config_overrides_text.trim()
    ? (JSON.parse(runForm.config_overrides_text) as Record<string, unknown>)
    : {}

  const fingerprint: Record<string, unknown> = {}
  if (runForm.git_sha.trim()) fingerprint.git_sha = runForm.git_sha.trim()
  if (runForm.prompt_version.trim()) fingerprint.prompt_version = runForm.prompt_version.trim()
  if (runForm.component_name.trim()) fingerprint.component = runForm.component_name.trim()
  if (Object.keys(overridesObj).length > 0) fingerprint.config_overrides = overridesObj

  creatingRun.value = true
  try {
    const created = await runsApi.create({
      run_name: runForm.run_name.trim(),
      suite_code: runForm.suite_code,
      dataset_id: runForm.dataset_id,
      dataset_split_filter: runForm.dataset_split_filter,
      sut_adapter_code: runForm.sut_adapter_code || null,
      provider_code: runForm.provider_code || null,
      model_override: runForm.model_override.trim() || null,
      baseline_run_id: runForm.baseline_run_id,
      sut_fingerprint: fingerprint,
      run_overrides: Object.keys(overridesObj).length > 0 ? overridesObj : null,
    })
    ElMessage.success(`实验批次 ${created.run_no} 已成功发起`)
    createDrawerOpen.value = false
    await fetchRuns()
  } catch (err) {
    ElMessage.error(formatReadableError(err))
  } finally {
    creatingRun.value = false
  }
}

async function handleCancelRun(row: EvalRunRecord): Promise<void> {
  try {
    await runsApi.cancel(row.run_no)
    ElMessage.info(`已请求取消批次 ${row.run_no}`)
    await fetchRuns()
  } catch (err) {
    ElMessage.error(formatReadableError(err))
  }
}

function goReport(runNo: string): void {
  void router.push(`/reports/${encodeURIComponent(runNo)}`)
}

function progressPercent(row: EvalRunRecord): number {
  if (!row.total_cases) return 0
  return Math.min(100, Math.round((row.completed_cases / row.total_cases) * 100))
}

onMounted(() => {
  void loadCatalogAndRuns()
})
</script>

<template>
  <div class="ev-page">
    <div class="ev-page-header">
      <div>
        <h1 class="ev-page-title">实验批次中心</h1>
        <p class="ev-page-subtitle">
          执行端到端黑盒验收或内部组件隔离回放批次，全程锁定评价维度版本与判别模型标尺快照
        </p>
      </div>
    </div>

    <el-alert
      v-if="errorBanner"
      :title="errorBanner"
      type="error"
      show-icon
      closable
      @close="errorBanner = null"
    />

    <!-- DESIGN.md §8.1 Filter & Split Bar (Max 1 Primary Button) -->
    <div class="ev-filter-bar">
      <div class="ev-filter-bar__left">
        <el-segmented
          v-model="splitFilter"
          :options="splitSegmentedOptions"
          @change="fetchRuns"
        />
        <el-select
          v-model="filterSuiteCode"
          placeholder="全部评测套件"
          clearable
          style="width: 210px"
          @change="fetchRuns"
        >
          <el-option
            v-for="s in suites"
            :key="s.suite_code"
            :label="`${s.suite_code} (${s.suite_name})`"
            :value="s.suite_code"
          />
        </el-select>
        <el-select
          v-model="filterStatus"
          placeholder="全部状态"
          clearable
          style="width: 150px"
          @change="fetchRuns"
        >
          <el-option label="COMPLETED 已完成" value="COMPLETED" />
          <el-option label="RUNNING 执行中" value="RUNNING" />
          <el-option label="PENDING 排队中" value="PENDING" />
          <el-option label="FAILED 失败" value="FAILED" />
        </el-select>
        <el-button @click="fetchRuns">查询</el-button>
        <el-button @click="resetFilters">重置</el-button>
        <el-button :icon="Refresh" @click="fetchRuns">刷新</el-button>
      </div>
      <div class="ev-filter-bar__right">
        <el-button type="primary" :icon="Plus" @click="openInitiateDrawer">
          发起实验批次
        </el-button>
      </div>
    </div>

    <!-- High-Density Runs Table -->
    <div class="ev-surface">
      <el-skeleton v-if="loading" :rows="6" animated />
      <EmptyStateBox
        v-else-if="runs.length === 0"
        :filtered="Boolean(splitFilter !== 'ALL' || filterSuiteCode || filterStatus)"
        entity-name="实验批次"
        action-label="发起实验批次"
        @reset="resetFilters"
        @action="openInitiateDrawer"
      />
      <template v-else>
        <el-table :data="runs" stripe style="width: 100%">
          <el-table-column label="批次号 / 实验名称" min-width="240" fixed="left">
            <template #default="{ row }">
              <div class="ev-mono ev-run-code" @click="goReport(row.run_no)">
                {{ row.run_no }}
              </div>
              <div class="ev-cell-secondary">{{ row.run_name }}</div>
            </template>
          </el-table-column>

          <el-table-column label="测试集分集" width="165">
            <template #default="{ row }">
              <SplitBadge :split="row.dataset_split_filter" />
              <div class="ev-cell-secondary ev-mono">{{ row.dataset_code }}</div>
            </template>
          </el-table-column>

          <el-table-column label="套件与锁定标尺快照" min-width="230">
            <template #default="{ row }">
              <div class="ev-mono">{{ row.suite_code }}</div>
              <div class="ev-cell-secondary ev-mono">
                模型: {{ row.pinned_model_name }}
              </div>
              <DualStatusBadge
                v-if="row.cross_model_warning"
                tone="warning"
                symbol="!"
                label="跨模型对比预警"
              />
            </template>
          </el-table-column>

          <el-table-column label="门禁通过率 / 加权均分" width="210" align="right">
            <template #default="{ row }">
              <template v-if="row.pass_rate !== null">
                <DualStatusBadge
                  :tone="row.pass_rate >= 0.8 ? 'success' : row.pass_rate >= 0.6 ? 'warning' : 'danger'"
                  :symbol="row.pass_rate >= 0.8 ? '✓' : row.pass_rate >= 0.6 ? '!' : '✕'"
                  :label="row.pass_rate >= 0.8 ? '通过率' : '待关注'"
                  :value="formatPercent(row.pass_rate, 1)"
                />
                <div class="ev-cell-secondary ev-mono">
                  均分 {{ formatScore(row.weighted_avg_score, 4) }}
                </div>
              </template>
              <span v-else class="ev-mono ev-cell-secondary">—</span>
            </template>
          </el-table-column>

          <el-table-column label="合批请求数 / 压缩比" width="165" align="right">
            <template #default="{ row }">
              <div class="ev-mono">{{ row.total_decision_calls }} 次请求</div>
              <div class="ev-cell-secondary ev-mono">
                压缩 {{ row.batch_compression_ratio }}x
              </div>
            </template>
          </el-table-column>

          <el-table-column label="执行状态与进度" width="185">
            <template #default="{ row }">
              <DualStatusBadge
                :tone="
                  row.status === 'COMPLETED'
                    ? 'success'
                    : row.status === 'FAILED'
                      ? 'danger'
                      : row.status === 'RUNNING'
                        ? 'warning'
                        : 'neutral'
                "
                :symbol="
                  row.status === 'COMPLETED'
                    ? '✓'
                    : row.status === 'FAILED'
                      ? '✕'
                      : '!'
                "
                :label="row.status"
                :value="`${row.completed_cases}/${row.total_cases}`"
              />
              <el-progress
                :percentage="progressPercent(row)"
                :show-text="false"
                :stroke-width="5"
                class="ev-prog-bar"
              />
            </template>
          </el-table-column>

          <el-table-column label="操作" width="190" fixed="right">
            <template #default="{ row }">
              <div class="ev-row-actions">
                <el-button
                  size="small"
                  :icon="DataAnalysis"
                  @click="goReport(row.run_no)"
                >
                  评估报告
                </el-button>
                <el-popconfirm
                  v-if="row.status === 'RUNNING' || row.status === 'PENDING'"
                  title="确定终止该运行中的实验批次吗？已评完用例将保留。"
                  confirm-button-text="确认取消"
                  cancel-button-text="返回"
                  confirm-button-type="danger"
                  width="280"
                  @confirm="handleCancelRun(row)"
                >
                  <template #reference>
                    <el-button
                      data-testid="btn-cancel-run"
                      size="small"
                      type="danger"
                      plain
                      :icon="VideoPause"
                    >
                      取消
                    </el-button>
                  </template>
                </el-popconfirm>
              </div>
            </template>
          </el-table-column>
        </el-table>

        <div class="ev-pagination-row">
          <el-pagination
            v-model:current-page="page"
            v-model:page-size="pageSize"
            layout="total, prev, pager, next"
            :total="total"
            @current-change="fetchRuns"
          />
        </div>
      </template>
    </div>

    <!-- Initiate Experiment Run Drawer (DESIGN.md §10) -->
    <el-drawer v-model="createDrawerOpen" title="发起实验批次" size="620px">
      <el-form ref="runFormRef" :model="runForm" :rules="runRules" label-position="top">
        <el-form-item label="1. 实验批次名称 (run_name)" prop="run_name">
          <el-input
            v-model="runForm.run_name"
            placeholder="如 路由主管提示词 v2 消融实验 (本地 tev1:0.8b)"
          />
        </el-form-item>

        <div class="ev-grid-2">
          <el-form-item label="2. 选择质量门禁套件 (suite_code)" prop="suite_code">
            <el-select
              v-model="runForm.suite_code"
              style="width: 100%"
              @change="onSuiteSelected"
            >
              <el-option
                v-for="s in suites"
                :key="s.suite_code"
                :label="`${s.suite_code} · ${s.eval_level}`"
                :value="s.suite_code"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="3. 选择目标测试集 (dataset_id)" prop="dataset_id">
            <el-select v-model="runForm.dataset_id" style="width: 100%">
              <el-option
                v-for="ds in compatibleDatasets"
                :key="ds.id"
                :label="`${ds.dataset_code} (${ds.version_tag}) · DEV:${ds.dev_case_count} / HOLDOUT:${ds.holdout_case_count}`"
                :value="ds.id"
              />
            </el-select>
          </el-form-item>
        </div>

        <el-form-item label="4. 固定测试集分集范围 (dataset_split_filter)" prop="dataset_split_filter">
          <el-radio-group v-model="runForm.dataset_split_filter">
            <el-radio value="DEV">DEV 调优集（用于日常迭代与自动阈值寻优）</el-radio>
            <el-radio value="HOLDOUT">HOLDOUT 留出验真集（用于发版门禁最终验收）</el-radio>
          </el-radio-group>
        </el-form-item>

        <div class="ev-grid-2">
          <el-form-item label="5. 被测适配器 (sut_adapter_code)">
            <el-select v-model="runForm.sut_adapter_code" style="width: 100%">
              <el-option
                v-for="ad in compatibleAdapters"
                :key="ad.adapter_code"
                :label="`${ad.adapter_code} (${ad.adapter_type})`"
                :value="ad.adapter_code"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="6. 锁定判别模型渠道 (provider_code)">
            <el-select v-model="runForm.provider_code" style="width: 100%">
              <el-option
                v-for="pv in providers"
                :key="pv.provider_code"
                :label="`${pv.provider_code} (${pv.default_model})`"
                :value="pv.provider_code"
              />
            </el-select>
          </el-form-item>
        </div>

        <div class="ev-grid-2">
          <el-form-item label="判别模型标识覆盖 (model_override，留空使用渠道默认)">
            <el-input v-model="runForm.model_override" placeholder="如 tev1:0.8b 或 typesafe/jev-1.14.0" />
          </el-form-item>
          <el-form-item label="绑定回归基线批次 (baseline_run_id，可选)">
            <el-select v-model="runForm.baseline_run_id" clearable placeholder="不对比基线" style="width: 100%">
              <el-option
                v-for="r in runs"
                :key="r.id"
                :label="`${r.run_no} (${r.pinned_model_name})`"
                :value="r.id"
              />
            </el-select>
          </el-form-item>
        </div>

        <div class="ev-panel">
          <h4 class="ev-fp-title">7. 被测系统版本指纹快照 (sut_fingerprint)</h4>
          <div class="ev-grid-3">
            <el-form-item label="Git Commit SHA">
              <el-input v-model="runForm.git_sha" placeholder="如 846e4b9b" class="ev-mono" />
            </el-form-item>
            <el-form-item label="提示词版本标识">
              <el-input v-model="runForm.prompt_version" placeholder="如 supervisor_v2" class="ev-mono" />
            </el-form-item>
            <el-form-item label="被测组件名 (组件回放)">
              <el-input v-model="runForm.component_name" placeholder="如 SupervisorRouter" class="ev-mono" />
            </el-form-item>
          </div>
          <el-form-item label="消融覆盖参数 JSON (config_overrides)" prop="config_overrides_text">
            <el-input
              v-model="runForm.config_overrides_text"
              type="textarea"
              :rows="2"
              class="ev-mono"
            />
          </el-form-item>
        </div>
      </el-form>

      <template #footer>
        <div class="ev-drawer-footer">
          <el-button @click="createDrawerOpen = false">取消</el-button>
          <el-button
            data-testid="btn-submit-create-run"
            type="primary"
            :loading="creatingRun"
            @click="handleCreateRun"
          >
            立即发起实验批次
          </el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.ev-run-code {
  color: var(--ev-color-primary);
  cursor: pointer;
  font-weight: 600;
}

.ev-run-code:hover {
  text-decoration: underline;
}

.ev-cell-secondary {
  font-size: var(--ev-fs-label);
  color: var(--ev-text-secondary);
  margin-top: 2px;
}

.ev-prog-bar {
  margin-top: 6px;
}

.ev-row-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.ev-pagination-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}

.ev-grid-2 {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.ev-grid-3 {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.ev-fp-title {
  margin: 0 0 10px;
  font-size: var(--ev-fs-label);
  font-weight: 600;
  color: var(--ev-text-secondary);
}

.ev-drawer-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 767px) {
  .ev-grid-2,
  .ev-grid-3 {
    grid-template-columns: 1fr;
  }
}
</style>
