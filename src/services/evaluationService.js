import api from './api'

export async function getEvaluations(employeeId) {
  const params = employeeId === undefined || employeeId === null ? {} : { employee_id: employeeId }
  const { data } = await api.get('/evaluations', { params })
  return data
}

export async function createEvaluation(evaluation) {
  const { data } = await api.post('/evaluations', evaluation)
  return data
}
