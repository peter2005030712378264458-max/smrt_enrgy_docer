let accessToken = null
const listeners = new Set()

function notifyListeners() {
  listeners.forEach((listener) => listener(accessToken))
}

export function getAccessToken() {
  return accessToken
}

export function setAccessToken(token) {
  accessToken = token ?? null
  notifyListeners()
}

export function clearAccessToken() {
  accessToken = null
  notifyListeners()
}

export function subscribeAccessToken(listener) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
