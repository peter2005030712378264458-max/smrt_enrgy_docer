import { refreshAccessToken } from '../../auth/api/authClient.js'
import { clearAccessToken, getAccessToken } from '../../auth/model/authStore.js'
import { buildApiUrl } from '../../../shared/api/config.js'

const ANALYTICS_API_BASE = '/consumption/analytics'

function buildQuery(params = {}) {
  const query = new URLSearchParams()

  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '' || value === 'all') {
      return
    }

    query.set(key, value)
  })

  const queryString = query.toString()
  return queryString ? `?${queryString}` : ''
}

async function analyticsRequest(path, options = {}, retry = true) {
  const headers = new Headers(options.headers ?? {})
  const accessToken = getAccessToken()

  if (accessToken) {
    headers.set('Authorization', `Bearer ${accessToken}`)
  }

  const response = await fetch(buildApiUrl(`${ANALYTICS_API_BASE}${path}`), {
    ...options,
    headers,
    credentials: 'include',
  })

  if (response.status !== 401 || !retry) {
    return response
  }

  try {
    await refreshAccessToken()
  } catch {
    clearAccessToken()
    return response
  }

  return analyticsRequest(path, options, false)
}

async function getJson(path) {
  const response = await analyticsRequest(path)
  const data = await response.json().catch(() => ({}))

  if (!response.ok) {
    throw new Error(data.detail ?? data.error ?? 'Не удалось выполнить аналитический расчет')
  }

  return data
}

export function getPeriodComparison(params) {
  return getJson(`/period-comparison/${buildQuery(params)}`)
}
