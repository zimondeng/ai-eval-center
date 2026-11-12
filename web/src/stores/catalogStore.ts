import { ref } from 'vue'
import { defineStore } from 'pinia'
import {
  adaptersApi,
  datasetsApi,
  dimensionsApi,
  providersApi,
  suitesApi,
} from '@/api/endpoints'
import type {
  DatasetRecord,
  DecisionProviderRecord,
  DimensionRecord,
  SuiteRecord,
  SutAdapterRecord,
} from '@/api/types'

export const useCatalogStore = defineStore('catalog', () => {
  const providers = ref<DecisionProviderRecord[]>([])
  const adapters = ref<SutAdapterRecord[]>([])
  const dimensions = ref<DimensionRecord[]>([])
  const suites = ref<SuiteRecord[]>([])
  const datasets = ref<DatasetRecord[]>([])
  const loading = ref(false)

  async function refreshAll(): Promise<void> {
    loading.value = true
    try {
      const [p, a, d, s, ds] = await Promise.all([
        providersApi.list(),
        adaptersApi.list(),
        dimensionsApi.list({ latest_only: false }),
        suitesApi.list(),
        datasetsApi.list(),
      ])
      providers.value = p
      adapters.value = a
      dimensions.value = d
      suites.value = s
      datasets.value = ds
    } finally {
      loading.value = false
    }
  }

  async function refreshProviders(): Promise<void> {
    providers.value = await providersApi.list()
  }

  async function refreshAdapters(): Promise<void> {
    adapters.value = await adaptersApi.list()
  }

  async function refreshDimensions(): Promise<void> {
    dimensions.value = await dimensionsApi.list({ latest_only: false })
  }

  async function refreshSuites(): Promise<void> {
    suites.value = await suitesApi.list()
  }

  async function refreshDatasets(): Promise<void> {
    datasets.value = await datasetsApi.list()
  }

  return {
    providers,
    adapters,
    dimensions,
    suites,
    datasets,
    loading,
    refreshAll,
    refreshProviders,
    refreshAdapters,
    refreshDimensions,
    refreshSuites,
    refreshDatasets,
  }
})
