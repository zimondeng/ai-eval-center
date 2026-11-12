import type { ApiEnvelope } from './types'

export const AUTH_TOKEN_STORAGE_KEY = 'ev_console_access_token'
export const AUTH_USER_STORAGE_KEY = 'ev_console_admin_user'

export class ApiRequestError extends Error {
  readonly code: number
  readonly status: number
  readonly requestId: string | null
  readonly data: unknown

  constructor(options: {
    message: string
    code: number
    status: number
    requestId?: string | null
    data?: unknown
  }) {
    super(options.message)
    this.name = 'ApiRequestError'
    this.code = options.code
    this.status = options.status
    this.requestId = options.requestId ?? null
    this.data = options.data ?? null
  }

  get readableSummary(): string {
    if (this.requestId) {
      return `${this.message}（追踪 ID: ${this.requestId}）`
    }
    return this.message
  }
}

let unauthorizedHandler: (() => void) | null = null

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler
}

export function getStoredToken(): string | null {
  try {
    return localStorage.getItem(AUTH_TOKEN_STORAGE_KEY)
  } catch {
    return null
  }
}

export function setStoredToken(token: string | null): void {
  try {
    if (token) {
      localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token)
    } else {
      localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY)
    }
  } catch {
    // ignore storage errors in restricted environments
  }
}

function buildUrl(
  path: string,
  query?: Record<string, string | number | boolean | null | undefined>,
): string {
  const normalizedPath = path.startsWith('/api/')
    ? path
    : `/api/v1${path.startsWith('/') ? path : `/${path}`}`

  if (!query) return normalizedPath

  const params = new URLSearchParams()
  for (const [key, val] of Object.entries(query)) {
    if (val === undefined || val === null || val === '') continue
    params.set(key, String(val))
  }
  const qs = params.toString()
  return qs ? `${normalizedPath}?${qs}` : normalizedPath
}

export async function apiRequest<T>(
  path: string,
  options: {
    method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
    query?: Record<string, string | number | boolean | null | undefined>
    body?: unknown
    skipAuth?: boolean
  } = {},
): Promise<T> {
  const { method = 'GET', query, body, skipAuth = false } = options
  const headers: Record<string, string> = {
    Accept: 'application/json',
  }

  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }

  if (!skipAuth) {
    const token = getStoredToken()
    if (token) {
      headers.Authorization = `Bearer ${token}`
    }
  }

  const url = buildUrl(path, query)
  let response: Response
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiRequestError({
      message: '网络连接异常，请检查后端服务是否运行于 18200 端口',
      code: 0,
      status: 0,
    })
  }

  const headerReqId = response.headers.get('X-Request-ID')
  let parsed: Partial<ApiEnvelope<T>> | null = null
  try {
    parsed = (await response.json()) as Partial<ApiEnvelope<T>>
  } catch {
    parsed = null
  }

  const reqId = (parsed && typeof parsed.request_id === 'string' && parsed.request_id) || headerReqId

  if (response.status === 401 && !skipAuth) {
    setStoredToken(null)
    if (unauthorizedHandler) {
      unauthorizedHandler()
    }
  }

  if (!response.ok || (parsed && typeof parsed.code === 'number' && parsed.code >= 400)) {
    const safeMessage =
      (parsed && typeof parsed.message === 'string' && parsed.message.trim()) ||
      `请求失败 (HTTP ${response.status})`
    throw new ApiRequestError({
      message: safeMessage,
      code: parsed?.code ?? response.status,
      status: response.status,
      requestId: reqId,
      data: parsed?.data,
    })
  }

  if (!parsed || !('data' in parsed)) {
    throw new ApiRequestError({
      message: '服务端响应缺少标准包络字段',
      code: 500,
      status: response.status,
      requestId: reqId,
    })
  }

  return parsed.data as T
}

export function formatReadableError(err: unknown): string {
  if (err instanceof ApiRequestError) {
    return err.readableSummary
  }
  if (err instanceof Error && err.message) {
    return err.message
  }
  return '请求执行异常，请稍后重试'
}

export function formatScore(val: number | null | undefined, digits = 4): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '—'
  return Number(val).toFixed(digits)
}

export function formatPercent(val: number | null | undefined, digits = 1): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '—'
  return `${(Number(val) * 100).toFixed(digits)}%`
}

export function formatSignedDelta(
  val: number | null | undefined,
  mode: 'score' | 'percent' | 'ms' = 'score',
): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '—'
  const num = Number(val)
  const prefix = num > 0 ? '+' : ''
  if (mode === 'percent') {
    return `${prefix}${(num * 100).toFixed(2)}%`
  }
  if (mode === 'ms') {
    return `${prefix}${Math.round(num)} ms`
  }
  return `${prefix}${num.toFixed(4)}`
}

export function isPlainJsonObject(val: unknown): val is Record<string, unknown> {
  return typeof val === 'object' && val !== null && !Array.isArray(val)
}

export function validateOptionalJsonObject(
  _rule: unknown,
  value: string | null | undefined,
  callback: (err?: Error) => void,
): void {
  const trimmed = (value ?? '').trim()
  if (!trimmed) {
    callback()
    return
  }
  try {
    const parsed: unknown = JSON.parse(trimmed)
    if (!isPlainJsonObject(parsed)) {
      callback(new Error('必须为合法的 JSON 对象（如 {"key": "value"}）'))
      return
    }
    callback()
  } catch {
    callback(new Error('JSON 语法格式不合法，请检查括号与引号'))
  }
}

export function validateRequiredJsonObject(
  _rule: unknown,
  value: string | null | undefined,
  callback: (err?: Error) => void,
): void {
  const trimmed = (value ?? '').trim()
  if (!trimmed) {
    callback(new Error('请输入 JSON 对象配置'))
    return
  }
  validateOptionalJsonObject(_rule, trimmed, callback)
}
