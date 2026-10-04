import api from './api'

export async function getEmployees(params = {}) {
  const { data } = await api.get('/employees', { params })
  return data
}

export async function getEmployee(id) {
  const { data } = await api.get(`/employees/${id}`)
  return data
}

export async function createEmployee(employee) {
  const { data } = await api.post('/employees', employee)
  return data
}

export async function updateEmployee(id, employee) {
  const { data } = await api.put(`/employees/${id}`, employee)
  return data
}
