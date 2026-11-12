import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiRequestError,
  apiRequest,
  formatReadableError,
  getStoredToken,
  setStoredToken,
  setUnauthorizedHandler,
} from '@/api/http'

describe('apiRequest envelope contract & 401 session handling', () => {
  beforeEach(() => {
    setStoredToken('tok_test_admin')
  })

  afterEach(() => {
    setStoredToken(null)
    setUnauthorizedHandler(null)
    vi.restoreAllMocks()
  })

  it('unwraps { code, message, request_id, data } and attaches Bearer token', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          code: 0,
          message: 'ok',
          request_id: 'req_abc123',
          data: { version: '1.0.0' },
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    const result = await apiRequest<{ version: string }>('/health')
    expect(result).toEqual({ version: '1.0.0' })
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/v1/health',
      expect.objectContaining({
        method: 'GET',
        headers: expect.objectContaining({
          Authorization: 'Bearer tok_test_admin',
        }),
      }),
    )
  })

  it('clears stored token, invokes unauthorizedHandler, and surfaces request_id on HTTP 401', async () => {
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)

    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          code: 40101,
          message: '登录态已过期，请重新登录',
          request_id: 'req_expired_99',
          data: null,
        }),
        { status: 401, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    let caught: unknown = null
    try {
      await apiRequest('/providers')
    } catch (err) {
      caught = err
    }

    expect(caught).toBeInstanceOf(ApiRequestError)
    expect(getStoredToken()).toBeNull()
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
    expect(formatReadableError(caught)).toBe('登录态已过期，请重新登录（追踪 ID: req_expired_99）')
  })
})
