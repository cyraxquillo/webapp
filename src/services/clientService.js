import api from './api'

export async function getMyClient() {
  const { data } = await api.get('/clients/me')
  return data
}
