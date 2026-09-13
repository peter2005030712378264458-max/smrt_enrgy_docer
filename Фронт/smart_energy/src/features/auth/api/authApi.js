import { buildApiUrl } from '../../../shared/api/config.js'

export const API_BASE = '/auth'

export async function login(payload) {
  return fetch(buildApiUrl(`${API_BASE}/login/`), {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })
}

export async function register(payload) {
  return fetch(buildApiUrl(`${API_BASE}/register/`), {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })
}

export async function refresh() {
  return fetch(buildApiUrl(`${API_BASE}/refresh/`), {
    method: 'POST',
    credentials: 'include',
  })
}

export async function logout() {
  return fetch(buildApiUrl(`${API_BASE}/logout/`), {
    method: 'POST',
    credentials: 'include', 
  })
}
