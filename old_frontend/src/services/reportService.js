import api from './api'

export async function getSummary() {
  const { data } = await api.get('/reports/summary')
  return data
}

export async function getPerformanceTrend() {
  const { data } = await api.get('/reports/performance-trend')
  return data
}
