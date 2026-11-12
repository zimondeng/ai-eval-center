<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { Lock, User } from '@element-plus/icons-vue'
import { formatReadableError } from '@/api/http'
import { useAuthStore } from '@/stores/authStore'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

const formRef = ref<FormInstance>()
const submitting = ref(false)
const serverError = ref<string | null>(null)

const form = reactive({
  username: 'admin',
  password: '',
})

const rules: FormRules<typeof form> = {
  username: [{ required: true, message: '请输入管理员账号', trigger: 'blur' }],
  password: [{ required: true, message: '请输入登录密码', trigger: 'blur' }],
}

async function handleSubmit(): Promise<void> {
  if (!formRef.value || submitting.value) return
  serverError.value = null

  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    await authStore.login(form.username, form.password)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/runs'
    await router.replace(redirect)
  } catch (err) {
    serverError.value = formatReadableError(err)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="ev-login-page">
    <section class="ev-login-card ev-surface" aria-labelledby="login-heading">
      <div class="ev-login-header">
        <div class="ev-login-brand">
          <span class="ev-login-mark ev-mono">EV</span>
          <h1 id="login-heading" class="ev-page-title">AI 评测中心登录</h1>
        </div>
        <p class="ev-page-subtitle">
          确定性概率评测、组件回放快照与防过拟合门禁工作台
        </p>
      </div>

      <el-alert
        v-if="serverError"
        :title="serverError"
        type="error"
        show-icon
        :closable="false"
        class="ev-login-alert"
      />

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        @submit.prevent="handleSubmit"
      >
        <el-form-item label="管理员账号" prop="username">
          <el-input
            v-model="form.username"
            placeholder="请输入账号（如 admin）"
            autocomplete="username"
          >
            <template #prefix>
              <el-icon><User /></el-icon>
            </template>
          </el-input>
        </el-form-item>

        <el-form-item label="登录密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            placeholder="请输入管理员密码"
            autocomplete="current-password"
          >
            <template #prefix>
              <el-icon><Lock /></el-icon>
            </template>
          </el-input>
        </el-form-item>

        <el-button
          type="primary"
          native-type="submit"
          :loading="submitting"
          class="ev-login-submit"
        >
          登录评测控制台
        </el-button>
      </el-form>

      <footer class="ev-login-footer ev-panel">
        <span>本地自动化流水线请使用渠道与适配器中心签发的</span>
        <code class="ev-mono">evk_live_*</code>
        <span>密钥访问接口。</span>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.ev-login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background-color: var(--ev-bg-page);
}

.ev-login-card {
  width: 100%;
  max-width: 420px;
  padding: 28px 28px 24px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.ev-login-brand {
  display: flex;
  align-items: center;
  gap: 10px;
}

.ev-login-mark {
  width: 30px;
  height: 30px;
  border-radius: var(--ev-radius-md);
  background-color: var(--ev-color-primary);
  color: var(--ev-text-inverse);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
}

.ev-login-alert {
  margin-bottom: 4px;
}

.ev-login-submit {
  width: 100%;
  margin-top: 6px;
}

.ev-login-footer {
  font-size: var(--ev-fs-label);
  color: var(--ev-text-secondary);
  line-height: 1.5;
}
</style>
