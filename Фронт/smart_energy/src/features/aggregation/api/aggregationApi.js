import { refreshAccessToken } from '../../auth/api/authClient.js'
import { getAccessToken } from '../../auth/model/authStore.js'
import { buildApiUrl } from '../../../shared/api/config.js'

export async function aggregationRequest(path, payload, retry = true) {
  const headers = { Authorization: `Bearer ${getAccessToken() ?? ''}` }
  if (payload !== undefined) headers['Content-Type'] = 'application/json'
  const response = await fetch(buildApiUrl(`/aggregation/${path}/`), {
    method: payload === undefined ? 'GET' : 'POST',
    headers,
    credentials: 'include',
    body: payload === undefined ? undefined : JSON.stringify(payload),
  })
  if (response.status === 401 && retry) {
    await refreshAccessToken()
    return aggregationRequest(path, payload, false)
  }
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail ?? 'Не удалось выполнить операцию адаптера')
  return data
}

export async function getSourceTables(connection, retry = true) {
  const response = await fetch(buildApiUrl(`/aggregation/tables/?connection=${encodeURIComponent(connection)}`), {
    credentials: 'include', headers: { Authorization: `Bearer ${getAccessToken() ?? ''}` },
  })
  if (response.status === 401 && retry) {
    await refreshAccessToken()
    return getSourceTables(connection, false)
  }
  const data = await response.json()
  if (!response.ok) throw new Error(data.detail ?? 'Не удалось загрузить таблицы')
  return data
}
