export interface EvChartPalette {
  primary: string
  primarySubtle: string
  successSolid: string
  successSubtle: string
  warningSolid: string
  warningSubtle: string
  dangerSolid: string
  dangerSubtle: string
  splitDev: string
  splitDevSubtle: string
  textPrimary: string
  textSecondary: string
  textTertiary: string
  borderSubtle: string
  cardBg: string
}

export function resolveChartPalette(): EvChartPalette {
  const styles =
    typeof window !== 'undefined' && typeof window.getComputedStyle === 'function'
      ? window.getComputedStyle(document.documentElement)
      : null

  const readToken = (name: string, fallback: string): string => {
    const raw = styles?.getPropertyValue(name)?.trim()
    return raw && raw.length > 0 ? raw : fallback
  }

  return {
    primary: readToken('--ev-color-primary', '#1D4ED8'),
    primarySubtle: readToken('--ev-color-primary-subtle', '#EFF6FF'),
    successSolid: readToken('--ev-color-success-solid', '#16A34A'),
    successSubtle: readToken('--ev-color-success-subtle', '#F0FDF4'),
    warningSolid: readToken('--ev-color-warning-solid', '#D97706'),
    warningSubtle: readToken('--ev-color-warning-subtle', '#FFFBEB'),
    dangerSolid: readToken('--ev-color-danger-solid', '#DC2626'),
    dangerSubtle: readToken('--ev-color-danger-subtle', '#FEF2F2'),
    splitDev: readToken('--ev-color-split-dev', '#0369A1'),
    splitDevSubtle: readToken('--ev-color-split-dev-subtle', '#F0F9FF'),
    textPrimary: readToken('--ev-text-primary', '#0F172A'),
    textSecondary: readToken('--ev-text-secondary', '#475569'),
    textTertiary: readToken('--ev-text-tertiary', '#64748B'),
    borderSubtle: readToken('--ev-border-subtle', '#E2E8F0'),
    cardBg: readToken('--ev-bg-card', '#FFFFFF'),
  }
}

export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false
  }
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}
