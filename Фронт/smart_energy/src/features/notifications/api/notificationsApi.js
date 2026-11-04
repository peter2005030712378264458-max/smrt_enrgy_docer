import { refreshAccessToken } from '../../auth/api/authClient.js'
import { getAccessToken } from '../../auth/model/authStore.js'
import { buildApiUrl } from '../../../shared/api/config.js'

export async function notificationsRequest(path, payload, retry = true, method = 'PATCH') {
  const headers = { Authorization: `Bearer ${getAccessToken() ?? ''}` }
  if (payload !== undefined) headers['Content-Type'] = 'application/json'
  const response = await fetch(buildApiUrl(`/notifications/${path}`), {
    method: payload === undefined ? 'GET' : method, headers, credentials: 'include',
    body: payload === undefined ? undefined : JSON.stringify(payload),
  })
  if (response.status === 401 && retry) {
    await refreshAccessToken()
    return notificationsRequest(path, payload, false, method)
  }
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = data.detail ?? Object.values(data)[0] ?? 'Не удалось загрузить уведомления'
    throw new Error(Array.isArray(error) ? error.join(' ') : String(error))
  }
  return data
}

export function previewNotifications(payload) {
  return notificationsRequest("preview/", payload, true, "POST")
}
