import axios from 'axios'

export const AUTH_TOKEN_KEY = 'lexie_access_token'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000',
  headers: { 'Content-Type': 'application/json' },
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem(AUTH_TOKEN_KEY)
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && !error.config?.skipAuthRedirect) {
      localStorage.removeItem(AUTH_TOKEN_KEY)
      window.dispatchEvent(new Event('lexie:session-expired'))
      if (window.location.pathname !== '/login') window.location.replace('/login')
    }
    return Promise.reject(error)
  },
)

export default api
