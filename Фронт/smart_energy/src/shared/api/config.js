export const API_ROOT = import.meta.env.VITE_API_URL ?? '/api'

export function buildApiUrl(path) {
  return `${API_ROOT}${path}`
}
