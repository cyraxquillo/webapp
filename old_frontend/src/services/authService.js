import api from './api'

export async function login(email, password) {
  const form = new URLSearchParams({ username: email, password })
  const { data } = await api.post('/auth/login', form, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    skipAuthRedirect: true,
  })
  return data
}

export async function getMe() {
  const { data } = await api.get('/auth/me')
  return data
}
