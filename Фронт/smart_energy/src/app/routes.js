export const ROUTES = Object.freeze({
  root: '/',
  login: '/login',
  register: '/register',
  dashboard: '/dashboard',
  connections: '/connections',
  analytics: '/analytics',
  aggregation: '/aggregation',
  notifications: '/notifications',
})

export const DASHBOARD_ROUTES = Object.freeze({
  [ROUTES.dashboard]: 'dashboard',
  [ROUTES.connections]: 'links',
  [ROUTES.analytics]: 'analytics',
  [ROUTES.aggregation]: 'aggregation',
  [ROUTES.notifications]: 'notifications',
})
