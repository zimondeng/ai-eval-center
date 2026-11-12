<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { Download, EditPen, Plus, Refresh, Upload, VideoCamera } from '@element-plus/icons-vue'
import { adaptersApi, datasetsApi } from '@/api/endpoints'
import { formatReadableError } from '@/api/http'
import type { DatasetRecord, EvalLevel, SutAdapterRecord } from '@/api/types'
import DualStatusBadge from '@/components/DualStatusBadge.vue'
import EmptyStateBox from '@/components/EmptyStateBox.vue'
import SplitBadge from '@/components/SplitBadge.vue'
import CurationBenchPanel from './CurationBenchPanel.vue'

const loading = ref(false)
const errorBanner = ref<string | null>(null)
const datasets = ref<DatasetRecord[]>([])
const adapters = ref<SutAdapterRecord[]>([])

const filterLevel = ref<string>('')
const searchQuery = ref('')

const activeCurationDataset = ref<DatasetRecord | null>(null)

/* ---- Create Dataset Dialog ---- */
const createDialogOpen = ref(false)
const creatingDataset = ref(false)
const createFormRef = ref<FormInstance>()
const createForm = reactive({
  dataset_code: '',
  version_tag: 'v1.0',
  dataset_name: '',
  eval_level: 'E2E' as EvalLevel,
  desensitize_policy_snapshot: 'gov_eval_corpus',
  status: 'PUBLISHED' as 'DRAFT' | 'PUBLISHED' | 'ARCHIVED',
  description: '',
})
const createRules: FormRules = {
  dataset_code: [
    {
      required: true,
      validator: (_rule, val: string, cb) =>
        !val?.trim() ? cb(new Error('请填写测试集编码')) : cb(),
      trigger: 'blur',
    },
  ],
  version_tag: [
    {
      required: true,
      validator: (_rule, val: string, cb) =>
        !val?.trim() ? cb(new Error('请填写版本标签')) : cb(),
      trigger: 'blur',
    },
  ],
  dataset_name: [
    {
      required: true,
      validator: (_rule, val: string, cb) =>
        !val?.trim() ? cb(new Error('请填写测试集名称')) : cb(),
      trigger: 'blur',
    },
  ],
}

/* ---- Offline JSONL Import Drawer ---- */
const importDrawerOpen = ref(false)
const importingCases = ref(false)
const importTargetDataset = ref<DatasetRecord | null>(null)
const importFormRef = ref<FormInstance>()
const importForm = reactive({
  jsonl_text:
    '{"case_code":"gjj_001","input_text":"常州灵活就业人员如何办理公积金开户？","expected_output":"凭身份证与一类卡线上办理","pii_masked":1,"uncertainty_score":0.76}\n',
  auto_desensitize: false,
  desensitize_policy_code: 'gov_eval_corpus',
  dev_ratio: 0.5,
})
const importRules: FormRules = {
  jsonl_text: [
    {
      required: true,
      validator: (_rule, val: string, cb) =>
        !val?.trim() ? cb(new Error('请输入至少一行 JSONL 用例数据')) : cb(),
      trigger: 'blur',
    },
  ],
  dev_ratio: [
    {
      required: true,
      type: 'number',
      min: 0.1,
      max: 0.9,
      message: 'DEV 比例须在 0.10 到 0.90 之间',
      trigger: 'change',
    },
  ],
  desensitize_policy_code: [
    {
      required: true,
      validator: (_rule, val: string, cb) =>
        !val?.trim() ? cb(new Error('请填写脱敏策略编码')) : cb(),
      trigger: 'blur',
    },
  ],
}

/* ---- Online Read-Only Desensitized Pull Drawer ---- */
const mysqlDrawerOpen = ref(false)
const pullingMysql = ref(false)
const mysqlTargetDataset = ref<DatasetRecord | null>(null)
const mysqlFormRef = ref<FormInstance>()
const mysqlForm = reactive({
  source_dsn_env: 'GOV_ONLINE_READONLY_DSN',
  preset_extractor: 'gov_routing_conversation',
  start_time: '2026-11-01T00:00:00',
  end_time: '2026-11-12T00:00:00',
  mining_signals: ['USER_DISLIKE', 'FALLBACK', 'STRATIFIED_SAMPLE'] as string[],
  limit: 200,
  desensitize_policy_code: 'gov_eval_corpus',
  dev_ratio: 0.5,
})
const mysqlRules: FormRules = {
  source_dsn_env: [
    {
      required: true,
      validator: (_rule, val: string, cb) =>
        !val?.trim() ? cb(new Error('请填写只读数据源环境变量名')) : cb(),
      trigger: 'blur',
    },
  ],
  preset_extractor: [
    {
      required: true,
      validator: (_rule, val: string, cb) =>
        !val?.trim() ? cb(new Error('请填写预设抽取器名称')) : cb(),
      trigger: 'blur',
    },
  ],
  limit: [
    {
      required: true,
      type: 'number',
      min: 1,
      max: 2000,
      message: '抽样条数须在 1 到 2000 之间',
      trigger: 'change',
    },
  ],
  desensitize_policy_code: [
    {
      required: true,
      validator: (_rule, val: string, cb) =>
        !val?.trim() ? cb(new Error('请填写脱敏策略编码')) : cb(),
      trigger: 'blur',
    },
  ],
  dev_ratio: [
    {
      required: true,
      type: 'number',
      min: 0.1,
      max: 0.9,
      message: 'DEV 比例须在 0.10 到 0.90 之间',
      trigger: 'change',
    },
  ],
}

/* ---- Component Snapshot Recorder Drawer ---- */
const recordDrawerOpen = ref(false)
const recordingSnapshots = ref(false)
const recordTargetDataset = ref<DatasetRecord | null>(null)
const recordFormRef = ref<FormInstance>()
const recordForm = reactive({
  adapter_code: '',
  split: '' as '' | 'DEV' | 'HOLDOUT',
  only_missing: true,
})
const recordRules: FormRules = {
  adapter_code: [{ required: true, message: '请选择 COMPONENT_HARNESS 类型适配器', trigger: 'change' }],
}

const componentHarnessAdapters = computed(() =>
  adapters.value.filter((a) => a.adapter_type === 'COMPONENT_HARNESS'),
)

const filteredDatasets = computed(() => {
  return datasets.value.filter((d) => {
    if (filterLevel.value && d.eval_level !== filterLevel.value) return false
    if (searchQuery.value.trim()) {
      const q = searchQuery.value.trim().toLowerCase()
      if (
        !d.dataset_code.toLowerCase().includes(q) &&
        !d.dataset_name.toLowerCase().includes(q)
      ) {
        return false
      }
    }
    return true
  })
})

async function loadAll(): Promise<void> {
  loading.value = true
  errorBanner.value = null
  try {
    const [ds, ad] = await Promise.all([datasetsApi.list(), adaptersApi.list()])
    datasets.value = ds
    adapters.value = ad
  } catch (err) {
    errorBanner.value = formatReadableError(err)
  } finally {
    loading.value = false
  }
}

function openCreateDataset(): void {
  createForm.dataset_code = ''
  createForm.version_tag = 'v1.0'
  createForm.dataset_name = ''
  createForm.eval_level = 'E2E'
  createForm.description = ''
  createDialogOpen.value = true
  createFormRef.value?.clearValidate()
}

async function handleCreateDataset(): Promise<void> {
  if (createFormRef.value) {
    const ok = await createFormRef.value.validate().catch(() => false)
    if (!ok) return
  }
  creatingDataset.value = true
  try {
    await datasetsApi.create({
      dataset_code: createForm.dataset_code.trim(),
      version_tag: createForm.version_tag.trim(),
      dataset_name: createForm.dataset_name.trim(),
      eval_level: createForm.eval_level,
      desensitize_policy_snapshot: createForm.desensitize_policy_snapshot.trim() || null,
      status: createForm.status,
      description: createForm.description.trim() || null,
    })
    ElMessage.success('测试集已创建')
    createDialogOpen.value = false
    datasets.value = await datasetsApi.list()
  } catch (err) {
    ElMessage.error(formatReadableError(err))
  } finally {
    creatingDataset.value = false
  }
}

function openImportDrawer(row: DatasetRecord): void {
  importTargetDataset.value = row
  importDrawerOpen.value = true
  importFormRef.value?.clearValidate()
}

async function handleImportJsonl(): Promise<void> {
  if (!importTargetDataset.value) return
  if (importFormRef.value) {
    const ok = await importFormRef.value.validate().catch(() => false)
    if (!ok) return
  }
  importingCases.value = true
  try {
    const res = await datasetsApi.importCases(importTargetDataset.value.id, {
      jsonl_text: importForm.jsonl_text,
      auto_desensitize: importForm.auto_desensitize,
      desensitize_policy_code: importForm.desensitize_policy_code,
      dev_ratio: importForm.dev_ratio,
    })
    ElMessage.success(
      `导入完成：新增 ${res.imported_count} 条（DEV ${res.dev_imported_count} / HOLDOUT ${res.holdout_imported_count}）`,
    )
    importDrawerOpen.value = false
    datasets.value = await datasetsApi.list()
  } catch (err) {
    ElMessage.error(formatReadableError(err))
  } finally {
    importingCases.value = false
  }
}

function openMysqlDrawer(row: DatasetRecord): void {
  mysqlTargetDataset.value = row
  mysqlDrawerOpen.value = true
  mysqlFormRef.value?.clearValidate()
}

async function handleMysqlIngest(): Promise<void> {
  if (!mysqlTargetDataset.value) return
  if (mysqlFormRef.value) {
    const ok = await mysqlFormRef.value.validate().catch(() => false)
    if (!ok) return
  }
  pullingMysql.value = true
  try {
    const res = await datasetsApi.ingestFromMysql(mysqlTargetDataset.value.id, {
      source_dsn_env: mysqlForm.source_dsn_env.trim(),
      preset_extractor: mysqlForm.preset_extractor.trim(),
      filter: {
        start_time: mysqlForm.start_time || null,
        end_time: mysqlForm.end_time || null,
        mining_signals: mysqlForm.mining_signals,
        limit: mysqlForm.limit,
      },
      desensitize_policy_code: mysqlForm.desensitize_policy_code.trim(),
      dev_ratio: mysqlForm.dev_ratio,
    })
    ElMessage.success(
      `脱敏拉取完成：入库 ${res.ingested_count} 条（策略快照 ${res.desensitize_policy_snapshot || '已记录'}）`,
    )
    mysqlDrawerOpen.value = false
    datasets.value = await datasetsApi.list()
  } catch (err) {
    ElMessage.error(formatReadableError(err))
  } finally {
    pullingMysql.value = false
  }
}

function openRecordSnapshotDrawer(row: DatasetRecord): void {
  recordTargetDataset.value = row
  recordForm.adapter_code = componentHarnessAdapters.value[0]?.adapter_code || ''
  recordForm.split = ''
  recordForm.only_missing = true
  recordDrawerOpen.value = true
  recordFormRef.value?.clearValidate()
}

async function handleRecordSnapshots(): Promise<void> {
  if (!recordTargetDataset.value) return
  if (recordFormRef.value) {
    const ok = await recordFormRef.value.validate().catch(() => false)
    if (!ok) return
  }
  recordingSnapshots.value = true
  try {
    const res = await datasetsApi.recordComponentSnapshots(recordTargetDataset.value.id, {
      adapter_code: recordForm.adapter_code,
      split: recordForm.split || null,
      only_missing: recordForm.only_missing,
    })
    ElMessage.success(`组件回放快照录制完成：共冻结 ${res.recorded_count} 条样本上下文`)
    recordDrawerOpen.value = false
    datasets.value = await datasetsApi.list()
  } catch (err) {
    ElMessage.error(formatReadableError(err))
  } finally {
    recordingSnapshots.value = false
  }
}

onMounted(() => {
  void loadAll()
})
</script>

<template>
  <div class="ev-page">
    <!-- Ground Truth Curation Bench Mode -->
    <CurationBenchPanel
      v-if="activeCurationDataset"
      :dataset-id="activeCurationDataset.id"
      :dataset-name="`${activeCurationDataset.dataset_name} (${activeCurationDataset.version_tag})`"
      :eval-level="activeCurationDataset.eval_level"
      @back="activeCurationDataset = null"
      @updated="loadAll"
    />

    <!-- Dataset Governance List Mode -->
    <template v-else>
      <div class="ev-page-header">
        <div>
          <h1 class="ev-page-title">测试集治理与真值校准台</h1>
          <p class="ev-page-subtitle">
            管理 <code class="ev-mono">DEV / HOLDOUT</code> 哈希切分测试集、单向脱敏采集、组件回放快照录制与人工真值定标
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

      <div class="ev-filter-bar">
        <div class="ev-filter-bar__left">
          <el-select
            v-model="filterLevel"
            placeholder="全部评测层级"
            clearable
            style="width: 190px"
          >
            <el-option label="E2E 端到端测试集" value="E2E" />
            <el-option label="COMPONENT 组件回放集" value="COMPONENT" />
          </el-select>
          <el-input
            v-model="searchQuery"
            placeholder="搜索测试集编码或名称"
            clearable
            style="width: 220px"
          />
          <el-button @click="(filterLevel = ''), (searchQuery = '')">重置</el-button>
          <el-button :icon="Refresh" @click="loadAll">刷新</el-button>
        </div>
        <div class="ev-filter-bar__right">
          <el-button type="primary" :icon="Plus" @click="openCreateDataset">
            新增测试集
          </el-button>
        </div>
      </div>

      <div class="ev-surface">
        <el-skeleton v-if="loading" :rows="5" animated />
        <EmptyStateBox
          v-else-if="filteredDatasets.length === 0"
          :filtered="Boolean(filterLevel || searchQuery)"
          entity-name="测试集"
          action-label="新增测试集"
          @reset="(filterLevel = ''), (searchQuery = '')"
          @action="openCreateDataset"
        />
        <el-table v-else :data="filteredDatasets" stripe style="width: 100%">
          <el-table-column label="测试集编码 / 版本" min-width="230" fixed="left">
            <template #default="{ row }">
              <div class="ev-cell-primary">
                <span class="ev-mono">{{ row.dataset_code }}</span>
                <span class="ev-ver-chip ev-mono">{{ row.version_tag }}</span>
              </div>
              <div class="ev-cell-secondary">{{ row.dataset_name }}</div>
            </template>
          </el-table-column>

          <el-table-column label="评测层级" width="160">
            <template #default="{ row }">
              <DualStatusBadge
                :tone="row.eval_level === 'COMPONENT' ? 'warning' : 'neutral'"
                :symbol="row.eval_level === 'COMPONENT' ? '!' : '✓'"
                :label="row.eval_level === 'COMPONENT' ? '组件级回放' : '端到端 E2E'"
              />
            </template>
          </el-table-column>

          <el-table-column label="已审核分集样本数 (DEV / HOLDOUT)" min-width="270">
            <template #default="{ row }">
              <div class="ev-split-counts">
                <span class="ev-split-pair">
                  <SplitBadge split="DEV" compact />
                  <strong class="ev-mono">{{ row.dev_case_count }}</strong>
                </span>
                <span class="ev-split-pair">
                  <SplitBadge split="HOLDOUT" compact />
                  <strong class="ev-mono">{{ row.holdout_case_count }}</strong>
                </span>
              </div>
              <div class="ev-cell-secondary ev-mono">
                总样本 {{ row.total_case_count }} · 待校准 {{ row.pending_review_count }}
              </div>
            </template>
          </el-table-column>

          <el-table-column label="脱敏策略快照" min-width="190">
            <template #default="{ row }">
              <DualStatusBadge
                v-if="row.desensitize_policy_snapshot"
                tone="success"
                symbol="✓"
                label="脱敏快照"
                :value="row.desensitize_policy_snapshot"
              />
              <span v-else class="ev-cell-secondary">未关联外部脱敏批次</span>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="380" fixed="right">
            <template #default="{ row }">
              <div class="ev-row-actions">
                <el-button size="small" :icon="EditPen" @click="activeCurationDataset = row">
                  真值校准台
                </el-button>
                <el-button size="small" :icon="Upload" @click="openImportDrawer(row)">
                  离线导入
                </el-button>
                <el-button size="small" :icon="Download" @click="openMysqlDrawer(row)">
                  脱敏抽样
                </el-button>
                <el-button
                  v-if="row.eval_level === 'COMPONENT'"
                  size="small"
                  :icon="VideoCamera"
                  @click="openRecordSnapshotDrawer(row)"
                >
                  录制组件快照
                </el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </template>

    <!-- Create Dataset Dialog -->
    <el-dialog v-model="createDialogOpen" title="新增测试集" width="520px">
      <el-form ref="createFormRef" :model="createForm" :rules="createRules" label-position="top">
        <div class="ev-grid-2">
          <el-form-item label="测试集编码 (dataset_code)" prop="dataset_code">
            <el-input v-model="createForm.dataset_code" placeholder="如 gov_e2e_golden" />
          </el-form-item>
          <el-form-item label="版本标签 (version_tag)" prop="version_tag">
            <el-input v-model="createForm.version_tag" placeholder="如 v1.0" />
          </el-form-item>
        </div>
        <el-form-item label="测试集名称" prop="dataset_name">
          <el-input v-model="createForm.dataset_name" placeholder="如 政务智能客服黄金验收测试集" />
        </el-form-item>
        <div class="ev-grid-2">
          <el-form-item label="评测层级 (eval_level)">
            <el-select v-model="createForm.eval_level" style="width: 100%">
              <el-option label="E2E 端到端接口评测" value="E2E" />
              <el-option label="COMPONENT 内部组件隔离回放" value="COMPONENT" />
            </el-select>
          </el-form-item>
          <el-form-item label="默认脱敏策略编码">
            <el-input v-model="createForm.desensitize_policy_snapshot" />
          </el-form-item>
        </div>
        <el-form-item label="备注说明">
          <el-input v-model="createForm.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="creatingDataset" @click="handleCreateDataset">
          创建测试集
        </el-button>
      </template>
    </el-dialog>

    <!-- Offline JSONL Import Drawer -->
    <el-drawer
      v-model="importDrawerOpen"
      :title="`离线 JSONL 语料导入 · ${importTargetDataset?.dataset_code || ''}`"
      size="620px"
    >
      <el-form ref="importFormRef" :model="importForm" :rules="importRules" label-position="top">
        <el-form-item label="JSONL 文本（每行一条 JSON 用例对象）" prop="jsonl_text">
          <el-input
            v-model="importForm.jsonl_text"
            type="textarea"
            :rows="10"
            class="ev-mono"
          />
        </el-form-item>
        <div class="ev-grid-2">
          <el-form-item label="DEV 调优集切分比例 (哈希稳定切分)" prop="dev_ratio">
            <el-input-number v-model="importForm.dev_ratio" :min="0.1" :max="0.9" :step="0.1" :precision="2" />
          </el-form-item>
          <el-form-item label="脱敏策略编码" prop="desensitize_policy_code">
            <el-input v-model="importForm.desensitize_policy_code" />
          </el-form-item>
        </div>
        <el-checkbox v-model="importForm.auto_desensitize">
          入库前自动调用数据脱敏中心（data-desensitization-center）执行在线内联脱敏清洗
        </el-checkbox>
      </el-form>
      <template #footer>
        <div class="ev-drawer-footer">
          <el-button @click="importDrawerOpen = false">取消</el-button>
          <el-button type="primary" :loading="importingCases" @click="handleImportJsonl">
            执行离线语料导入
          </el-button>
        </div>
      </template>
    </el-drawer>

    <!-- Online Read-Only Desensitized Pull Drawer -->
    <el-drawer
      v-model="mysqlDrawerOpen"
      :title="`只读源抽样并单向脱敏入库 · ${mysqlTargetDataset?.dataset_code || ''}`"
      size="600px"
    >
      <div class="ev-panel ev-notice-mb">
        数据安全红线：流式抽取记录将在内存中先经数据脱敏中心清洗并校验 <code class="ev-mono">pii_masked = 1</code>，绝不落盘原始敏感语料。
      </div>
      <el-form ref="mysqlFormRef" :model="mysqlForm" :rules="mysqlRules" label-position="top">
        <div class="ev-grid-2">
          <el-form-item label="只读数据源环境变量名 (source_dsn_env)" prop="source_dsn_env">
            <el-input v-model="mysqlForm.source_dsn_env" />
          </el-form-item>
          <el-form-item label="预设抽取器 (preset_extractor)" prop="preset_extractor">
            <el-input v-model="mysqlForm.preset_extractor" />
          </el-form-item>
        </div>
        <div class="ev-grid-2">
          <el-form-item label="起始时间">
            <el-input v-model="mysqlForm.start_time" />
          </el-form-item>
          <el-form-item label="截止时间">
            <el-input v-model="mysqlForm.end_time" />
          </el-form-item>
        </div>
        <el-form-item label="困难样本挖掘信号 (mining_signals)">
          <el-checkbox-group v-model="mysqlForm.mining_signals">
            <el-checkbox label="用户点踩" value="USER_DISLIKE" />
            <el-checkbox label="市级兜底" value="CITY_BACKUP" />
            <el-checkbox label="拒答兜底" value="FALLBACK" />
            <el-checkbox label="分层随机抽样" value="STRATIFIED_SAMPLE" />
          </el-checkbox-group>
        </el-form-item>
        <div class="ev-grid-3">
          <el-form-item label="最大抽样条数" prop="limit">
            <el-input-number v-model="mysqlForm.limit" :min="1" :max="2000" />
          </el-form-item>
          <el-form-item label="脱敏策略编码" prop="desensitize_policy_code">
            <el-input v-model="mysqlForm.desensitize_policy_code" />
          </el-form-item>
          <el-form-item label="DEV 比例" prop="dev_ratio">
            <el-input-number v-model="mysqlForm.dev_ratio" :min="0.1" :max="0.9" :step="0.1" :precision="2" />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <div class="ev-drawer-footer">
          <el-button @click="mysqlDrawerOpen = false">取消</el-button>
          <el-button type="primary" :loading="pullingMysql" @click="handleMysqlIngest">
            拉取并脱敏入库
          </el-button>
        </div>
      </template>
    </el-drawer>

    <!-- Component Snapshot Recorder Drawer (Record vs Replay explicitly distinguished per DESIGN.md §8.7) -->
    <el-drawer
      v-model="recordDrawerOpen"
      :title="`录制组件回放快照 (Record Mode) · ${recordTargetDataset?.dataset_code || ''}`"
      size="560px"
    >
      <div class="ev-panel ev-notice-mb">
        <strong>Record 录制 vs Replay 回放隔离说明：</strong>
        本操作调用测试环境适配器的 <code class="ev-mono">record_endpoint_url</code> 冻结一次智能体目录与下游候选回复至 <code class="ev-mono">sut_context_json</code>；后续在「实验批次」中发起评测时将走无缓存隔离回放（Replay），零外部依赖验证组件改动。
      </div>
      <el-form ref="recordFormRef" :model="recordForm" :rules="recordRules" label-position="top">
        <el-form-item label="选择组件级 Harness 适配器" prop="adapter_code">
          <el-select v-model="recordForm.adapter_code" style="width: 100%">
            <el-option
              v-for="ad in componentHarnessAdapters"
              :key="ad.adapter_code"
              :label="`${ad.adapter_code} (${ad.record_endpoint_url || '未配录制端点'})`"
              :value="ad.adapter_code"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="目标分集范围">
          <el-select v-model="recordForm.split" clearable placeholder="全部分集" style="width: 100%">
            <el-option label="仅 DEV 调优集" value="DEV" />
            <el-option label="仅 HOLDOUT 留出集" value="HOLDOUT" />
          </el-select>
        </el-form-item>
        <el-checkbox v-model="recordForm.only_missing">
          仅录制尚未包含 <code class="ev-mono">sut_context_json</code> 快照的缺失样本
        </el-checkbox>
      </el-form>
      <template #footer>
        <div class="ev-drawer-footer">
          <el-button @click="recordDrawerOpen = false">取消</el-button>
          <el-button type="primary" :loading="recordingSnapshots" @click="handleRecordSnapshots">
            开始录制冻结快照
          </el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.ev-cell-primary {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ev-cell-secondary {
  font-size: var(--ev-fs-label);
  color: var(--ev-text-secondary);
  margin-top: 2px;
}

.ev-ver-chip {
  padding: 1px 6px;
  border-radius: var(--ev-radius-sm);
  font-size: var(--ev-fs-label);
  background-color: var(--ev-bg-panel);
  border: 1px solid var(--ev-border-subtle);
  color: var(--ev-text-secondary);
}

.ev-split-counts {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.ev-split-pair {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.ev-row-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.ev-grid-2 {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.ev-grid-3 {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.ev-notice-mb {
  margin-bottom: 14px;
  font-size: var(--ev-fs-label);
  color: var(--ev-text-secondary);
  line-height: 1.6;
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
