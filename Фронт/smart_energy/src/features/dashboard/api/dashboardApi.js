import { refreshAccessToken } from '../../auth/api/authClient.js'
import { clearAccessToken, getAccessToken } from '../../auth/model/authStore.js'
import { buildApiUrl } from '../../../shared/api/config.js'

const DASHBOARD_API_BASE = '/consumption/dashboard'

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

async function dashboardRequest(path, options = {}, retry = true) {
  const headers = new Headers(options.headers ?? {})
  const accessToken = getAccessToken()

  if (accessToken) {
    headers.set('Authorization', `Bearer ${accessToken}`)
  }

  const response = await fetch(buildApiUrl(`${DASHBOARD_API_BASE}${path}`), {
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

  return dashboardRequest(path, options, false)
}

async function getJson(path) {
  const response = await dashboardRequest(path)
  const data = await response.json().catch(() => ({}))

  if (!response.ok) {
    throw new Error(data.detail ?? data.error ?? 'Не удалось загрузить данные дашборда')
  }

  return data
}

export function getDashboardFilters() {
  return getJson('/filters/')
}

export function getDashboardSummary(params) {
  return getJson(`/summary/${buildQuery(params)}`)
}

export function getDashboardTimeseries(params) {
  return getJson(`/timeseries/${buildQuery(params)}`)
}

export function getTopDevices(params) {
  return getJson(`/top-devices/${buildQuery(params)}`)
}

export function getRoomLoads(params) {
  return getJson(`/room-loads/${buildQuery(params)}`)
}

export function getDeviceDetail(dataName, params) {
  return getJson(`/devices/${encodeURIComponent(dataName)}/${buildQuery(params)}`)
}
