import api from './api'

export async function getMe() {
  const { data } = await api.get('/users/me')
  return data
}

export async function updateProfile(profile) {
  const { data } = await api.put('/users/me', profile)
  return data
}

export async function updatePassword(passwords) {
  const { data } = await api.put('/users/me/password', passwords)
  return data
}

export async function getNotificationPrefs() {
  const { data } = await api.get('/users/me/notifications')
  return data
}

export async function updateNotificationPrefs(preferences) {
  const { data } = await api.put('/users/me/notifications', preferences)
  return data
}
