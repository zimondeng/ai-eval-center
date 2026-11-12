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
})
