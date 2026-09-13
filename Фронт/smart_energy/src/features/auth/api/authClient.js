import { API_BASE, refresh } from './authApi.js'
import { clearAccessToken, getAccessToken, setAccessToken } from '../model/authStore.js'
import { buildApiUrl } from '../../../shared/api/config.js'

let refreshPromise = null

export async function refreshAccessToken() {
  if (!refreshPromise) {
    refreshPromise = refresh()
      .then(async (response) => {
        if (!response.ok) {
          throw new Error('Не удалось обновить сессию')
        }

        const data = await response.json()
        setAccessToken(data.access)
        return data.access
      })
      .finally(() => {
        refreshPromise = null
      })
  }

  return refreshPromise
}

export async function apiRequest(path, options = {}, retry = true) {
  const headers = new Headers(options.headers ?? {})
  const accessToken = getAccessToken()

  if (accessToken) {
    headers.set('Authorization', `Bearer ${accessToken}`)
  }

  const response = await fetch(buildApiUrl(`${API_BASE}${path}`), {
    ...options,
    headers,
    credentials: 'include',
  })

  if (response.status !== 401 || !retry || path === '/refresh/') {
    return response
  }

  try {
    await refreshAccessToken()
  } catch {
    clearAccessToken()
    return response
  }

  const nextHeaders = new Headers(options.headers ?? {})
  const nextAccessToken = getAccessToken()

  if (nextAccessToken) {
    nextHeaders.set('Authorization', `Bearer ${nextAccessToken}`)
  }

  return fetch(buildApiUrl(`${API_BASE}${path}`), {
    ...options,
    headers: nextHeaders,
    credentials: 'include',
  })
}
