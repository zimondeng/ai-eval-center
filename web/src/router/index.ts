import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/authStore'
import AppShell from '@/layouts/AppShell.vue'

export const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/login/LoginView.vue'),
    meta: { public: true, title: '管理员登录' },
  },
  {
    path: '/',
    component: AppShell,
    redirect: '/runs',
    children: [
      {
        path: 'providers',
        name: 'providers',
        component: () => import('@/views/providers/ProvidersView.vue'),
        meta: { title: '渠道与适配器', domain: '渠道与适配器' },
      },
      {
        path: 'dimensions',
        name: 'dimensions',
        component: () => import('@/views/dimensions/DimensionsView.vue'),
        meta: { title: '评价维度与套件', domain: '评价维度与套件' },
      },
      {
        path: 'datasets',
        name: 'datasets',
        component: () => import('@/views/datasets/DatasetsView.vue'),
        meta: { title: '测试集与校准', domain: '测试集与校准' },
      },
      {
        path: 'runs',
        name: 'runs',
        component: () => import('@/views/runs/RunsView.vue'),
        meta: { title: '实验批次', domain: '实验批次' },
      },
      {
        path: 'reports/:runNo?',
        name: 'reports',
        component: () => import('@/views/reports/ReportsView.vue'),
        meta: { title: '回归与阈值报告', domain: '回归与阈值报告' },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/runs',
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  const authStore = useAuthStore()
  if (to.meta.public) {
    if (authStore.isAuthenticated) {
      return { path: '/runs' }
    }
    return true
  }

  if (!authStore.isAuthenticated) {
    return {
      path: '/login',
      query: to.fullPath && to.fullPath !== '/' ? { redirect: to.fullPath } : undefined,
    }
  }

  if (!authStore.hydrated) {
    const ok = await authStore.hydrateSession()
    if (!ok) {
      return {
        path: '/login',
        query: to.fullPath && to.fullPath !== '/' ? { redirect: to.fullPath } : undefined,
      }
    }
  }

  return true
})

export default router
