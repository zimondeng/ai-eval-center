import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { authApi } from '@/api/endpoints'
import {
  AUTH_USER_STORAGE_KEY,
  getStoredToken,
  setStoredToken,
} from '@/api/http'
import type { AdminUserProfile } from '@/api/types'

function readStoredUser(): AdminUserProfile | null {
  try {
    const raw = localStorage.getItem(AUTH_USER_STORAGE_KEY)
    return raw ? (JSON.parse(raw) as AdminUserProfile) : null
  } catch {
    return null
  }
}

function writeStoredUser(user: AdminUserProfile | null): void {
  try {
    if (user) {
      localStorage.setItem(AUTH_USER_STORAGE_KEY, JSON.stringify(user))
    } else {
      localStorage.removeItem(AUTH_USER_STORAGE_KEY)
    }
  } catch {
    // ignore storage errors
  }
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(getStoredToken())
  const user = ref<AdminUserProfile | null>(readStoredUser())
  const hydrated = ref(false)

  const isAuthenticated = computed(() => Boolean(token.value))

  async function login(username: string, password: string): Promise<AdminUserProfile> {
    const res = await authApi.login(username, password)
    token.value = res.access_token
    user.value = res.user
    hydrated.value = true
    setStoredToken(res.access_token)
    writeStoredUser(res.user)
    return res.user
  }

  async function hydrateSession(): Promise<boolean> {
    if (!token.value) {
      hydrated.value = true
      return false
    }
    try {
      const profile = await authApi.me()
      user.value = profile
      writeStoredUser(profile)
      hydrated.value = true
      return true
    } catch {
      clearSession()
      hydrated.value = true
      return false
    }
  }

  function clearSession(): void {
    token.value = null
    user.value = null
    setStoredToken(null)
    writeStoredUser(null)
  }

  return {
    token,
    user,
    hydrated,
    isAuthenticated,
    login,
    hydrateSession,
    clearSession,
  }
})
