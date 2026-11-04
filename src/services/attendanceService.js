import api from './api'

export async function getAttendance() {
  const { data } = await api.get('/attendance')
  return data
}

export async function timeIn(location) {
  const { data } = await api.post('/attendance/time-in', location)
  return data
}

export async function timeOut(location) {
  const { data } = await api.post('/attendance/time-out', location)
  return data
}

export async function getOfficeLocations() {
  const { data } = await api.get('/office-locations')
  return data
}

export async function createOfficeLocation(location) {
  const { data } = await api.post('/office-locations', location)
  return data
}

export async function updateOfficeLocation(id, location) {
  const { data } = await api.patch(`/office-locations/${id}`, location)
  return data
}

export async function deleteOfficeLocation(id) {
  await api.delete(`/office-locations/${id}`)
}

export async function getNotifications() {
  const { data } = await api.get('/notifications')
  return data
}

export async function markNotificationRead(id) {
  const { data } = await api.post(`/notifications/${id}/read`)
  return data
}
