<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  ArrowLeft,
  Check,
  Delete,
  DocumentChecked,
  Plus,
  Refresh,
} from '@element-plus/icons-vue'
import { adaptersApi, dimensionsApi, providersApi, suitesApi } from '@/api/endpoints'
import { formatReadableError, formatScore } from '@/api/http'
import type {
  AtomicQuestionSpec,
  DecisionProviderRecord,
  DimensionCategory,
  DimensionCreatePayload,
  DimensionRecord,
  EvaluatorKind,
  LintReport,
  QuestionType,
  SuiteDimensionBinding,
  SuitePassPolicy,
  SuiteRecord,
  SuiteUpsertPayload,
  SutAdapterRecord,
} from '@/api/types'
import DualStatusBadge from '@/components/DualStatusBadge.vue'
import EmptyStateBox from '@/components/EmptyStateBox.vue'

const activeTab = ref<'dimensions' | 'suites'>('dimensions')
const loading = ref(false)
const errorBanner = ref<string | null>(null)

const dimensions = ref<DimensionRecord[]>([])
const suites = ref<SuiteRecord[]>([])
const adapters = ref<SutAdapterRecord[]>([])
const providers = ref<DecisionProviderRecord[]>([])

/* ---- Dimension list filters ---- */
const filterCategory = ref<string>('')
const filterKind = ref<string>('')

const categoryOptions: { label: string; value: DimensionCategory }[] = [
  { label: 'GROUNDING 事实忠实度', value: 'GROUNDING' },
  { label: 'ACCURACY 政策/结论准确性', value: 'ACCURACY' },
  { label: 'RELEVANCY 问答相关性', value: 'RELEVANCY' },
  { label: 'ROUTING 路由目标分派', value: 'ROUTING' },
  { label: 'SYNTHESIS 多源汇总', value: 'SYNTHESIS' },
  { label: 'SAFETY 合规边界', value: 'SAFETY' },
  { label: 'PERFORMANCE 延迟 SLA', value: 'PERFORMANCE' },
]

const stateFieldOptions = [
  { label: 'input (用户提问)', value: 'input' },
  { label: 'actual_output (被测实际输出)', value: 'actual_output' },
  { label: 'expected_output (人工校准标准答案)', value: 'expected_output' },
  { label: 'references (召回参考知识片段)', value: 'references' },
  { label: 'conversation_turns (多轮历史)', value: 'conversation_turns' },
  { label: 'actual_structured (实际结构化输出)', value: 'actual_structured' },
  { label: 'expected_structured (期望结构化目标)', value: 'expected_structured' },
]

/* ---- Two-Column Immutable Dimension Editor State ---- */
interface EditableChoiceOption {
  optKey: string
  description: string
  notApplicable: boolean
  score: number
}

interface EditableQuestionItem {
  key: string
  type: QuestionType
  weight: number
  instructions: string
  scoreTiers: string[]
  choiceOptions: EditableChoiceOption[]
}

const editorOpen = ref(false)
const linterMobileDrawerOpen = ref(false)
const editingExistingVersion = ref<number | null>(null)
const savingDimension = ref(false)
const linting = ref(false)
const lintReport = ref<LintReport>({ passed: true, issues: [] })

const dimForm = reactive({
  dimension_code: '',
  dimension_name: '',
  category: 'GROUNDING' as DimensionCategory,
  target_turn_mode: 'SINGLE_TURN' as 'SINGLE_TURN' | 'MULTI_TURN' | 'BOTH',
  evaluator_kind: 'SYSTEM_ONE' as EvaluatorKind,
  state_fields: ['input', 'actual_output', 'references'] as string[],
  default_threshold: 0.75,
  default_weight: 1.5,
  strict_mode: false,
  description: '',
  deterministic_rule_text: JSON.stringify(
    {
      rule_type: 'json_exact_or_in',
      actual_path: '$.actual_agent_id',
      expected_path: '$.expected_agent_ids',
    },
    null,
    2,
  ),
})

const editableQuestions = ref<EditableQuestionItem[]>([])

let lintTimer: ReturnType<typeof setTimeout> | null = null

const latestDimensionsByCode = computed(() => {
  const seen = new Set<string>()
  const result: DimensionRecord[] = []
  for (const d of dimensions.value) {
    if (!seen.has(d.dimension_code)) {
      seen.add(d.dimension_code)
      result.push(d)
    }
  }
  return result
})

const filteredDimensions = computed(() => {
  return latestDimensionsByCode.value.filter((d) => {
    if (filterCategory.value && d.category !== filterCategory.value) return false
    if (filterKind.value && d.evaluator_kind !== filterKind.value) return false
    return true
  })
})

const activeDimensionsForSuite = computed(() =>
  dimensions.value.filter((d) => d.status === 'ACTIVE'),
)

const nextVersionNumber = computed(() =>
  editingExistingVersion.value !== null ? editingExistingVersion.value + 1 : 1,
)

const lintErrorCount = computed(
  () => lintReport.value.issues.filter((i) => i.severity === 'ERROR').length,
)

const lintWarnCount = computed(
  () => lintReport.value.issues.filter((i) => i.severity === 'WARN').length,
)

const publishBlockedReason = computed(() => {
  if (dimForm.evaluator_kind === 'SYSTEM_ONE' && lintErrorCount.value > 0) {
    const firstIssue = lintReport.value.issues.find((i) => i.severity === 'ERROR')
    if (firstIssue?.code === 'LINT_SCORE_ORDER' && firstIssue.question_key) {
      return `静态质检未通过：${firstIssue.question_key} 的第 0 档检测到正向高分措辞，请按“最差到最好（worst-first）”升序调整档位描述后再发布。`
    }
    return `当前含 ${lintErrorCount.value} 项 Linter ERROR，修复后方可发布维度`
  }
  return ''
})

const statePreviewText = computed(() => {
  const sampleMap: Record<string, string> = {
    input: '常州灵活就业人员如何办理住房公积金缴存开户？',
    actual_output: '您可通过“常州住房公积金”微信公众号在线办理灵活就业缴存开户...',
    expected_output: '年满16周岁且未达法定退休年龄的灵活就业人员可凭身份证与I类银行卡办理...',
    references: '[1] 《常州市灵活就业人员参加住房公积金制度试点实施方案》第三条...',
    conversation_turns: '[{"role": "user", "content": "..."}]',
    actual_structured: '{"actual_agent_id": "cz_gjj_agent"}',
    expected_structured: '{"expected_agent_ids": ["cz_gjj_agent"]}',
  }
  if (dimForm.state_fields.length === 0) {
    return '（未勾选任何 state_fields，判别模型将无法读取上下文）'
  }
  return dimForm.state_fields
    .map((field) => `[${field}]\n${sampleMap[field] || '...'}`)
    .join('\n\n')
})

function serializeEditableQuestions(): AtomicQuestionSpec[] {
  return editableQuestions.value.map((q) => {
    if (q.type === 'noul') {
      return {
        key: q.key.trim(),
        type: 'noul',
        weight: q.weight,
        instructions: q.instructions,
      }
    }
    if (q.type === 'score') {
      return {
        key: q.key.trim(),
        type: 'score',
        weight: q.weight,
        instructions: q.instructions,
        criteria: q.scoreTiers.map((t) => t.trim()),
      }
    }
    const criteriaMap: Record<string, string> = {}
    const scoresMap: Record<string, number | null> = {}
    for (const opt of q.choiceOptions) {
      const k = opt.optKey.trim()
      if (!k) continue
      criteriaMap[k] = opt.description.trim()
      scoresMap[k] = opt.notApplicable ? null : Number(opt.score)
    }
    return {
      key: q.key.trim(),
      type: 'choice',
      weight: q.weight,
      instructions: q.instructions,
      criteria: criteriaMap,
      option_scores: scoresMap,
    }
  })
}

async function runLiveLint(): Promise<void> {
  if (!editorOpen.value || dimForm.evaluator_kind !== 'SYSTEM_ONE') {
    lintReport.value = { passed: true, issues: [] }
    return
  }
  linting.value = true
  try {
    lintReport.value = await dimensionsApi.lint({
      state_fields_json: dimForm.state_fields,
      questions_json: serializeEditableQuestions(),
    })
  } catch (err) {
    // Surface lint request issues non-destructively
    lintReport.value = {
      passed: false,
      issues: [
        {
          severity: 'ERROR',
          code: 'LINT_REQUEST_FAILED',
          message: formatReadableError(err),
        },
      ],
    }
  } finally {
    linting.value = false
  }
}

function scheduleLiveLint(): void {
  if (lintTimer) clearTimeout(lintTimer)
  lintTimer = setTimeout(() => {
    lintTimer = null
    void runLiveLint()
  }, 240)
}

watch(
  () => [dimForm.evaluator_kind, dimForm.state_fields, editableQuestions.value],
  () => {
    if (editorOpen.value) {
      scheduleLiveLint()
    }
  },
  { deep: true },
)

function createDefaultQuestion(index: number): EditableQuestionItem {
  return {
    key: `question_${index}`,
    type: 'score',
    weight: 1.5,
    instructions: '对比 references，actual_output 给出的核心结论是否准确且有据可查？',
    scoreTiers: [
      '核心结论存在明显事实编造或与 references 相矛盾',
      '部分结论有依据，但遗漏关键限制条件',
      '所有实质性结论均完全忠实于 references',
    ],
    choiceOptions: [
      { optKey: 'inaccurate', description: '存在事实偏差或缺失关键材料', notApplicable: false, score: 0.0 },
      { optKey: 'accurate', description: '结论与材料指引完整准确', notApplicable: false, score: 1.0 },
      { optKey: 'not_applicable', description: '当前提问不涉及该类场景（分母剔除）', notApplicable: true, score: 0.0 },
    ],
  }
}

function openCreateDimension(): void {
  editingExistingVersion.value = null
  dimForm.dimension_code = ''
  dimForm.dimension_name = ''
  dimForm.category = 'GROUNDING'
  dimForm.target_turn_mode = 'SINGLE_TURN'
  dimForm.evaluator_kind = 'SYSTEM_ONE'
  dimForm.state_fields = ['input', 'actual_output', 'references']
  dimForm.default_threshold = 0.75
  dimForm.default_weight = 1.5
  dimForm.strict_mode = false
  dimForm.description = ''
  editableQuestions.value = [createDefaultQuestion(1)]
  editorOpen.value = true
  void runLiveLint()
}

function openUpgradeDimension(row: DimensionRecord): void {
  editingExistingVersion.value = row.version
  dimForm.dimension_code = row.dimension_code
  dimForm.dimension_name = row.dimension_name
  dimForm.category = row.category
  dimForm.target_turn_mode = row.target_turn_mode
  dimForm.evaluator_kind = row.evaluator_kind
  dimForm.state_fields = [...(row.state_fields_json || ['input', 'actual_output'])]
  dimForm.default_threshold = row.default_threshold
  dimForm.default_weight = row.default_weight
  dimForm.strict_mode = row.strict_mode
  dimForm.description = row.description || ''

  if (row.deterministic_rule_json) {
    dimForm.deterministic_rule_text = JSON.stringify(row.deterministic_rule_json, null, 2)
  }

  if (row.questions_json?.length) {
    editableQuestions.value = row.questions_json.map((q) => {
      const scoreTiers = Array.isArray(q.criteria)
        ? [...q.criteria]
        : ['存在明显错误', '基本符合预期', '完全准确忠实']
      const choiceOptions: EditableChoiceOption[] =
        q.criteria && !Array.isArray(q.criteria)
          ? Object.entries(q.criteria).map(([optKey, desc]) => {
              const sc = q.option_scores?.[optKey]
              return {
                optKey,
                description: desc,
                notApplicable: sc === null || sc === undefined,
                score: typeof sc === 'number' ? sc : 0,
              }
            })
          : [
              { optKey: 'fail', description: '不满足要求', notApplicable: false, score: 0 },
              { optKey: 'pass', description: '完全满足要求', notApplicable: false, score: 1 },
            ]
      return {
        key: q.key,
        type: q.type,
        weight: q.weight,
        instructions: q.instructions,
        scoreTiers,
        choiceOptions,
      }
    })
  } else {
    editableQuestions.value = [createDefaultQuestion(1)]
  }

  editorOpen.value = true
  void runLiveLint()
}

function addAtomicQuestion(): void {
  editableQuestions.value.push(createDefaultQuestion(editableQuestions.value.length + 1))
}

function removeAtomicQuestion(idx: number): void {
  if (editableQuestions.value.length <= 1) return
  editableQuestions.value.splice(idx, 1)
}

function focusQuestionCard(qKey?: string | null): void {
  if (!qKey) return
  const el = document.getElementById(`q-card-${qKey}`)
  el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

async function handleSubmitDimension(status: 'DRAFT' | 'ACTIVE'): Promise<void> {
  if (!dimForm.dimension_code.trim() || !dimForm.dimension_name.trim()) {
    ElMessage.warning('请填写维度编码与维度名称')
    return
  }

  let detRule: Record<string, unknown> | null = null
  if (dimForm.evaluator_kind === 'DETERMINISTIC') {
    try {
      detRule = JSON.parse(dimForm.deterministic_rule_text) as Record<string, unknown>
    } catch {
      ElMessage.error('确定性规则 JSON 格式不合法')
      return
    }
  }

  const payload: DimensionCreatePayload = {
    dimension_code: dimForm.dimension_code.trim(),
    dimension_name: dimForm.dimension_name.trim(),
    category: dimForm.category,
    target_turn_mode: dimForm.target_turn_mode,
    evaluator_kind: dimForm.evaluator_kind,
    state_fields_json: dimForm.evaluator_kind === 'SYSTEM_ONE' ? dimForm.state_fields : null,
    questions_json:
      dimForm.evaluator_kind === 'SYSTEM_ONE' ? serializeEditableQuestions() : null,
    deterministic_rule_json: dimForm.evaluator_kind === 'DETERMINISTIC' ? detRule : null,
    default_threshold: dimForm.default_threshold,
    strict_mode: dimForm.strict_mode,
    default_weight: dimForm.default_weight,
    status,
    description: dimForm.description.trim() || null,
  }

  savingDimension.value = true
  try {
    const created = await dimensionsApi.createVersion(payload)
    ElMessage.success(
      `已保存 ${created.dimension_code} v${created.version}（${created.status}）`,
    )
    editorOpen.value = false
    dimensions.value = await dimensionsApi.list({ latest_only: false })
  } catch (err) {
    ElMessage.error(formatReadableError(err))
  } finally {
    savingDimension.value = false
  }
}

/* ---- Suite Gate Assembler ---- */
const suiteDrawerOpen = ref(false)
const isEditingSuite = ref(false)
const savingSuite = ref(false)

const suiteForm = reactive<{
  suite_code: string
  suite_name: string
  eval_level: 'E2E' | 'COMPONENT'
  default_adapter_id: number | null
  default_provider_id: number | null
  default_model_override: string
  pass_policy: SuitePassPolicy
  suite_pass_threshold: number
  confidence_review_floor: number
  enable_semantic_regression: boolean
  status: 'ACTIVE' | 'ARCHIVED'
  description: string
  bindings: SuiteDimensionBinding[]
}>({
  suite_code: '',
  suite_name: '',
  eval_level: 'E2E',
  default_adapter_id: null,
  default_provider_id: null,
  default_model_override: '',
  pass_policy: 'ALL_BLOCKERS_AND_WEIGHTED',
  suite_pass_threshold: 0.75,
  confidence_review_floor: 0.5,
  enable_semantic_regression: true,
  status: 'ACTIVE',
  description: '',
  bindings: [],
})

function openCreateSuite(): void {
  isEditingSuite.value = false
  suiteForm.suite_code = ''
  suiteForm.suite_name = ''
  suiteForm.eval_level = 'E2E'
  suiteForm.default_adapter_id = null
  suiteForm.default_provider_id = null
  suiteForm.default_model_override = ''
  suiteForm.pass_policy = 'ALL_BLOCKERS_AND_WEIGHTED'
  suiteForm.suite_pass_threshold = 0.75
  suiteForm.confidence_review_floor = 0.5
  suiteForm.enable_semantic_regression = true
  suiteForm.status = 'ACTIVE'
  suiteForm.description = ''
  const firstActive = activeDimensionsForSuite.value[0]
  suiteForm.bindings = firstActive
    ? [
        {
          dimension_code: firstActive.dimension_code,
          version: firstActive.version,
          weight: firstActive.default_weight,
          threshold: firstActive.default_threshold,
          is_blocker: true,
        },
      ]
    : []
  suiteDrawerOpen.value = true
}

function openEditSuite(row: SuiteRecord): void {
  isEditingSuite.value = true
  suiteForm.suite_code = row.suite_code
  suiteForm.suite_name = row.suite_name
  suiteForm.eval_level = row.eval_level
  suiteForm.default_adapter_id = row.default_adapter_id
  suiteForm.default_provider_id = row.default_provider_id
  suiteForm.default_model_override = row.default_model_override || ''
  suiteForm.pass_policy = row.pass_policy
  suiteForm.suite_pass_threshold = row.suite_pass_threshold
  suiteForm.confidence_review_floor = row.confidence_review_floor
  suiteForm.enable_semantic_regression = row.enable_semantic_regression
  suiteForm.status = row.status
  suiteForm.description = row.description || ''
  suiteForm.bindings = row.dimensions_json.map((b) => ({ ...b }))
  suiteDrawerOpen.value = true
}

function addSuiteBinding(): void {
  const candidate = activeDimensionsForSuite.value.find(
    (d) => !suiteForm.bindings.some((b) => b.dimension_code === d.dimension_code),
  )
  if (!candidate) {
    ElMessage.info('所有 ACTIVE 评价维度均已在列表中')
    return
  }
  suiteForm.bindings.push({
    dimension_code: candidate.dimension_code,
    version: candidate.version,
    weight: candidate.default_weight,
    threshold: candidate.default_threshold,
    is_blocker: false,
  })
}

async function handleSaveSuite(): Promise<void> {
  if (!suiteForm.suite_code.trim() || !suiteForm.suite_name.trim()) {
    ElMessage.warning('请填写套件编码与名称')
    return
  }
  if (suiteForm.bindings.length === 0) {
    ElMessage.warning('请至少绑定一个锁定版本的评价维度')
    return
  }

  const payload: SuiteUpsertPayload = {
    suite_code: suiteForm.suite_code.trim(),
    suite_name: suiteForm.suite_name.trim(),
    eval_level: suiteForm.eval_level,
    default_adapter_id: suiteForm.default_adapter_id,
    default_provider_id: suiteForm.default_provider_id,
    default_model_override: suiteForm.default_model_override.trim() || null,
    dimensions_json: suiteForm.bindings,
    pass_policy: suiteForm.pass_policy,
    suite_pass_threshold: suiteForm.suite_pass_threshold,
    confidence_review_floor: suiteForm.confidence_review_floor,
    enable_semantic_regression: suiteForm.enable_semantic_regression,
    status: suiteForm.status,
    description: suiteForm.description.trim() || null,
  }

  savingSuite.value = true
  try {
    if (isEditingSuite.value) {
      await suitesApi.update(payload.suite_code, payload)
    } else {
      await suitesApi.create(payload)
    }
    ElMessage.success('评测套件门禁已保存')
    suiteDrawerOpen.value = false
    suites.value = await suitesApi.list()
  } catch (err) {
    ElMessage.error(formatReadableError(err))
  } finally {
    savingSuite.value = false
  }
}

async function loadAll(): Promise<void> {
  loading.value = true
  errorBanner.value = null
  try {
    const [d, s, a, p] = await Promise.all([
      dimensionsApi.list({ latest_only: false }),
      suitesApi.list(),
      adaptersApi.list(),
      providersApi.list(),
    ])
    dimensions.value = d
    suites.value = s
    adapters.value = a
    providers.value = p
  } catch (err) {
    errorBanner.value = formatReadableError(err)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void loadAll()
})

onBeforeUnmount(() => {
  if (lintTimer) clearTimeout(lintTimer)
})
</script>

<template>
  <div class="ev-page">
    <!-- Two-Column Immutable Dimension Workbench Mode (DESIGN.md §7.1 & §8.3) -->
    <template v-if="editorOpen">
      <div class="ev-page-header">
        <div class="ev-editor-title-group">
          <el-button :icon="ArrowLeft" @click="editorOpen = false">返回维度列表</el-button>
          <div>
            <h1 class="ev-page-title">
              {{ editingExistingVersion ? '升版编辑评价维度' : '新增评价维度' }}
            </h1>
            <p class="ev-page-subtitle">
              锁定版本语义：<span class="ev-mono">
                {{
                  editingExistingVersion
                    ? `v${editingExistingVersion} -> 保存后生成 v${nextVersionNumber}`
                    : '首次发布生成 v1'
                }}
              </span>
            </p>
          </div>
        </div>

        <div class="ev-editor-mobile-only">
          <el-button @click="linterMobileDrawerOpen = true">
            查看质检诊断 ({{ lintErrorCount }} 错 / {{ lintWarnCount }} 警)
          </el-button>
        </div>
      </div>

      <!-- DESIGN.md §7.2 Narrow Screen Guard -->
      <div class="ev-narrow-editor-notice ev-panel">
        重型原子题编排器建议在标准桌面端（≥ 1024px）操作，以并排对照实时质检与可见状态预览。
      </div>

      <!-- DESIGN.md §12 Immutable Version Upgrade Notice -->
      <div v-if="editingExistingVersion !== null" class="ev-upgrade-banner ev-panel">
        <DualStatusBadge tone="warning" symbol="!" label="不可变版本保护" :value="`v${nextVersionNumber}`" />
        <span>
          已发布维度不可原地覆盖。保存本次题干或档位修改将自动生成
          <code class="ev-mono">{{ dimForm.dimension_code }}</code> 的
          <code class="ev-mono">v{{ nextVersionNumber }}</code> 新版本，历史批次得分不受影响。
        </span>
      </div>

      <div class="ev-workbench-columns">
        <!-- Left 60% Form -->
        <section class="ev-workbench-left ev-surface">
          <el-form label-position="top">
            <div class="ev-grid-2">
              <el-form-item label="维度编码 (dimension_code)" required>
                <el-input
                  v-model="dimForm.dimension_code"
                  :disabled="editingExistingVersion !== null"
                  placeholder="如 rag_faithfulness_s1（不可含双下划线）"
                />
              </el-form-item>
              <el-form-item label="维度名称" required>
                <el-input v-model="dimForm.dimension_name" placeholder="如 知识引用忠实度与零编造" />
              </el-form-item>
            </div>

            <div class="ev-grid-3">
              <el-form-item label="质量分类 (category)">
                <el-select v-model="dimForm.category" style="width: 100%">
                  <el-option
                    v-for="opt in categoryOptions"
                    :key="opt.value"
                    :label="opt.label"
                    :value="opt.value"
                  />
                </el-select>
              </el-form-item>
              <el-form-item label="评估机制 (evaluator_kind)">
                <el-select v-model="dimForm.evaluator_kind" style="width: 100%">
                  <el-option label="SYSTEM_ONE (概率原子题)" value="SYSTEM_ONE" />
                  <el-option label="DETERMINISTIC (纯代码规则)" value="DETERMINISTIC" />
                </el-select>
              </el-form-item>
              <el-form-item label="适用轮次">
                <el-select v-model="dimForm.target_turn_mode" style="width: 100%">
                  <el-option label="SINGLE_TURN 单轮" value="SINGLE_TURN" />
                  <el-option label="MULTI_TURN 多轮" value="MULTI_TURN" />
                  <el-option label="BOTH 通用" value="BOTH" />
                </el-select>
              </el-form-item>
            </div>

            <div class="ev-grid-3">
              <el-form-item label="默认达标阈值 [0, 1]">
                <el-input-number
                  v-model="dimForm.default_threshold"
                  :min="0"
                  :max="1"
                  :step="0.05"
                  :precision="2"
                />
              </el-form-item>
              <el-form-item label="默认合成权重">
                <el-input-number
                  v-model="dimForm.default_weight"
                  :min="0.1"
                  :max="10"
                  :step="0.5"
                  :precision="2"
                />
              </el-form-item>
              <el-form-item label="严格满分模式">
                <el-switch v-model="dimForm.strict_mode" active-text="任一题未满分置 0" />
              </el-form-item>
            </div>

            <!-- SYSTEM_ONE Atomic Questions -->
            <template v-if="dimForm.evaluator_kind === 'SYSTEM_ONE'">
              <el-form-item label="可见上下文字段 (state_fields — 共享相同字段集的多维度自动合批)">
                <el-checkbox-group v-model="dimForm.state_fields">
                  <el-checkbox
                    v-for="sf in stateFieldOptions"
                    :key="sf.value"
                    :label="sf.label"
                    :value="sf.value"
                  />
                </el-checkbox-group>
              </el-form-item>

              <div class="ev-questions-header">
                <h3 class="ev-section-title">原子题列表 ({{ editableQuestions.length }} / 64)</h3>
                <el-button size="small" :icon="Plus" @click="addAtomicQuestion">
                  新增原子题
                </el-button>
              </div>

              <div
                v-for="(q, qIdx) in editableQuestions"
                :id="`q-card-${q.key}`"
                :key="qIdx"
                class="ev-q-card ev-panel"
              >
                <div class="ev-q-card__top">
                  <span class="ev-mono ev-q-index">#{{ qIdx + 1 }}</span>
                  <el-input
                    v-model="q.key"
                    placeholder="题号 key（如 core_conclusion）"
                    style="width: 210px"
                  />
                  <el-select v-model="q.type" style="width: 170px">
                    <el-option label="noul · 是非判别题" value="noul" />
                    <el-option label="score · 有序量表题" value="score" />
                    <el-option label="choice · 多选分类题" value="choice" />
                  </el-select>
                  <span class="ev-cell-secondary">权重</span>
                  <el-input-number v-model="q.weight" :min="0.1" :max="10" :step="0.5" size="small" />
                  <el-tooltip content="移除该原子题" placement="top">
                    <el-button
                      v-if="editableQuestions.length > 1"
                      size="small"
                      type="danger"
                      plain
                      :icon="Delete"
                      aria-label="移除该原子题"
                      @click="removeAtomicQuestion(qIdx)"
                    />
                  </el-tooltip>
                </div>

                <el-form-item label="判别题干指令 (instructions)" class="ev-q-mt">
                  <el-input
                    v-model="q.instructions"
                    type="textarea"
                    :rows="2"
                    placeholder="陈述清晰单一的质量判别标准（避免复合多问句）"
                  />
                </el-form-item>

                <!-- SCORE Question: Worst-First Mandatory Badges -->
                <div v-if="q.type === 'score'" class="ev-tier-section">
                  <div class="ev-tier-hint">
                    <strong>档位顺序约束（Worst-First）：</strong>
                    必须严格按「第 0 档最差（0.00）→ 第 {{ q.scoreTiers.length - 1 }} 档最好（1.00）」升序编写，颠倒将触发 Linter ERROR 阻断发布。
                  </div>
                  <div v-for="(_, tIdx) in q.scoreTiers" :key="tIdx" class="ev-tier-row">
                    <span
                      class="ev-tier-badge ev-mono"
                      :class="{
                        'is-worst': tIdx === 0,
                        'is-best': tIdx === q.scoreTiers.length - 1,
                      }"
                    >
                      第 {{ tIdx }} 档 ·
                      {{
                        tIdx === 0
                          ? '最差 0.00'
                          : tIdx === q.scoreTiers.length - 1
                            ? '最好 1.00'
                            : (tIdx / (q.scoreTiers.length - 1)).toFixed(2)
                      }}
                    </span>
                    <el-input v-model="q.scoreTiers[tIdx]" placeholder="描述该档位的客观证据表现" />
                    <el-button
                      v-if="q.scoreTiers.length > 2"
                      size="small"
                      link
                      @click="q.scoreTiers.splice(tIdx, 1)"
                    >
                      删除
                    </el-button>
                  </div>
                  <el-button size="small" @click="q.scoreTiers.push('')">+ 新增量表档位</el-button>
                </div>

                <!-- CHOICE Question: Option Scores + null N/A option -->
                <div v-else-if="q.type === 'choice'" class="ev-tier-section">
                  <div class="ev-tier-hint">
                    支持勾选「不适用剔除项（score = null）」：当不适用选项概率之和 ≥ 0.5 时，该题自动从维度分母中剔除。
                  </div>
                  <div v-for="(opt, oIdx) in q.choiceOptions" :key="oIdx" class="ev-choice-row">
                    <el-input v-model="opt.optKey" placeholder="选项编码" style="width: 140px" />
                    <el-input v-model="opt.description" placeholder="选项判别描述" style="flex: 1" />
                    <el-checkbox v-model="opt.notApplicable">不适用 (null)</el-checkbox>
                    <el-input-number
                      v-if="!opt.notApplicable"
                      v-model="opt.score"
                      :min="0"
                      :max="1"
                      :step="0.2"
                      :precision="2"
                      size="small"
                    />
                    <span v-else class="ev-na-pill ev-mono">剔除分母</span>
                    <el-button
                      v-if="q.choiceOptions.length > 2"
                      size="small"
                      link
                      @click="q.choiceOptions.splice(oIdx, 1)"
                    >
                      删除
                    </el-button>
                  </div>
                  <el-button
                    size="small"
                    @click="
                      q.choiceOptions.push({
                        optKey: `opt_${q.choiceOptions.length + 1}`,
                        description: '',
                        notApplicable: false,
                        score: 0.5,
                      })
                    "
                  >
                    + 新增分类选项
                  </el-button>
                </div>
              </div>
            </template>

            <!-- DETERMINISTIC Rule Editor -->
            <template v-else>
              <el-form-item label="确定性规则配置 JSON (deterministic_rule_json)">
                <el-input
                  v-model="dimForm.deterministic_rule_text"
                  type="textarea"
                  :rows="8"
                  class="ev-mono"
                />
              </el-form-item>
            </template>

            <el-form-item label="维度说明 (description)">
              <el-input v-model="dimForm.description" type="textarea" :rows="2" />
            </el-form-item>

            <!-- Action Bar: Max 1 Primary Button, Disabled + Tooltip on Linter ERROR -->
            <div class="ev-editor-actions">
              <el-button @click="editorOpen = false">取消</el-button>
              <el-button :loading="savingDimension" @click="handleSubmitDimension('DRAFT')">
                存为草稿 (DRAFT)
              </el-button>
              <el-tooltip
                :content="publishBlockedReason"
                :disabled="!publishBlockedReason"
                placement="top"
              >
                <span class="ev-tooltip-wrap">
                  <el-button
                    type="primary"
                    :icon="Check"
                    :loading="savingDimension"
                    :disabled="Boolean(publishBlockedReason)"
                    @click="handleSubmitDimension('ACTIVE')"
                  >
                    发布维度 v{{ nextVersionNumber }}
                  </el-button>
                </span>
              </el-tooltip>
            </div>
          </el-form>
        </section>

        <!-- Right 40% Sticky State Preview & Live Rubric Linter -->
        <aside class="ev-workbench-right">
          <div class="ev-surface ev-sticky-box">
            <div class="ev-linter-head">
              <h3 class="ev-section-title">题干静态质检 (Rubric Linter)</h3>
              <el-button
                size="small"
                :icon="DocumentChecked"
                :loading="linting"
                @click="runLiveLint"
              >
                执行静态质检
              </el-button>
            </div>

            <div class="ev-linter-summary">
              <DualStatusBadge
                v-if="lintErrorCount === 0 && lintWarnCount === 0"
                tone="success"
                symbol="✓"
                label="静态质检全部通过"
              />
              <DualStatusBadge
                v-else-if="lintErrorCount > 0"
                tone="danger"
                symbol="✕"
                label="阻断级错误"
                :value="`${lintErrorCount} 项`"
              />
              <DualStatusBadge
                v-if="lintWarnCount > 0"
                tone="warning"
                symbol="!"
                label="优化建议"
                :value="`${lintWarnCount} 项`"
              />
            </div>

            <div v-if="lintReport.issues.length > 0" class="ev-lint-list">
              <div
                v-for="(issue, idx) in lintReport.issues"
                :key="idx"
                class="ev-lint-item ev-panel"
              >
                <div class="ev-lint-item__head">
                  <DualStatusBadge
                    :tone="issue.severity === 'ERROR' ? 'danger' : 'warning'"
                    :symbol="issue.severity === 'ERROR' ? '✕' : '!'"
                    :label="issue.severity"
                    :value="issue.code"
                  />
                  <el-button
                    v-if="issue.question_key"
                    size="small"
                    link
                    @click="focusQuestionCard(issue.question_key)"
                  >
                    定位 {{ issue.question_key }}
                  </el-button>
                </div>
                <p class="ev-lint-item__msg">{{ issue.message }}</p>
              </div>
            </div>

            <h3 class="ev-section-title ev-section-mt">System One 可见状态预览 (state)</h3>
            <pre class="ev-state-preview ev-mono">{{ statePreviewText }}</pre>
          </div>
        </aside>
      </div>

      <!-- Tablet Drawer for Linter (768px-1023px per DESIGN.md §7.2) -->
      <el-drawer v-model="linterMobileDrawerOpen" title="静态质检与可见状态预览" size="420px">
        <div v-for="(issue, idx) in lintReport.issues" :key="idx" class="ev-lint-item ev-panel">
          <DualStatusBadge
            :tone="issue.severity === 'ERROR' ? 'danger' : 'warning'"
            :symbol="issue.severity === 'ERROR' ? '✕' : '!'"
            :label="issue.severity"
            :value="issue.code"
          />
          <p class="ev-lint-item__msg">{{ issue.message }}</p>
        </div>
        <pre class="ev-state-preview ev-mono ev-section-mt">{{ statePreviewText }}</pre>
      </el-drawer>
    </template>

    <!-- Standard Tabs Mode -->
    <template v-else>
      <div class="ev-page-header">
        <div>
          <h1 class="ev-page-title">评价维度与评测套件工作台</h1>
          <p class="ev-page-subtitle">
            不可变版本化管理原子题评价维度、静态质检规则，并组装锁定版本的质量门禁套件
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

      <el-tabs v-model="activeTab">
        <!-- Tab 1: Dimensions -->
        <el-tab-pane label="评价维度列表" name="dimensions">
          <div class="ev-filter-bar">
            <div class="ev-filter-bar__left">
              <el-select
                v-model="filterCategory"
                placeholder="全部质量分类"
                clearable
                style="width: 190px"
              >
                <el-option
                  v-for="opt in categoryOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-select>
              <el-select
                v-model="filterKind"
                placeholder="全部评估机制"
                clearable
                style="width: 190px"
              >
                <el-option label="SYSTEM_ONE 概率判别" value="SYSTEM_ONE" />
                <el-option label="DETERMINISTIC 确定性规则" value="DETERMINISTIC" />
              </el-select>
              <el-button @click="(filterCategory = ''), (filterKind = '')">重置</el-button>
              <el-button :icon="Refresh" @click="loadAll">刷新</el-button>
            </div>
            <div class="ev-filter-bar__right">
              <el-button type="primary" :icon="Plus" @click="openCreateDimension">
                新增评价维度
              </el-button>
            </div>
          </div>

          <div class="ev-surface ev-section-mt">
            <el-skeleton v-if="loading" :rows="5" animated />
            <EmptyStateBox
              v-else-if="filteredDimensions.length === 0"
              :filtered="Boolean(filterCategory || filterKind)"
              entity-name="评价维度"
              action-label="新增评价维度"
              @reset="(filterCategory = ''), (filterKind = '')"
              @action="openCreateDimension"
            />
            <el-table v-else :data="filteredDimensions" stripe style="width: 100%">
              <el-table-column label="维度编码 / 最新版本" min-width="230" fixed="left">
                <template #default="{ row }">
                  <div class="ev-cell-primary">
                    <span class="ev-mono">{{ row.dimension_code }}</span>
                    <span class="ev-ver-pill ev-mono">v{{ row.version }}</span>
                  </div>
                  <div class="ev-cell-secondary">{{ row.dimension_name }}</div>
                </template>
              </el-table-column>

              <el-table-column label="分类 / 评估机制" width="210">
                <template #default="{ row }">
                  <div class="ev-mono">{{ row.category }}</div>
                  <div class="ev-cell-secondary">{{ row.evaluator_kind }}</div>
                </template>
              </el-table-column>

              <el-table-column label="可见字段 / 原子题数" min-width="220">
                <template #default="{ row }">
                  <template v-if="row.evaluator_kind === 'SYSTEM_ONE'">
                    <div class="ev-mono">{{ (row.state_fields_json || []).join(', ') }}</div>
                    <div class="ev-cell-secondary ev-mono">
                      {{ (row.questions_json || []).length }} 道原子题
                    </div>
                  </template>
                  <template v-else>
                    <span class="ev-mono">
                      {{ row.deterministic_rule_json?.rule_type || 'DETERMINISTIC' }}
                    </span>
                  </template>
                </template>
              </el-table-column>

              <el-table-column label="默认阈值 / 权重" width="160" align="right">
                <template #default="{ row }">
                  <div class="ev-mono">{{ formatScore(row.default_threshold, 2) }}</div>
                  <div class="ev-cell-secondary ev-mono">
                    权重 {{ formatScore(row.default_weight, 1) }}
                  </div>
                </template>
              </el-table-column>

              <el-table-column label="静态质检状态" width="160">
                <template #default="{ row }">
                  <DualStatusBadge
                    :tone="row.lint_passed ? 'success' : 'danger'"
                    :symbol="row.lint_passed ? '✓' : '✕'"
                    :label="row.lint_passed ? '质检通过' : '存在阻断项'"
                  />
                </template>
              </el-table-column>

              <el-table-column label="发布状态" width="130">
                <template #default="{ row }">
                  <DualStatusBadge
                    :tone="row.status === 'ACTIVE' ? 'success' : 'neutral'"
                    :symbol="row.status === 'ACTIVE' ? '✓' : '!'"
                    :label="row.status"
                  />
                </template>
              </el-table-column>

              <el-table-column label="操作" width="140" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" @click="openUpgradeDimension(row)">升版编辑</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-tab-pane>

        <!-- Tab 2: Suites -->
        <el-tab-pane label="评测套件组装器" name="suites">
          <div class="ev-filter-bar">
            <div class="ev-filter-bar__left">
              <span class="ev-cell-secondary">
                评测套件强制绑定具体评价维度版本号（如 <code class="ev-mono">v1</code>），杜绝浮动版本漂移
              </span>
            </div>
            <div class="ev-filter-bar__right">
              <el-button type="primary" :icon="Plus" @click="openCreateSuite">
                新增评测套件
              </el-button>
            </div>
          </div>

          <div class="ev-surface ev-section-mt">
            <EmptyStateBox
              v-if="suites.length === 0"
              entity-name="评测套件"
              action-label="新增评测套件"
              @action="openCreateSuite"
            />
            <el-table v-else :data="suites" stripe style="width: 100%">
              <el-table-column label="套件编码 / 名称" min-width="240" fixed="left">
                <template #default="{ row }">
                  <div class="ev-mono">{{ row.suite_code }}</div>
                  <div class="ev-cell-secondary">{{ row.suite_name }}</div>
                </template>
              </el-table-column>

              <el-table-column label="评测层级 / 门禁策略" width="230">
                <template #default="{ row }">
                  <div class="ev-mono">{{ row.eval_level }}</div>
                  <div class="ev-cell-secondary ev-mono">{{ row.pass_policy }}</div>
                </template>
              </el-table-column>

              <el-table-column label="锁定评价维度组合" min-width="280">
                <template #default="{ row }">
                  <div class="ev-binding-tags">
                    <span
                      v-for="b in row.dimensions_json"
                      :key="`${b.dimension_code}-${b.version}`"
                      class="ev-ver-pill ev-mono"
                    >
                      {{ b.dimension_code }}@v{{ b.version }}
                      <template v-if="b.is_blocker"> [Blocker]</template>
                    </span>
                  </div>
                </template>
              </el-table-column>

              <el-table-column label="总及格线 / 复核线" width="170" align="right">
                <template #default="{ row }">
                  <div class="ev-mono">及格 ≥ {{ formatScore(row.suite_pass_threshold, 2) }}</div>
                  <div class="ev-cell-secondary ev-mono">
                    把握度 &lt; {{ formatScore(row.confidence_review_floor, 2) }}
                  </div>
                </template>
              </el-table-column>

              <el-table-column label="操作" width="120" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" @click="openEditSuite(row)">配置门禁</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-tab-pane>
      </el-tabs>
    </template>

    <!-- Suite Gate Assembler Drawer -->
    <el-drawer
      v-model="suiteDrawerOpen"
      :title="isEditingSuite ? '编辑评测套件门禁' : '新增评测套件门禁'"
      size="720px"
    >
      <el-form label-position="top">
        <div class="ev-grid-2">
          <el-form-item label="套件编码 (suite_code)" required>
            <el-input
              v-model="suiteForm.suite_code"
              :disabled="isEditingSuite"
              placeholder="如 suite_gov_e2e_golden_v1"
            />
          </el-form-item>
          <el-form-item label="套件名称" required>
            <el-input v-model="suiteForm.suite_name" placeholder="如 政务客服端到端黄金验收套件" />
          </el-form-item>
        </div>

        <div class="ev-grid-3">
          <el-form-item label="评测层级 (eval_level)">
            <el-select v-model="suiteForm.eval_level" style="width: 100%">
              <el-option label="E2E 端到端" value="E2E" />
              <el-option label="COMPONENT 组件级" value="COMPONENT" />
            </el-select>
          </el-form-item>
          <el-form-item label="门禁判定策略 (pass_policy)">
            <el-select v-model="suiteForm.pass_policy" style="width: 100%">
              <el-option label="一票否决 + 加权总分达标" value="ALL_BLOCKERS_AND_WEIGHTED" />
              <el-option label="全部维度同时达标" value="ALL_DIMENSIONS" />
            </el-select>
          </el-form-item>
          <el-form-item label="套件加权及格线">
            <el-input-number
              v-model="suiteForm.suite_pass_threshold"
              :min="0"
              :max="1"
              :step="0.05"
              :precision="2"
            />
          </el-form-item>
        </div>

        <div class="ev-grid-3">
          <el-form-item label="把握度复核下限 (confidence_review_floor)">
            <el-input-number
              v-model="suiteForm.confidence_review_floor"
              :min="0"
              :max="1"
              :step="0.05"
              :precision="2"
            />
          </el-form-item>
          <el-form-item label="默认被测适配器 (可选)">
            <el-select v-model="suiteForm.default_adapter_id" clearable style="width: 100%">
              <el-option
                v-for="ad in adapters.filter((x) => x.eval_level === suiteForm.eval_level)"
                :key="ad.id"
                :label="`${ad.adapter_code} (${ad.adapter_name})`"
                :value="ad.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="默认判别模型渠道 (可选)">
            <el-select v-model="suiteForm.default_provider_id" clearable style="width: 100%">
              <el-option
                v-for="pv in providers"
                :key="pv.id"
                :label="`${pv.provider_code} (${pv.default_model})`"
                :value="pv.id"
              />
            </el-select>
          </el-form-item>
        </div>

        <el-checkbox v-model="suiteForm.enable_semantic_regression">
          存在回归基线批次时自动执行四分类语义回归差异判别
        </el-checkbox>

        <div class="ev-questions-header ev-section-mt">
          <h3 class="ev-section-title">锁定版本评价维度绑定 ({{ suiteForm.bindings.length }})</h3>
          <el-button size="small" :icon="Plus" @click="addSuiteBinding">添加维度绑定</el-button>
        </div>

        <div
          v-for="(b, bIdx) in suiteForm.bindings"
          :key="bIdx"
          class="ev-binding-row ev-panel"
        >
          <el-select v-model="b.dimension_code" style="width: 220px">
            <el-option
              v-for="d in latestDimensionsByCode.filter((x) => x.status === 'ACTIVE')"
              :key="d.dimension_code"
              :label="`${d.dimension_code} (${d.dimension_name})`"
              :value="d.dimension_code"
            />
          </el-select>
          <el-select v-model="b.version" style="width: 100px">
            <el-option
              v-for="verRow in activeDimensionsForSuite.filter(
                (x) => x.dimension_code === b.dimension_code,
              )"
              :key="verRow.version"
              :label="`v${verRow.version}`"
              :value="verRow.version"
            />
          </el-select>
          <span class="ev-cell-secondary">阈值</span>
          <el-input-number v-model="b.threshold" :min="0" :max="1" :step="0.05" :precision="2" size="small" />
          <span class="ev-cell-secondary">权重</span>
          <el-input-number v-model="b.weight" :min="0.1" :max="10" :step="0.5" :precision="1" size="small" />
          <el-checkbox v-model="b.is_blocker">一票否决</el-checkbox>
          <el-button
            v-if="suiteForm.bindings.length > 1"
            size="small"
            link
            @click="suiteForm.bindings.splice(bIdx, 1)"
          >
            移除
          </el-button>
        </div>
      </el-form>

      <template #footer>
        <div class="ev-drawer-footer">
          <el-button @click="suiteDrawerOpen = false">取消</el-button>
          <el-button type="primary" :loading="savingSuite" @click="handleSaveSuite">
            保存评测套件
          </el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.ev-section-mt {
  margin-top: 16px;
}

.ev-editor-title-group {
  display: flex;
  align-items: center;
  gap: 14px;
}

.ev-editor-mobile-only {
  display: none;
}

.ev-narrow-editor-notice {
  display: none;
  font-size: var(--ev-fs-label);
  color: var(--ev-color-warning);
}

.ev-upgrade-banner {
  display: flex;
  align-items: center;
  gap: 12px;
  background-color: var(--ev-color-warning-subtle);
  border-color: var(--ev-color-warning-border);
  color: var(--ev-text-primary);
  font-size: var(--ev-fs-body);
}

.ev-workbench-columns {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(320px, 2fr);
  gap: 16px;
  align-items: start;
}

.ev-sticky-box {
  position: sticky;
  top: 68px;
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

.ev-questions-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.ev-q-card {
  margin-bottom: 12px;
}

.ev-q-card__top {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.ev-q-index {
  font-weight: 700;
  color: var(--ev-color-primary);
}

.ev-q-mt {
  margin-top: 10px;
  margin-bottom: 10px;
}

.ev-tier-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ev-tier-hint {
  font-size: var(--ev-fs-label);
  color: var(--ev-text-secondary);
  line-height: 1.5;
}

.ev-tier-row,
.ev-choice-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.ev-tier-badge {
  padding: 3px 8px;
  border-radius: var(--ev-radius-sm);
  font-size: var(--ev-fs-label);
  background-color: var(--ev-bg-card);
  border: 1px solid var(--ev-border-subtle);
  color: var(--ev-text-secondary);
  white-space: nowrap;
}

.ev-tier-badge.is-worst {
  color: var(--ev-color-danger);
  background-color: var(--ev-color-danger-subtle);
  border-color: var(--ev-color-danger-border);
}

.ev-tier-badge.is-best {
  color: var(--ev-color-success);
  background-color: var(--ev-color-success-subtle);
  border-color: var(--ev-color-success-border);
}

.ev-na-pill {
  font-size: var(--ev-fs-label);
  padding: 2px 8px;
  border-radius: var(--ev-radius-sm);
  color: var(--ev-color-neutral);
  background-color: var(--ev-color-neutral-subtle);
  border: 1px solid var(--ev-color-neutral-border);
}

.ev-editor-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid var(--ev-border-subtle);
}

.ev-tooltip-wrap {
  display: inline-flex;
}

.ev-linter-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}

.ev-linter-summary {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.ev-lint-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ev-lint-item__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.ev-lint-item__msg {
  margin: 6px 0 0;
  font-size: var(--ev-fs-label);
  color: var(--ev-text-primary);
  line-height: 1.5;
}

.ev-state-preview {
  margin: 8px 0 0;
  padding: 10px 12px;
  background-color: var(--ev-bg-panel);
  border: 1px solid var(--ev-border-subtle);
  border-radius: var(--ev-radius-md);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 280px;
  overflow-y: auto;
}

.ev-cell-primary {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ev-cell-secondary {
  font-size: var(--ev-fs-label);
  color: var(--ev-text-secondary);
}

.ev-ver-pill {
  display: inline-block;
  padding: 1px 7px;
  border-radius: var(--ev-radius-sm);
  font-size: var(--ev-fs-label);
  background-color: var(--ev-color-primary-subtle);
  color: var(--ev-color-primary);
  border: 1px solid var(--ev-color-primary-border);
}

.ev-binding-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.ev-binding-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.ev-drawer-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 1023px) {
  .ev-workbench-columns {
    grid-template-columns: 1fr;
  }
  .ev-workbench-right {
    display: none;
  }
  .ev-editor-mobile-only {
    display: block;
  }
}

@media (max-width: 767px) {
  .ev-grid-2,
  .ev-grid-3 {
    grid-template-columns: 1fr;
  }
  .ev-narrow-editor-notice {
    display: block;
  }
}
</style>
