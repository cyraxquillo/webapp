import api from './api'

export async function getPredictions(employeeId) {
  const params = employeeId === undefined || employeeId === null ? {} : { employee_id: employeeId }
  const { data } = await api.get('/predictions', { params })
  return data
}

export async function runPrediction(employeeId) {
  const { data } = await api.post(`/predictions/${employeeId}`)
  return data
}
