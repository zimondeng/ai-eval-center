import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import SplitBadge from '@/components/SplitBadge.vue'
import DualStatusBadge from '@/components/DualStatusBadge.vue'

describe('SplitBadge & DualStatusBadge (DESIGN.md §4 Rules 3 & 4)', () => {
  it('renders DEV split with sky-blue isolation class and without lock icon', () => {
    const wrapper = mount(SplitBadge, {
      props: { split: 'DEV' },
    })

    expect(wrapper.classes()).toContain('ev-split-badge--dev')
    expect(wrapper.text()).toContain('DEV')
    expect(wrapper.text()).toContain('调优集')
    expect(wrapper.find('[data-testid="holdout-lock-icon"]').exists()).toBe(false)
  })

  it('renders HOLDOUT split with deep-teal isolation class and mandatory lock icon', () => {
    const wrapper = mount(SplitBadge, {
      props: { split: 'HOLDOUT' },
    })

    expect(wrapper.classes()).toContain('ev-split-badge--holdout')
    expect(wrapper.text()).toContain('HOLDOUT')
    expect(wrapper.text()).toContain('留出集')
    expect(wrapper.find('[data-testid="holdout-lock-icon"]').exists()).toBe(true)
  })

  it('enforces dual-coding status rule: symbol + Chinese label + tabular value', () => {
    const passed = mount(DualStatusBadge, {
      props: {
        tone: 'success',
        symbol: '✓',
        label: '门禁通过',
        value: '0.8640',
      },
    })
    expect(passed.classes()).toContain('ev-dual-badge--success')
    expect(passed.find('[data-testid="dual-symbol"]').text()).toBe('✓')
    expect(passed.find('[data-testid="dual-label"]').text()).toBe('门禁通过')
    expect(passed.find('[data-testid="dual-value"]').text()).toBe('0.8640')
    expect(passed.find('[data-testid="dual-value"]').classes()).toContain('ev-mono')

    const regressed = mount(DualStatusBadge, {
      props: {
        tone: 'danger',
        symbol: '↓',
        label: '较基线退化',
        value: '-3.60%',
      },
    })
    expect(regressed.classes()).toContain('ev-dual-badge--danger')
    expect(regressed.find('[data-testid="dual-symbol"]').text()).toBe('↓')
    expect(regressed.find('[data-testid="dual-label"]').text()).toBe('较基线退化')
    expect(regressed.find('[data-testid="dual-value"]').text()).toBe('-3.60%')
  })

  it('renders all 4 semantic regression tags with symbol + Chinese label + enum code (DESIGN.md §4 Rule 4 & §15 #7)', () => {
    const cases = [
      {
        tag: 'SAME' as const,
        symbol: '✓',
        expectedLabel: '实质一致 (SAME)',
        toneClass: 'ev-dual-badge--success',
      },
      {
        tag: 'SPECIFICITY_DIFFERS' as const,
        symbol: '~',
        expectedLabel: '详略差异 (SPECIFICITY_DIFFERS)',
        toneClass: 'ev-dual-badge--info',
      },
      {
        tag: 'ACTION_DIFFERS' as const,
        symbol: '!',
        expectedLabel: '指引变更 (ACTION_DIFFERS)',
        toneClass: 'ev-dual-badge--warning',
      },
      {
        tag: 'FACT_DIFFERS' as const,
        symbol: '✕',
        expectedLabel: '事实退化 (FACT_DIFFERS)',
        toneClass: 'ev-dual-badge--danger',
      },
    ]

    for (const item of cases) {
      const badge = mount(DualStatusBadge, {
        props: {
          regressionTag: item.tag,
        },
      })
      expect(badge.classes()).toContain(item.toneClass)
      expect(badge.find('[data-testid="dual-symbol"]').text()).toBe(item.symbol)
      expect(badge.find('[data-testid="dual-label"]').text()).toBe(item.expectedLabel)
      expect(badge.text()).toContain(`${item.symbol}${item.expectedLabel}`.replace(item.symbol, ''))
    }
  })
})
