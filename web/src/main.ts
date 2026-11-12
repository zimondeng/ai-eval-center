import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './styles/tokens.css'
import './styles/base.css'

import App from './App.vue'
import router from './router'
import { setUnauthorizedHandler } from './api/http'
import { useAuthStore } from './stores/authStore'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(ElementPlus)
app.use(router)

setUnauthorizedHandler(() => {
  const authStore = useAuthStore(pinia)
  authStore.clearSession()
  if (router.currentRoute.value.path !== '/login') {
    void router.push({
      path: '/login',
      query: { redirect: router.currentRoute.value.fullPath },
    })
  }
})

app.mount('#app')
