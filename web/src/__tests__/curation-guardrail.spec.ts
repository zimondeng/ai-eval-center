import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import CurationBenchPanel from '@/views/datasets/CurationBenchPanel.vue'
import { datasetsApi } from '@/api/endpoints'
import type { EvalCaseRecord } from '@/api/types'

vi.mock('@/api/endpoints', () => ({
  datasetsApi: {
    listCases: vi.fn(),
    curateCase: vi.fn(),
  },
}))

const UNMASKED_TOOLTIP_COPY =
  '当前用例尚未完成脱敏确认（pii_masked = 0），禁止置为「已审核生效」。请先执行脱敏清洗或人工核验无敏感信息。'

function makeCase(overrides: Partial<EvalCaseRecord>): EvalCaseRecord {
  return {
    id: 501,
    uuid: 'u-501',
    dataset_id: 12,
    case_code: 'case_unmasked_501',
    dataset_split: 'DEV',
    source_kind: 'FILE_IMPORT',
    source_ref_json: null,
    turn_mode: 'SINGLE_TURN',
    input_text: '请问常州灵活就业公积金开户需要哪些材料？',
    conversation_turns_json: null,
    expected_output: '需本人身份证及一类借记卡办理。',
    expected_outcome: null,
    reference_contexts_json: ['《常州市灵活就业人员参加住房公积金制度试点实施方案》'],
    expected_structured_json: { expected_agent_ids: ['cz_gjj_agent'] },
    sut_input_json: null,
    sut_context_json: null,
    has_snapshot: false,
    tags_json: null,
    difficulty: 'NORMAL',
    pii_masked: 0,
    desensitize_summary_json: null,
    uncertainty_score: 0.88,
    review_status: 'PENDING_REVIEW',
    reviewed_by: null,
    reviewed_at: null,
    review_note: null,
    create_time: '2026-11-12T08:00:00',
    update_time: '2026-11-12T08:00:00',
    ...overrides,
  }
}

describe('CurationBenchPanel pii_masked=0 guardrail (DESIGN.md §9 & §12)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('requests uncertainty_desc order and blocks approval with exact tooltip when pii_masked === 0', async () => {
    const unmaskedCase = makeCase({ id: 501, pii_masked: 0, uncertainty_score: 0.91 })
    const maskedCase = makeCase({
      id: 502,
      case_code: 'case_masked_502',
      pii_masked: 1,
      uncertainty_score: 0.42,
    })

    vi.mocked(datasetsApi.listCases).mockResolvedValue({
      items: [unmaskedCase, maskedCase],
      total: 2,
      page: 1,
      page_size: 50,
    })
    vi.mocked(datasetsApi.curateCase).mockImplementation(async (caseId, payload) => ({
      ...(caseId === 501 ? unmaskedCase : maskedCase),
      expected_output: payload.expected_output ?? '',
      pii_masked: Number(payload.pii_masked ?? 1),
      review_status: payload.review_status ?? 'APPROVED',
    }))

    const wrapper = mount(CurationBenchPanel, {
      props: {
        datasetId: 12,
        datasetName: '政务黄金测试集 v1',
      },
    })
    await flushPromises()

    expect(datasetsApi.listCases).toHaveBeenCalledWith(
      12,
      expect.objectContaining({
        sort: 'uncertainty_desc',
      }),
    )

    const approveBtn = wrapper.find('[data-testid="btn-approve-case"]')
    expect(approveBtn.exists()).toBe(true)
    expect(approveBtn.attributes('disabled')).toBeDefined()

    const guardWrap = wrapper.find('[data-testid="approve-guard-reason"]')
    expect(guardWrap.attributes('data-guard-message')).toBe(UNMASKED_TOOLTIP_COPY)

    await approveBtn.trigger('click')
    await flushPromises()
    expect(datasetsApi.curateCase).not.toHaveBeenCalled()

    // Select the already-desensitized case (pii_masked === 1) and verify approval succeeds
    const caseRows = wrapper.findAll('[data-testid="curation-case-item"]')
    expect(caseRows.length).toBe(2)
    await caseRows[1].trigger('click')
    await flushPromises()

    const enabledBtn = wrapper.find('[data-testid="btn-approve-case"]')
    expect(enabledBtn.attributes('disabled')).toBeUndefined()
    await enabledBtn.trigger('click')
    await flushPromises()

    expect(datasetsApi.curateCase).toHaveBeenCalledWith(
      502,
      expect.objectContaining({
        review_status: 'APPROVED',
        pii_masked: 1,
      }),
    )
  })
})
