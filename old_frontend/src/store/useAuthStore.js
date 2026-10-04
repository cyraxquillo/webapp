import { create } from 'zustand'
import { getMe, login as requestLogin } from '../services/authService'
import { AUTH_TOKEN_KEY } from '../services/api'

export const roleHome = (role) => `/${role}/overview`

export const useAuthStore = create((set, get) => ({
  user: null,
  token: null,
  initialized: false,
  isInitializing: false,
  isLoggingIn: false,
  loginError: '',
  setUser: (user) => set({ user }),
  initialize: async () => {
    if (get().initialized || get().isInitializing) return
    set({ isInitializing: true })
    const token = localStorage.getItem(AUTH_TOKEN_KEY)
    if (!token) {
      set({ initialized: true, isInitializing: false })
      return
    }
    set({ token })
    try {
      const user = await getMe()
      if (get().token === token) set({ user, initialized: true, isInitializing: false })
    } catch {
      localStorage.removeItem(AUTH_TOKEN_KEY)
      set({ user: null, token: null, initialized: true, isInitializing: false })
    }
  },
  login: async (email, password) => {
    set({ isLoggingIn: true, loginError: '' })
    try {
      const tokenResponse = await requestLogin(email, password)
      localStorage.setItem(AUTH_TOKEN_KEY, tokenResponse.access_token)
      set({ token: tokenResponse.access_token, user: { email, role: tokenResponse.role } })
      const user = await getMe()
      set({ user, initialized: true })
      return user
    } catch (error) {
      localStorage.removeItem(AUTH_TOKEN_KEY)
      set({ user: null, token: null, loginError: error.response?.data?.detail || 'Unable to sign in. Please try again.' })
      throw error
    } finally {
      set({ isLoggingIn: false })
    }
  },
  logout: () => {
    localStorage.removeItem(AUTH_TOKEN_KEY)
    set({ user: null, token: null, loginError: '', initialized: true })
  },
}))

if (typeof window !== 'undefined') {
  window.addEventListener('lexie:session-expired', () => useAuthStore.getState().logout())
}
