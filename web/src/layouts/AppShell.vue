<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Connection,
  DataAnalysis,
  Expand,
  Files,
  Fold,
  Operation,
  SwitchButton,
  VideoPlay,
} from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/authStore'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

const isCollapsed = ref(false)
const isMobileViewport = ref(false)
const mobileNavOpen = ref(false)

interface NavDomainItem {
  path: string
  matchPrefix: string
  label: string
  icon: typeof Connection
}

const navItems: NavDomainItem[] = [
  { path: '/providers', matchPrefix: '/providers', label: '渠道与适配器', icon: Connection },
  { path: '/dimensions', matchPrefix: '/dimensions', label: '评价维度与套件', icon: Operation },
  { path: '/datasets', matchPrefix: '/datasets', label: '测试集与校准', icon: Files },
  { path: '/runs', matchPrefix: '/runs', label: '实验批次', icon: VideoPlay },
  { path: '/reports', matchPrefix: '/reports', label: '回归与阈值报告', icon: DataAnalysis },
]

const currentDomainTitle = computed(() => (route.meta.domain as string) || '实验批次')

function isNavActive(item: NavDomainItem): boolean {
  return route.path === item.path || route.path.startsWith(`${item.matchPrefix}/`)
}

function handleNavigate(path: string): void {
  void router.push(path)
  if (isMobileViewport.value) {
    mobileNavOpen.value = false
  }
}

function toggleSidebar(): void {
  if (isMobileViewport.value) {
    mobileNavOpen.value = !mobileNavOpen.value
  } else {
    isCollapsed.value = !isCollapsed.value
  }
}

function handleLogout(): void {
  authStore.clearSession()
  void router.push('/login')
}

function syncViewportMode(): void {
  if (typeof window === 'undefined') return
  const width = window.innerWidth
  isMobileViewport.value = width < 768
  if (width >= 768 && width < 1024) {
    isCollapsed.value = true
  } else if (width >= 1440) {
    isCollapsed.value = false
  }
}

onMounted(() => {
  syncViewportMode()
  window.addEventListener('resize', syncViewportMode)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', syncViewportMode)
})
</script>

<template>
  <div class="ev-shell">
    <aside
      v-if="!isMobileViewport"
      class="ev-sidebar"
      :class="{ 'ev-sidebar--collapsed': isCollapsed }"
      aria-label="主业务导航"
    >
      <div class="ev-sidebar__brand">
        <span class="ev-sidebar__mark ev-mono">EV</span>
        <span v-if="!isCollapsed" class="ev-sidebar__name">AI 评测中心</span>
      </div>

      <nav class="ev-sidebar__nav">
        <el-tooltip
          v-for="item in navItems"
          :key="item.path"
          :content="item.label"
          placement="right"
          :disabled="!isCollapsed"
        >
          <button
            type="button"
            class="ev-nav-item"
            :class="{ 'is-active': isNavActive(item) }"
            @click="handleNavigate(item.path)"
          >
            <el-icon class="ev-nav-item__icon">
              <component :is="item.icon" />
            </el-icon>
            <span v-if="!isCollapsed" class="ev-nav-item__label">{{ item.label }}</span>
          </button>
        </el-tooltip>
      </nav>

      <div v-if="!isCollapsed" class="ev-sidebar__footer">
        <span class="ev-sidebar__spec">确定性概率实验台</span>
        <span class="ev-sidebar__sub ev-mono">System One · v1.0</span>
      </div>
    </aside>

    <el-drawer
      v-model="mobileNavOpen"
      direction="ltr"
      size="240px"
      title="AI 评测中心导航"
      :with-header="true"
    >
      <nav class="ev-mobile-nav">
        <button
          v-for="item in navItems"
          :key="item.path"
          type="button"
          class="ev-nav-item"
          :class="{ 'is-active': isNavActive(item) }"
          @click="handleNavigate(item.path)"
        >
          <el-icon class="ev-nav-item__icon">
            <component :is="item.icon" />
          </el-icon>
          <span class="ev-nav-item__label">{{ item.label }}</span>
        </button>
      </nav>
    </el-drawer>

    <div class="ev-main-column">
      <header class="ev-topbar">
        <div class="ev-topbar__left">
          <el-tooltip :content="isCollapsed ? '展开左侧导航栏' : '收折左侧导航栏'" placement="bottom">
            <button
              type="button"
              class="ev-topbar__toggle ev-icon-btn"
              :aria-label="isCollapsed ? '展开左侧导航栏' : '收折左侧导航栏'"
              @click="toggleSidebar"
            >
              <el-icon>
                <Expand v-if="isCollapsed || isMobileViewport" />
                <Fold v-else />
              </el-icon>
            </button>
          </el-tooltip>

          <el-breadcrumb separator="/">
            <el-breadcrumb-item>AI 评测中心</el-breadcrumb-item>
            <el-breadcrumb-item>{{ currentDomainTitle }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>

        <div class="ev-topbar__right">
          <span class="ev-env-pill">标尺锁定 · 单端口托管</span>
          <span class="ev-user-pill">
            {{ authStore.user?.display_name || authStore.user?.username || '管理员' }}
          </span>
          <el-tooltip content="退出登录" placement="bottom">
            <button
              type="button"
              class="ev-topbar__logout ev-icon-btn"
              aria-label="退出登录"
              @click="handleLogout"
            >
              <el-icon><SwitchButton /></el-icon>
            </button>
          </el-tooltip>
        </div>
      </header>

      <main class="ev-content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<style scoped>
.ev-shell {
  display: flex;
  min-height: 100vh;
  background-color: var(--ev-bg-page);
}

.ev-sidebar {
  width: 220px;
  flex-shrink: 0;
  background-color: var(--ev-bg-card);
  border-right: 1px solid var(--ev-border-subtle);
  display: flex;
  flex-direction: column;
  transition: width var(--ev-duration-enter) var(--ev-ease-out);
  position: sticky;
  top: 0;
  height: 100vh;
}

.ev-sidebar--collapsed {
  width: 64px;
}

.ev-sidebar__brand {
  height: 52px;
  padding: 0 16px;
  display: flex;
  align-items: center;
  gap: 10px;
  border-bottom: 1px solid var(--ev-border-subtle);
}

.ev-sidebar__mark {
  width: 28px;
  height: 28px;
  border-radius: var(--ev-radius-md);
  background-color: var(--ev-color-primary);
  color: var(--ev-text-inverse);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: var(--ev-fs-label);
  font-weight: 700;
  flex-shrink: 0;
}

.ev-sidebar__name {
  font-size: 15px;
  font-weight: 600;
  color: var(--ev-text-primary);
  white-space: nowrap;
}

.ev-sidebar__nav {
  padding: 12px 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
}

.ev-mobile-nav {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.ev-nav-item {
  width: 100%;
  border: 1px solid transparent;
  background: transparent;
  color: var(--ev-text-secondary);
  border-radius: var(--ev-radius-md);
  padding: 9px 12px;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: var(--ev-fs-body);
  font-family: var(--ev-font-sans);
  cursor: pointer;
  text-align: left;
  transition:
    background-color var(--ev-duration-exit) var(--ev-ease-out),
    color var(--ev-duration-exit) var(--ev-ease-out);
}

.ev-sidebar--collapsed .ev-nav-item {
  justify-content: center;
  padding: 10px 0;
}

.ev-nav-item:hover {
  background-color: var(--ev-bg-panel);
  color: var(--ev-text-primary);
}

.ev-nav-item.is-active {
  background-color: var(--ev-color-primary-subtle);
  color: var(--ev-color-primary);
  font-weight: 600;
}

.ev-nav-item__icon {
  font-size: 17px;
  flex-shrink: 0;
}

.ev-nav-item__label {
  white-space: nowrap;
}

.ev-sidebar__footer {
  padding: 12px 16px;
  border-top: 1px solid var(--ev-border-subtle);
  background-color: var(--ev-bg-panel);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.ev-sidebar__spec {
  font-size: var(--ev-fs-label);
  color: var(--ev-text-secondary);
  font-weight: 500;
}

.ev-sidebar__sub {
  font-size: var(--ev-fs-label);
  color: var(--ev-text-tertiary);
}

.ev-main-column {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.ev-topbar {
  height: 52px;
  background-color: var(--ev-bg-card);
  border-bottom: 1px solid var(--ev-border-subtle);
  padding: 0 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  position: sticky;
  top: 0;
  z-index: 20;
}

.ev-topbar__left,
.ev-topbar__right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.ev-topbar__toggle,
.ev-topbar__logout {
  border: 1px solid var(--ev-border-subtle);
  background-color: var(--ev-bg-card);
  color: var(--ev-text-secondary);
  border-radius: var(--ev-radius-md);
  cursor: pointer;
}

.ev-topbar__toggle:hover,
.ev-topbar__logout:hover {
  background-color: var(--ev-bg-panel);
  color: var(--ev-text-primary);
}

.ev-env-pill {
  font-size: var(--ev-fs-label);
  font-weight: 500;
  color: var(--ev-text-secondary);
  background-color: var(--ev-bg-panel);
  border: 1px solid var(--ev-border-subtle);
  padding: 2px 8px;
  border-radius: var(--ev-radius-sm);
}

.ev-user-pill {
  font-size: var(--ev-fs-body);
  font-weight: 500;
  color: var(--ev-text-primary);
}

.ev-content {
  flex: 1;
  padding: 20px 24px;
  max-width: 100%;
  overflow-x: hidden;
}

@media (max-width: 767px) {
  .ev-topbar {
    padding: 0 12px;
  }
  .ev-env-pill {
    display: none;
  }
  .ev-content {
    padding: 14px 12px;
  }
}
</style>
