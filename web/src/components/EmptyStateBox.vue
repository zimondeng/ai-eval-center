<script setup lang="ts">
import { FolderOpened, Search } from '@element-plus/icons-vue'

withDefaults(
  defineProps<{
    filtered?: boolean
    entityName?: string
    description?: string
    actionLabel?: string
  }>(),
  {
    filtered: false,
    entityName: '记录',
    description: '',
    actionLabel: '',
  },
)

const emit = defineEmits<{
  (e: 'action'): void
  (e: 'reset'): void
}>()
</script>

<template>
  <div class="ev-empty-box">
    <el-icon class="ev-empty-box__icon">
      <Search v-if="filtered" />
      <FolderOpened v-else />
    </el-icon>
    <p class="ev-empty-box__text">
      <template v-if="description">{{ description }}</template>
      <template v-else-if="filtered">
        未找到符合当前筛选条件的用例，请放宽过滤项或点击「重置」。
      </template>
      <template v-else>
        暂无{{ entityName }}。可调整筛选条件，或点击右上角「新增{{ entityName }}」开始配置。
      </template>
    </p>
    <div class="ev-empty-box__actions">
      <el-button v-if="filtered" size="small" @click="emit('reset')">重置筛选</el-button>
      <el-button v-else-if="actionLabel" type="primary" size="small" @click="emit('action')">
        {{ actionLabel }}
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.ev-empty-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 36px 20px;
  text-align: center;
  color: var(--ev-text-secondary);
}

.ev-empty-box__icon {
  font-size: 28px;
  color: var(--ev-text-tertiary);
  margin-bottom: 10px;
}

.ev-empty-box__text {
  margin: 0 0 12px;
  font-size: var(--ev-fs-body);
  max-width: 56ch;
}

.ev-empty-box__actions {
  display: flex;
  gap: 8px;
}
</style>
