<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Connection,
  CopyDocument,
  Key,
  Plus,
  Refresh,
  Setting,
} from '@element-plus/icons-vue'
import { adaptersApi, authApi, providersApi } from '@/api/endpoints'
import { formatReadableError } from '@/api/http'
import type {
  AdapterTestResult,
  AdapterType,
  AdapterUpsertPayload,
  ApiKeyRecord,
  DecisionProviderRecord,
  EvalLevel,
  ProviderAuthType,
  ProviderKind,
  ProviderNetworkMode,
  ProviderProbeResult,
  ProviderUpsertPayload,
  SutAdapterRecord,
} from '@/api/types'
import DualStatusBadge from '@/components/DualStatusBadge.vue'
import EmptyStateBox from '@/components/EmptyStateBox.vue'

const activeTab = ref<'providers' | 'adapters' | 'keys'>('providers')
const loading = ref(false)
const errorBanner = ref<string | null>(null)

const providers = ref<DecisionProviderRecord[]>([])
const adapters = ref<SutAdapterRecord[]>([])
const apiKeys = ref<ApiKeyRecord[]>([])

/* ---- Provider state ---- */
const providerFilterKind = ref<string>('')
const probingCode = ref<string | null>(null)
const fetchingModelsCode = ref<string | null>(null)
const probeResults = reactive<Record<string, ProviderProbeResult>>({})
const fetchedModels = reactive<Record<string, string[]>>({})

const providerDrawerOpen = ref(false)
const savingProvider = ref(false)
const isEditingProvider = ref(false)
const providerForm = reactive<ProviderUpsertPayload>({
  provider_code: '',
  provider_name: '',
  provider_kind: 'OLLAMA_LOCAL',
  endpoint_url: 'http://127.0.0.1:11434/v1/systemone',
  models_list_url: 'http://127.0.0.1:11434/v1/models',
  default_model: 'tev1:0.8b',
  auth_type: 'NONE',
  api_key_plain: '',
  api_key_env_name: '',
  network_mode: 'DIRECT',
  custom_proxy_url: '',
  timeout_seconds: 90,
  max_concurrency: 8,
  max_questions_per_call: 64,
  max_options_per_question: 24,
  waf_strip_code_retry: false,
  cost_per_1m_input_tokens: 0,
  is_default: false,
  status: 'ACTIVE',
})

/* ---- Adapter state ---- */
const adapterFilterLevel = ref<string>('')
const adapterDrawerOpen = ref(false)
const savingAdapter = ref(false)
const adapterForm = reactive<{
  adapter_code: string
  adapter_name: string
  eval_level: EvalLevel
  adapter_type: AdapterType
  endpoint_url: string
  record_endpoint_url: string
  auth_json_text: string
  request_mapping_text: string
  response_mapping_text: string
  timeout_seconds: number
  max_concurrency: number
  status: 'ACTIVE' | 'DISABLED'
  description: string
}>({
  adapter_code: '',
  adapter_name: '',
  eval_level: 'E2E',
  adapter_type: 'HTTP_JSON',
  endpoint_url: 'http://127.0.0.1:8080/ai/invoke',
  record_endpoint_url: '',
  auth_json_text: '{\n  "header": "Authorization",\n  "prefix": "Bearer ",\n  "env_name": "EVAL_JAVA_BEARER_TOKEN"\n}',
  request_mapping_text: '{\n  "body_template": { "responseMode": "blocking" },\n  "field_map": { "query": "$.input_text" }\n}',
  response_mapping_text: '{\n  "actual_output": "$.data.answer",\n  "actual_references": "$.data.references[*].content"\n}',
  timeout_seconds: 60,
  max_concurrency: 4,
  status: 'ACTIVE',
  description: '',
})

const debugDrawerOpen = ref(false)
const debugTargetAdapter = ref<SutAdapterRecord | null>(null)
const runningAdapterDebug = ref(false)
const debugResult = ref<AdapterTestResult | null>(null)
const debugError = ref<string | null>(null)
const debugForm = reactive({
  input_text: '你好，请介绍常州住房公积金咨询渠道与办理材料。',
  sut_input_text: '{\n  "selected_region": "天宁区"\n}',
  sut_context_text: '',
  run_overrides_text: '',
})

/* ---- API Key state ---- */
const keyDialogOpen = ref(false)
const creatingKey = ref(false)
const revokingKeyId = ref<number | null>(null)
const revokeConfirmTarget = ref<ApiKeyRecord | null>(null)
const newlyIssuedPlainKey = ref<string | null>(null)
const keyForm = reactive({
  key_name: '',
  rate_limit_rpm: 300,
  expires_in_days: 90 as number | null,
})

const filteredProviders = computed(() => {
  if (!providerFilterKind.value) return providers.value
  return providers.value.filter((p) => p.provider_kind === providerFilterKind.value)
})

const filteredAdapters = computed(() => {
  if (!adapterFilterLevel.value) return adapters.value
  return adapters.value.filter((a) => a.eval_level === adapterFilterLevel.value)
})

function providerKindLabel(kind: ProviderKind): string {
  if (kind === 'OLLAMA_LOCAL') return '本地决策模型 (Ollama)'
  if (kind === 'DECISION_RELAY') return '第三方决策中转站'
  return 'TypeSafe 官方云端'
}

function networkModeLabel(mode: ProviderNetworkMode): string {
  if (mode === 'DIRECT') return 'DIRECT 直连'
  if (mode === 'PROXY_FALLBACK') return 'PROXY_FALLBACK 代理回退链'
  return 'CUSTOM_PROXY 自定义代理'
}

async function loadAll(): Promise<void> {
  loading.value = true
  errorBanner.value = null
  try {
    const [p, a, k] = await Promise.all([
      providersApi.list(),
      adaptersApi.list(),
      authApi.listApiKeys(),
    ])
    providers.value = p
    adapters.value = a
    apiKeys.value = k
  } catch (err) {
    errorBanner.value = formatReadableError(err)
  } finally {
    loading.value = false
  }
}

function applyProviderPreset(kind: ProviderKind): void {
  providerForm.provider_kind = kind
  if (kind === 'OLLAMA_LOCAL') {
    providerForm.endpoint_url = 'http://127.0.0.1:11434/v1/systemone'
    providerForm.models_list_url = 'http://127.0.0.1:11434/v1/models'
    providerForm.default_model = 'tev1:0.8b'
    providerForm.auth_type = 'NONE'
    providerForm.network_mode = 'DIRECT'
    providerForm.waf_strip_code_retry = false
    providerForm.cost_per_1m_input_tokens = 0
  } else if (kind === 'DECISION_RELAY') {
    providerForm.endpoint_url = 'https://emrank.tech/v1/decisions'
    providerForm.models_list_url = 'https://emrank.tech/v1/models'
    providerForm.default_model = 'typesafe/jev-1.14.0'
    providerForm.auth_type = 'BEARER'
    providerForm.network_mode = 'PROXY_FALLBACK'
    providerForm.waf_strip_code_retry = true
    providerForm.cost_per_1m_input_tokens = 0.2
  } else {
    providerForm.endpoint_url = 'https://api.typesafe.ai/v1/systemone'
    providerForm.models_list_url = ''
    providerForm.default_model = 'jev-1.14.0'
    providerForm.auth_type = 'BEARER'
    providerForm.network_mode = 'PROXY_FALLBACK'
    providerForm.waf_strip_code_retry = true
    providerForm.cost_per_1m_input_tokens = 0.2
  }
}

function openCreateProvider(): void {
  isEditingProvider.value = false
  providerForm.provider_code = ''
  providerForm.provider_name = ''
  providerForm.api_key_plain = ''
  providerForm.api_key_env_name = ''
  providerForm.is_default = false
  providerForm.status = 'ACTIVE'
  applyProviderPreset('OLLAMA_LOCAL')
  providerDrawerOpen.value = true
}

function openEditProvider(row: DecisionProviderRecord): void {
  isEditingProvider.value = true
  providerForm.provider_code = row.provider_code
  providerForm.provider_name = row.provider_name
  providerForm.provider_kind = row.provider_kind
  providerForm.endpoint_url = row.endpoint_url
  providerForm.models_list_url = row.models_list_url || ''
  providerForm.default_model = row.default_model
  providerForm.auth_type = row.auth_type as ProviderAuthType
  providerForm.api_key_plain = ''
  providerForm.api_key_env_name = row.api_key_env_name || ''
  providerForm.network_mode = row.network_mode
  providerForm.custom_proxy_url = row.custom_proxy_url || ''
  providerForm.timeout_seconds = row.timeout_seconds
  providerForm.max_concurrency = row.max_concurrency
  providerForm.max_questions_per_call = row.max_questions_per_call
  providerForm.max_options_per_question = row.max_options_per_question
  providerForm.waf_strip_code_retry = row.waf_strip_code_retry
  providerForm.cost_per_1m_input_tokens = row.cost_per_1m_input_tokens
  providerForm.is_default = row.is_default
  providerForm.status = row.status
  providerDrawerOpen.value = true
}

async function handleSaveProvider(): Promise<void> {
  if (!providerForm.provider_code.trim() || !providerForm.provider_name.trim()) {
    ElMessage.warning('请完整填写渠道编码与渠道名称')
    return
  }
  savingProvider.value = true
  try {
    await providersApi.upsert({
      ...providerForm,
      models_list_url: providerForm.models_list_url?.trim() || null,
      api_key_plain: providerForm.api_key_plain?.trim() || null,
      api_key_env_name: providerForm.api_key_env_name?.trim() || null,
      custom_proxy_url: providerForm.custom_proxy_url?.trim() || null,
    })
    ElMessage.success('判别模型渠道已保存')
    providerDrawerOpen.value = false
    providers.value = await providersApi.list()
  } catch (err) {
    ElMessage.error(formatReadableError(err))
  } finally {
    savingProvider.value = false
  }
}

async function handleProbeProvider(row: DecisionProviderRecord): Promise<void> {
  probingCode.value = row.provider_code
  try {
    const res = await providersApi.probe(row.provider_code)
    probeResults[row.provider_code] = res
    providers.value = await providersApi.list()
    if (res.ok) {
      ElMessage.success(`渠道连通正常，耗时 ${res.latency_ms} ms`)
    } else {
      ElMessage.warning(res.error || '渠道连通性探活未通过')
    }
  } catch (err) {
    ElMessage.error(formatReadableError(err))
  } finally {
    probingCode.value = null
  }
}

async function handleFetchModels(row: DecisionProviderRecord): Promise<void> {
  fetchingModelsCode.value = row.provider_code
  try {
    const res = await providersApi.fetchModels(row.provider_code)
    fetchedModels[row.provider_code] = res.models
    ElMessage.success(`已拉取 ${res.models.length} 个可用模型标识`)
  } catch (err) {
    ElMessage.error(formatReadableError(err))
  } finally {
    fetchingModelsCode.value = null
  }
}

/* ---- Adapter methods ---- */
function openCreateAdapter(): void {
  adapterForm.adapter_code = ''
  adapterForm.adapter_name = ''
  adapterForm.eval_level = 'E2E'
  adapterForm.adapter_type = 'HTTP_JSON'
  adapterForm.endpoint_url = 'http://127.0.0.1:8080/ai/invoke'
  adapterForm.record_endpoint_url = ''
  adapterForm.status = 'ACTIVE'
  adapterForm.description = ''
  adapterDrawerOpen.value = true
}

function openEditAdapter(row: SutAdapterRecord): void {
  adapterForm.adapter_code = row.adapter_code
  adapterForm.adapter_name = row.adapter_name
  adapterForm.eval_level = row.eval_level
  adapterForm.adapter_type = row.adapter_type
  adapterForm.endpoint_url = row.endpoint_url
  adapterForm.record_endpoint_url = row.record_endpoint_url || ''
  adapterForm.auth_json_text = row.auth_config_json
    ? JSON.stringify(row.auth_config_json, null, 2)
    : ''
  adapterForm.request_mapping_text = JSON.stringify(row.request_mapping_json || {}, null, 2)
  adapterForm.response_mapping_text = JSON.stringify(row.response_mapping_json || {}, null, 2)
  adapterForm.timeout_seconds = row.timeout_seconds
  adapterForm.max_concurrency = row.max_concurrency
  adapterForm.status = row.status
  adapterForm.description = row.description || ''
  adapterDrawerOpen.value = true
}

async function handleSaveAdapter(): Promise<void> {
  if (!adapterForm.adapter_code.trim() || !adapterForm.adapter_name.trim()) {
    ElMessage.warning('请填写被测适配器编码与名称')
    return
  }
  let authObj: Record<string, unknown> | null = null
  let reqMap: Record<string, unknown> = {}
  let respMap: Record<string, unknown> = {}
  try {
    if (adapterForm.auth_json_text.trim()) {
      authObj = JSON.parse(adapterForm.auth_json_text) as Record<string, unknown>
    }
    reqMap = JSON.parse(adapterForm.request_mapping_text || '{}') as Record<string, unknown>
    respMap = JSON.parse(adapterForm.response_mapping_text || '{}') as Record<string, unknown>
  } catch {
    ElMessage.error('鉴权或 JSONPath 映射配置不是合法的 JSON 对象')
    return
  }

  const payload: AdapterUpsertPayload = {
    adapter_code: adapterForm.adapter_code.trim(),
    adapter_name: adapterForm.adapter_name.trim(),
    eval_level: adapterForm.eval_level,
    adapter_type: adapterForm.adapter_type,
    endpoint_url: adapterForm.endpoint_url.trim(),
    record_endpoint_url: adapterForm.record_endpoint_url.trim() || null,
    auth_config_json: authObj,
    request_mapping_json: reqMap,
    response_mapping_json: respMap,
    timeout_seconds: adapterForm.timeout_seconds,
    max_concurrency: adapterForm.max_concurrency,
    status: adapterForm.status,
    description: adapterForm.description.trim() || null,
  }

  savingAdapter.value = true
  try {
    await adaptersApi.upsert(payload)
    ElMessage.success('被测适配器已保存')
    adapterDrawerOpen.value = false
    adapters.value = await adaptersApi.list()
  } catch (err) {
    ElMessage.error(formatReadableError(err))
  } finally {
    savingAdapter.value = false
  }
}

function openDebugAdapter(row: SutAdapterRecord): void {
  debugTargetAdapter.value = row
  debugResult.value = null
  debugError.value = null
  debugDrawerOpen.value = true
}

async function handleRunAdapterTest(): Promise<void> {
  const target = debugTargetAdapter.value
  if (!target) return
  debugError.value = null
  debugResult.value = null

  let sutInput: Record<string, unknown> | null = null
  let sutContext: Record<string, unknown> | null = null
  let overrides: Record<string, unknown> | null = null
  try {
    if (debugForm.sut_input_text.trim()) {
      sutInput = JSON.parse(debugForm.sut_input_text) as Record<string, unknown>
    }
    if (debugForm.sut_context_text.trim()) {
      sutContext = JSON.parse(debugForm.sut_context_text) as Record<string, unknown>
    }
    if (debugForm.run_overrides_text.trim()) {
      overrides = JSON.parse(debugForm.run_overrides_text) as Record<string, unknown>
    }
  } catch {
    debugError.value = '调试扩展 JSON 格式不合法，请检查语法'
    return
  }

  runningAdapterDebug.value = true
  try {
    debugResult.value = await adaptersApi.test(target.adapter_code, {
      input_text: debugForm.input_text,
      sut_input_json: sutInput,
      sut_context_json: sutContext,
      run_overrides: overrides,
    })
  } catch (err) {
    debugError.value = formatReadableError(err)
  } finally {
    runningAdapterDebug.value = false
  }
}

/* ---- API Key methods ---- */
function openIssueKeyDialog(): void {
  keyForm.key_name = ''
  keyForm.rate_limit_rpm = 300
  keyForm.expires_in_days = 90
  newlyIssuedPlainKey.value = null
  keyDialogOpen.value = true
}

async function handleCreateApiKey(): Promise<void> {
  if (!keyForm.key_name.trim()) {
    ElMessage.warning('请输入密钥用途名称')
    return
  }
  creatingKey.value = true
  try {
    const res = await authApi.createApiKey({
      key_name: keyForm.key_name.trim(),
      rate_limit_rpm: keyForm.rate_limit_rpm,
      expires_in_days: keyForm.expires_in_days,
    })
    newlyIssuedPlainKey.value = res.api_key_plain || null
    apiKeys.value = await authApi.listApiKeys()
    ElMessage.success('自动化密钥已签发，请立即复制保存明文')
  } catch (err) {
    ElMessage.error(formatReadableError(err))
  } finally {
    creatingKey.value = false
  }
}

async function copyPlainKey(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('密钥已复制到剪贴板')
  } catch {
    ElMessage.info('请手动选中并复制密钥文本')
  }
}

function promptRevokeKey(row: ApiKeyRecord): void {
  revokeConfirmTarget.value = row
}

async function confirmRevokeKey(): Promise<void> {
  const row = revokeConfirmTarget.value
  if (!row) return
  revokingKeyId.value = row.id
  try {
    await authApi.revokeApiKey(row.id)
    revokeConfirmTarget.value = null
    apiKeys.value = await authApi.listApiKeys()
    ElMessage.success('密钥已吊销')
  } catch (err) {
    ElMessage.error(formatReadableError(err))
  } finally {
    revokingKeyId.value = null
  }
}

onMounted(() => {
  void loadAll()
})
</script>

<template>
  <div class="ev-page">
    <div class="ev-page-header">
      <div>
        <h1 class="ev-page-title">渠道与适配器中心</h1>
        <p class="ev-page-subtitle">
          统一管理 System One 判别模型渠道、可插拔被测适配器（E2E / 组件回放）与自动化流水线密钥
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

    <el-tabs v-model="activeTab" class="ev-tabs">
      <!-- 1. Decision Providers -->
      <el-tab-pane label="判别模型渠道" name="providers">
        <div class="ev-filter-bar">
          <div class="ev-filter-bar__left">
            <el-select
              v-model="providerFilterKind"
              placeholder="全部渠道类型"
              clearable
              style="width: 210px"
            >
              <el-option label="本地决策模型 (Ollama)" value="OLLAMA_LOCAL" />
              <el-option label="第三方决策中转站" value="DECISION_RELAY" />
              <el-option label="TypeSafe 官方云端" value="TYPESAFE_OFFICIAL" />
            </el-select>
            <el-button @click="providerFilterKind = ''">重置</el-button>
            <el-button :icon="Refresh" @click="loadAll">刷新</el-button>
          </div>
          <div class="ev-filter-bar__right">
            <el-button type="primary" :icon="Plus" @click="openCreateProvider">
              新增判别模型渠道
            </el-button>
          </div>
        </div>

        <div class="ev-surface ev-section-mt">
          <el-skeleton v-if="loading" :rows="5" animated />
          <EmptyStateBox
            v-else-if="filteredProviders.length === 0"
            :filtered="Boolean(providerFilterKind)"
            entity-name="判别模型渠道"
            action-label="新增判别模型渠道"
            @reset="providerFilterKind = ''"
            @action="openCreateProvider"
          />
          <el-table v-else :data="filteredProviders" stripe style="width: 100%">
            <el-table-column label="渠道标识 / 名称" min-width="230" fixed="left">
              <template #default="{ row }">
                <div class="ev-cell-primary">
                  <span class="ev-mono">{{ row.provider_code }}</span>
                  <el-tag v-if="row.is_default" size="small" effect="plain">默认标尺</el-tag>
                </div>
                <div class="ev-cell-secondary">{{ row.provider_name }}</div>
              </template>
            </el-table-column>

            <el-table-column label="端点类型与网络策略" min-width="240">
              <template #default="{ row }">
                <div>{{ providerKindLabel(row.provider_kind) }}</div>
                <div class="ev-cell-secondary ev-mono">
                  {{ networkModeLabel(row.network_mode) }}
                  <span v-if="row.waf_strip_code_retry"> · WAF 自愈开启</span>
                </div>
              </template>
            </el-table-column>

            <el-table-column label="默认判别模型与端点" min-width="240">
              <template #default="{ row }">
                <div class="ev-mono">{{ row.default_model }}</div>
                <div class="ev-cell-secondary ev-mono">{{ row.endpoint_url }}</div>
                <div
                  v-if="fetchedModels[row.provider_code]?.length"
                  class="ev-models-inline ev-mono"
                >
                  可用模型 ({{ fetchedModels[row.provider_code].length }}):
                  {{ fetchedModels[row.provider_code].slice(0, 4).join(', ') }}
                </div>
              </template>
            </el-table-column>

            <el-table-column label="连通探活状态" min-width="200">
              <template #default="{ row }">
                <DualStatusBadge
                  v-if="row.last_health_ok === true"
                  tone="success"
                  symbol="✓"
                  label="探活正常"
                  :value="
                    probeResults[row.provider_code]
                      ? `${probeResults[row.provider_code].latency_ms} ms`
                      : null
                  "
                />
                <DualStatusBadge
                  v-else-if="row.last_health_ok === false"
                  tone="danger"
                  symbol="✕"
                  label="探活异常"
                />
                <DualStatusBadge v-else tone="neutral" symbol="!" label="尚未探活" />
                <div
                  v-if="probeResults[row.provider_code]?.probability_sample"
                  class="ev-cell-secondary ev-mono"
                >
                  ping p_true={{
                    probeResults[row.provider_code].probability_sample?.p_true.toFixed(4)
                  }}
                  · conf={{
                    probeResults[row.provider_code].probability_sample?.confidence.toFixed(4)
                  }}
                </div>
                <div
                  v-else-if="probeResults[row.provider_code]?.error"
                  class="ev-probe-error"
                >
                  {{ probeResults[row.provider_code].error }}
                </div>
              </template>
            </el-table-column>

            <el-table-column label="合批上限 / 成本" width="160" align="right">
              <template #default="{ row }">
                <div class="ev-mono">≤ {{ row.max_questions_per_call }} 题/次</div>
                <div class="ev-cell-secondary ev-mono">
                  ${{ Number(row.cost_per_1m_input_tokens).toFixed(2) }} / 1M
                </div>
              </template>
            </el-table-column>

            <el-table-column label="操作" width="250" fixed="right">
              <template #default="{ row }">
                <div class="ev-row-actions">
                  <el-button
                    size="small"
                    :icon="Connection"
                    :loading="probingCode === row.provider_code"
                    @click="handleProbeProvider(row)"
                  >
                    测试渠道连通性
                  </el-button>
                  <el-button
                    size="small"
                    :loading="fetchingModelsCode === row.provider_code"
                    @click="handleFetchModels(row)"
                  >
                    拉取模型
                  </el-button>
                  <el-button size="small" link @click="openEditProvider(row)">编辑</el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <!-- 2. SUT Adapters -->
      <el-tab-pane label="被测适配器 (SUT Adapter)" name="adapters">
        <div class="ev-filter-bar">
          <div class="ev-filter-bar__left">
            <el-select
              v-model="adapterFilterLevel"
              placeholder="全部评测层级"
              clearable
              style="width: 180px"
            >
              <el-option label="E2E 端到端黑盒" value="E2E" />
              <el-option label="COMPONENT 组件回放" value="COMPONENT" />
            </el-select>
            <el-button @click="adapterFilterLevel = ''">重置</el-button>
          </div>
          <div class="ev-filter-bar__right">
            <el-button type="primary" :icon="Plus" @click="openCreateAdapter">
              新增被测适配器
            </el-button>
          </div>
        </div>

        <div class="ev-surface ev-section-mt">
          <el-skeleton v-if="loading" :rows="4" animated />
          <EmptyStateBox
            v-else-if="filteredAdapters.length === 0"
            :filtered="Boolean(adapterFilterLevel)"
            entity-name="被测适配器"
            action-label="新增被测适配器"
            @reset="adapterFilterLevel = ''"
            @action="openCreateAdapter"
          />
          <el-table v-else :data="filteredAdapters" stripe style="width: 100%">
            <el-table-column label="适配器编码 / 名称" min-width="230" fixed="left">
              <template #default="{ row }">
                <div class="ev-mono">{{ row.adapter_code }}</div>
                <div class="ev-cell-secondary">{{ row.adapter_name }}</div>
              </template>
            </el-table-column>

            <el-table-column label="评测层级 / 协议类型" width="200">
              <template #default="{ row }">
                <DualStatusBadge
                  :tone="row.eval_level === 'COMPONENT' ? 'warning' : 'neutral'"
                  :symbol="row.eval_level === 'COMPONENT' ? '!' : '✓'"
                  :label="row.eval_level === 'COMPONENT' ? '组件隔离回放' : '端到端 E2E'"
                  :value="row.adapter_type"
                />
              </template>
            </el-table-column>

            <el-table-column label="调用端点与录制端点" min-width="280">
              <template #default="{ row }">
                <div class="ev-mono">{{ row.endpoint_url }}</div>
                <div v-if="row.record_endpoint_url" class="ev-cell-secondary ev-mono">
                  Record: {{ row.record_endpoint_url }}
                </div>
              </template>
            </el-table-column>

            <el-table-column label="超时 / 并发" width="140" align="right">
              <template #default="{ row }">
                <span class="ev-mono">{{ row.timeout_seconds }} s · {{ row.max_concurrency }}x</span>
              </template>
            </el-table-column>

            <el-table-column label="状态" width="120">
              <template #default="{ row }">
                <DualStatusBadge
                  :tone="row.status === 'ACTIVE' ? 'success' : 'neutral'"
                  :symbol="row.status === 'ACTIVE' ? '✓' : '!'"
                  :label="row.status === 'ACTIVE' ? '已启用' : '已停用'"
                />
              </template>
            </el-table-column>

            <el-table-column label="操作" width="210" fixed="right">
              <template #default="{ row }">
                <div class="ev-row-actions">
                  <el-button size="small" :icon="Setting" @click="openDebugAdapter(row)">
                    映射调试
                  </el-button>
                  <el-button size="small" link @click="openEditAdapter(row)">编辑配置</el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <!-- 3. Automation API Keys -->
      <el-tab-pane label="自动化密钥 (evk_*)" name="keys">
        <div class="ev-filter-bar">
          <div class="ev-filter-bar__left">
            <span class="ev-cell-secondary">
              供 CI/CD 流水线通过请求头 <code class="ev-mono">X-API-Key: evk_live_...</code> 触发评测与导入语料
            </span>
          </div>
          <div class="ev-filter-bar__right">
            <el-button type="primary" :icon="Key" @click="openIssueKeyDialog">
              签发自动化密钥
            </el-button>
          </div>
        </div>

        <div class="ev-surface ev-section-mt">
          <EmptyStateBox
            v-if="apiKeys.length === 0"
            entity-name="自动化密钥"
            action-label="签发自动化密钥"
            @action="openIssueKeyDialog"
          />
          <el-table v-else :data="apiKeys" stripe style="width: 100%">
            <el-table-column label="密钥名称" prop="key_name" min-width="200" />
            <el-table-column label="前缀标识" width="180">
              <template #default="{ row }">
                <span class="ev-mono">{{ row.key_prefix }}...</span>
              </template>
            </el-table-column>
            <el-table-column label="限流配额" width="150" align="right">
              <template #default="{ row }">
                <span class="ev-mono">{{ row.rate_limit_rpm }} RPM</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="140">
              <template #default="{ row }">
                <DualStatusBadge
                  :tone="row.status === 'ACTIVE' ? 'success' : 'neutral'"
                  :symbol="row.status === 'ACTIVE' ? '✓' : '✕'"
                  :label="row.status === 'ACTIVE' ? '生效中' : '已吊销'"
                />
              </template>
            </el-table-column>
            <el-table-column label="过期时间" min-width="180">
              <template #default="{ row }">
                <span class="ev-mono">{{ row.expires_at || '永不过期' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="130" fixed="right">
              <template #default="{ row }">
                <el-button
                  v-if="row.status === 'ACTIVE'"
                  size="small"
                  type="danger"
                  plain
                  @click="promptRevokeKey(row)"
                >
                  吊销密钥
                </el-button>
                <span v-else class="ev-cell-secondary">不可恢复</span>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- Provider Upsert Drawer -->
    <el-drawer
      v-model="providerDrawerOpen"
      :title="isEditingProvider ? '编辑判别模型渠道' : '新增判别模型渠道'"
      size="600px"
    >
      <div class="ev-preset-bar ev-panel">
        <span class="ev-cell-secondary">快速填充预设：</span>
        <el-button size="small" @click="applyProviderPreset('OLLAMA_LOCAL')">
          本地 Ollama tev1
        </el-button>
        <el-button size="small" @click="applyProviderPreset('DECISION_RELAY')">
          EMRank 决策中转
        </el-button>
        <el-button size="small" @click="applyProviderPreset('TYPESAFE_OFFICIAL')">
          TypeSafe 官方
        </el-button>
      </div>

      <el-form label-position="top" class="ev-section-mt">
        <div class="ev-grid-2">
          <el-form-item label="渠道编码 (provider_code)" required>
            <el-input
              v-model="providerForm.provider_code"
              :disabled="isEditingProvider"
              placeholder="如 ollama_local_tev1"
            />
          </el-form-item>
          <el-form-item label="渠道名称" required>
            <el-input v-model="providerForm.provider_name" placeholder="如 本地 Ollama tev1:0.8b" />
          </el-form-item>
        </div>

        <div class="ev-grid-2">
          <el-form-item label="渠道类型 (provider_kind)" required>
            <el-select v-model="providerForm.provider_kind" style="width: 100%">
              <el-option label="OLLAMA_LOCAL (本地决策模型)" value="OLLAMA_LOCAL" />
              <el-option label="DECISION_RELAY (第三方中转)" value="DECISION_RELAY" />
              <el-option label="TYPESAFE_OFFICIAL (官方云端)" value="TYPESAFE_OFFICIAL" />
            </el-select>
          </el-form-item>
          <el-form-item label="默认锁定模型名 (default_model)" required>
            <el-input v-model="providerForm.default_model" placeholder="如 tev1:0.8b 或 typesafe/jev-1.14.0" />
          </el-form-item>
        </div>

        <el-form-item label="决策请求端点 (endpoint_url)" required>
          <el-input v-model="providerForm.endpoint_url" placeholder="http://127.0.0.1:11434/v1/systemone" />
        </el-form-item>

        <el-form-item label="模型列表端点 (models_list_url，可选)">
          <el-input v-model="providerForm.models_list_url" placeholder="https://emrank.tech/v1/models" />
        </el-form-item>

        <div class="ev-grid-2">
          <el-form-item label="鉴权方式">
            <el-select v-model="providerForm.auth_type" style="width: 100%">
              <el-option label="NONE (无鉴权)" value="NONE" />
              <el-option label="BEARER (加密存储密钥)" value="BEARER" />
              <el-option label="ENV_REF (读取服务端环境变量)" value="ENV_REF" />
            </el-select>
          </el-form-item>
          <el-form-item label="网络代理策略">
            <el-select v-model="providerForm.network_mode" style="width: 100%">
              <el-option label="DIRECT (强制直连)" value="DIRECT" />
              <el-option label="PROXY_FALLBACK (10808 -> 7897 -> 直连)" value="PROXY_FALLBACK" />
              <el-option label="CUSTOM_PROXY (指定代理地址)" value="CUSTOM_PROXY" />
            </el-select>
          </el-form-item>
        </div>

        <el-form-item v-if="providerForm.auth_type === 'BEARER'" label="API Key 明文 (留空则保持已存密文不变)">
          <el-input v-model="providerForm.api_key_plain" type="password" show-password placeholder="sk-..." />
        </el-form-item>

        <el-form-item v-if="providerForm.auth_type === 'ENV_REF'" label="服务端环境变量名">
          <el-input v-model="providerForm.api_key_env_name" placeholder="如 EMRANK_API_KEY" />
        </el-form-item>

        <el-form-item v-if="providerForm.network_mode === 'CUSTOM_PROXY'" label="自定义代理地址">
          <el-input v-model="providerForm.custom_proxy_url" placeholder="http://127.0.0.1:10808" />
        </el-form-item>

        <div class="ev-grid-2">
          <el-form-item label="单次合批最大题数">
            <el-input-number v-model="providerForm.max_questions_per_call" :min="1" :max="128" />
          </el-form-item>
          <el-form-item label="每百万输入 Token 成本 (USD)">
            <el-input-number
              v-model="providerForm.cost_per_1m_input_tokens"
              :min="0"
              :step="0.05"
              :precision="2"
            />
          </el-form-item>
        </div>

        <div class="ev-switch-row">
          <el-checkbox v-model="providerForm.waf_strip_code_retry">
            启用边缘防火墙 HTTP 403 剥离围栏代码块自动重试
          </el-checkbox>
          <el-checkbox v-model="providerForm.is_default">设为平台默认判别渠道</el-checkbox>
        </div>
      </el-form>

      <template #footer>
        <div class="ev-drawer-footer">
          <el-button @click="providerDrawerOpen = false">取消</el-button>
          <el-button type="primary" :loading="savingProvider" @click="handleSaveProvider">
            保存判别模型渠道
          </el-button>
        </div>
      </template>
    </el-drawer>

    <!-- Adapter Upsert Drawer -->
    <el-drawer v-model="adapterDrawerOpen" title="配置被测适配器" size="640px">
      <el-form label-position="top">
        <div class="ev-grid-2">
          <el-form-item label="适配器编码 (adapter_code)" required>
            <el-input v-model="adapterForm.adapter_code" placeholder="如 gov_e2e_routing_blocking" />
          </el-form-item>
          <el-form-item label="适配器名称" required>
            <el-input v-model="adapterForm.adapter_name" placeholder="如 政务智能客服路由总入口" />
          </el-form-item>
        </div>

        <div class="ev-grid-2">
          <el-form-item label="评测层级 (eval_level)">
            <el-select v-model="adapterForm.eval_level" style="width: 100%">
              <el-option label="E2E 端到端接口" value="E2E" />
              <el-option label="COMPONENT 内部组件回放" value="COMPONENT" />
            </el-select>
          </el-form-item>
          <el-form-item label="适配器协议类型">
            <el-select v-model="adapterForm.adapter_type" style="width: 100%">
              <el-option label="HTTP_JSON (阻塞式 JSON)" value="HTTP_JSON" />
              <el-option label="HTTP_SSE (流式采集 TTFT)" value="HTTP_SSE" />
              <el-option label="COMPONENT_HARNESS (隔离回放网关)" value="COMPONENT_HARNESS" />
            </el-select>
          </el-form-item>
        </div>

        <el-form-item label="调用/回放端点 (endpoint_url)" required>
          <el-input v-model="adapterForm.endpoint_url" />
        </el-form-item>

        <el-form-item
          v-if="adapterForm.adapter_type === 'COMPONENT_HARNESS'"
          label="组件快照录制端点 (record_endpoint_url)"
        >
          <el-input
            v-model="adapterForm.record_endpoint_url"
            placeholder="http://127.0.0.1:8000/internal/eval-harness/record"
          />
        </el-form-item>

        <el-form-item label="鉴权配置 JSON (auth_config_json)">
          <el-input v-model="adapterForm.auth_json_text" type="textarea" :rows="3" class="ev-mono" />
        </el-form-item>

        <el-form-item label="入参 JSONPath 映射 (request_mapping_json)">
          <el-input
            v-model="adapterForm.request_mapping_text"
            type="textarea"
            :rows="4"
            class="ev-mono"
          />
        </el-form-item>

        <el-form-item label="出参 JSONPath 抽取映射 (response_mapping_json)">
          <el-input
            v-model="adapterForm.response_mapping_text"
            type="textarea"
            :rows="4"
            class="ev-mono"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <div class="ev-drawer-footer">
          <el-button @click="adapterDrawerOpen = false">取消</el-button>
          <el-button type="primary" :loading="savingAdapter" @click="handleSaveAdapter">
            保存被测适配器
          </el-button>
        </div>
      </template>
    </el-drawer>

    <!-- Adapter JSONPath Mapping Debug Drawer -->
    <el-drawer
      v-model="debugDrawerOpen"
      :title="`被测适配器映射联调 · ${debugTargetAdapter?.adapter_code || ''}`"
      size="680px"
    >
      <el-form label-position="top">
        <el-form-item label="模拟输入文本 (input_text)">
          <el-input v-model="debugForm.input_text" type="textarea" :rows="2" />
        </el-form-item>
        <div class="ev-grid-2">
          <el-form-item label="扩展入参 JSON (sut_input_json)">
            <el-input v-model="debugForm.sut_input_text" type="textarea" :rows="3" class="ev-mono" />
          </el-form-item>
          <el-form-item label="运行参数覆盖 JSON (run_overrides)">
            <el-input
              v-model="debugForm.run_overrides_text"
              type="textarea"
              :rows="3"
              class="ev-mono"
              placeholder='{"confidence_threshold": 0.72}'
            />
          </el-form-item>
        </div>
        <el-form-item
          v-if="debugTargetAdapter?.adapter_type === 'COMPONENT_HARNESS'"
          label="冻结组件快照 JSON (sut_context_json)"
        >
          <el-input v-model="debugForm.sut_context_text" type="textarea" :rows="3" class="ev-mono" />
        </el-form-item>

        <el-button type="primary" :loading="runningAdapterDebug" @click="handleRunAdapterTest">
          发送联调请求并验证抽取
        </el-button>
      </el-form>

      <el-alert
        v-if="debugError"
        :title="debugError"
        type="error"
        show-icon
        :closable="false"
        class="ev-section-mt"
      />

      <div v-if="debugResult" class="ev-panel ev-section-mt">
        <div class="ev-debug-metrics">
          <DualStatusBadge
            tone="success"
            symbol="✓"
            label="联调响应成功"
            :value="`${debugResult.sut_latency_ms ?? 0} ms`"
          />
          <span v-if="debugResult.ttft_ms !== null" class="ev-mono">
            TTFT: {{ debugResult.ttft_ms }} ms
          </span>
        </div>
        <h4 class="ev-sub-heading">抽取后 actual_output</h4>
        <div class="ev-prose">{{ debugResult.actual_output || '（空）' }}</div>
        <h4 class="ev-sub-heading">组装请求预览 (request_preview)</h4>
        <pre class="ev-code-block ev-mono">{{ JSON.stringify(debugResult.request_preview, null, 2) }}</pre>
        <h4 class="ev-sub-heading">结构化决策抽取 (actual_structured)</h4>
        <pre class="ev-code-block ev-mono">{{ JSON.stringify(debugResult.actual_structured, null, 2) }}</pre>
      </div>
    </el-drawer>

    <!-- Issue API Key Dialog -->
    <el-dialog v-model="keyDialogOpen" title="签发自动化评测密钥" width="500px">
      <div v-if="newlyIssuedPlainKey" class="ev-panel">
        <p class="ev-key-once-notice">
          密钥明文仅在本次签发时展示一次，服务端仅保存不可逆哈希。请立即复制并妥善保管：
        </p>
        <div class="ev-key-box">
          <code class="ev-mono">{{ newlyIssuedPlainKey }}</code>
          <el-tooltip content="复制密钥明文" placement="top">
            <el-button
              size="small"
              :icon="CopyDocument"
              aria-label="复制密钥明文"
              @click="copyPlainKey(newlyIssuedPlainKey)"
            />
          </el-tooltip>
        </div>
      </div>

      <el-form v-else label-position="top">
        <el-form-item label="密钥名称" required>
          <el-input v-model="keyForm.key_name" placeholder="如 GitLab CI 每日门禁流水线" />
        </el-form-item>
        <div class="ev-grid-2">
          <el-form-item label="每分钟限流 (RPM)">
            <el-input-number v-model="keyForm.rate_limit_rpm" :min="10" :max="10000" />
          </el-form-item>
          <el-form-item label="有效天数">
            <el-input-number v-model="keyForm.expires_in_days" :min="1" :max="3650" />
          </el-form-item>
        </div>
      </el-form>

      <template #footer>
        <el-button @click="keyDialogOpen = false">
          {{ newlyIssuedPlainKey ? '我已保存，关闭' : '取消' }}
        </el-button>
        <el-button
          v-if="!newlyIssuedPlainKey"
          type="primary"
          :loading="creatingKey"
          @click="handleCreateApiKey"
        >
          确认签发密钥
        </el-button>
      </template>
    </el-dialog>

    <!-- Risk Confirmation Dialog for Revoking API Key (DESIGN.md §8.8 & §12) -->
    <el-dialog
      :model-value="Boolean(revokeConfirmTarget)"
      title="吊销自动化密钥确认"
      width="460px"
      @close="revokeConfirmTarget = null"
    >
      <p class="ev-confirm-copy">
        吊销后使用该密钥（<code class="ev-mono">{{ revokeConfirmTarget?.key_prefix }}...</code>）的自动化流水线将立即无法触发评测，且不可恢复。确定吊销吗？
      </p>
      <template #footer>
        <el-button @click="revokeConfirmTarget = null">取消</el-button>
        <el-button
          type="danger"
          :loading="revokingKeyId !== null"
          @click="confirmRevokeKey"
        >
          确认吊销
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.ev-section-mt {
  margin-top: 16px;
}

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

.ev-models-inline {
  margin-top: 4px;
  font-size: var(--ev-fs-label);
  color: var(--ev-color-primary);
}

.ev-probe-error {
  margin-top: 4px;
  font-size: var(--ev-fs-label);
  color: var(--ev-color-danger);
}

.ev-row-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.ev-preset-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.ev-grid-2 {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.ev-switch-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ev-drawer-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.ev-debug-metrics {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.ev-sub-heading {
  margin: 12px 0 6px;
  font-size: var(--ev-fs-label);
  font-weight: 600;
  color: var(--ev-text-secondary);
}

.ev-code-block {
  margin: 0;
  padding: 10px 12px;
  background-color: var(--ev-bg-card);
  border: 1px solid var(--ev-border-subtle);
  border-radius: var(--ev-radius-sm);
  overflow-x: auto;
  max-height: 200px;
}

.ev-key-once-notice {
  margin: 0 0 10px;
  font-size: var(--ev-fs-body);
  color: var(--ev-color-warning);
  font-weight: 500;
}

.ev-key-box {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
  background-color: var(--ev-bg-card);
  border: 1px solid var(--ev-border-strong);
  border-radius: var(--ev-radius-md);
  word-break: break-all;
}

.ev-confirm-copy {
  margin: 0;
  font-size: var(--ev-fs-body);
  color: var(--ev-text-primary);
  line-height: 1.6;
}

@media (max-width: 767px) {
  .ev-grid-2 {
    grid-template-columns: 1fr;
  }
}
</style>
