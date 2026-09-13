import { useEffect, useMemo, useState } from 'react'
import { NavLink, Outlet, useOutletContext } from 'react-router'
import {
  getDashboardFilters,
  getDashboardSummary,
  getDashboardTimeseries,
  getDeviceDetail,
  getRoomLoads,
  getTopDevices,
} from '../features/dashboard/api/dashboardApi.js'
import { getPeriodComparison } from '../features/analytics/api/analyticsApi.js'
import { ROUTES } from '../app/routes.js'
import ThemeToggle from '../shared/theme/ThemeToggle.jsx'
import {
  buildAvailableDates,
  buildDashboardParams,
  buildDefaultAnalyticsPeriods,
  buildPowerStats,
  buildSummaryFromTimeseries,
  getDeviceLabel,
  normalizeFilters,
  validateAnalyticsPeriods,
} from './dashboard/dashboardUtils.js'
import '../dashboard.css'

const NAV_ITEMS = [
  { id: 'dashboard', label: 'Дашборд', path: ROUTES.dashboard },
  { id: 'links', label: 'Связи', path: ROUTES.connections },
  { id: 'analytics', label: 'Аналитика', path: ROUTES.analytics },
]

function getUserName(currentUser) {
  const fullName = [currentUser?.first_name, currentUser?.last_name].filter(Boolean).join(' ')
  return fullName || currentUser?.email || 'Пользователь'
}

function getUserInitials(name) {
  return name.split(' ').filter(Boolean).map((part) => part[0]).join('').slice(0, 2).toUpperCase()
}

function SidebarIcon({ id }) {
  if (id === 'analytics') return <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 19h16" /><path d="M6 16l4-5 4 3 4-7" /><path d="M18 7h-4M18 7v4" /></svg>
  if (id === 'links') return <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M7 12h10M9 7h6M9 17h6" /><path d="M5 4h14v16H5z" /></svg>
  return <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 4h7v7H4zM13 4h7v4h-7zM13 10h7v10h-7zM4 13h7v7H4z" /></svg>
}

function DashboardPage() {
  const { currentUser, onLogout, preview = false } = useOutletContext()
  const [filters, setFilters] = useState(null)
  const [summary, setSummary] = useState(null)
  const [timeseries, setTimeseries] = useState([])
  const [topDevices, setTopDevices] = useState([])
  const [roomLoads, setRoomLoads] = useState([])
  const [deviceDetail, setDeviceDetail] = useState(null)
  const [selectedDataName, setSelectedDataName] = useState('all')
  const [selectedRoom, setSelectedRoom] = useState('all')
  const [selectedConsumerClass, setSelectedConsumerClass] = useState('all')
  const [period, setPeriod] = useState('24h')
  const [selectedDate, setSelectedDate] = useState('')
  const [analyticsPeriod1From, setAnalyticsPeriod1From] = useState('')
  const [analyticsPeriod1To, setAnalyticsPeriod1To] = useState('')
  const [analyticsPeriod2From, setAnalyticsPeriod2From] = useState('')
  const [analyticsPeriod2To, setAnalyticsPeriod2To] = useState('')
  const [analyticsAlpha, setAnalyticsAlpha] = useState('0.05')
  const [analyticsAlternative, setAnalyticsAlternative] = useState('two_sided')
  const [analyticsResult, setAnalyticsResult] = useState(null)
  const [analyticsLoading, setAnalyticsLoading] = useState(false)
  const [analyticsError, setAnalyticsError] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const userName = getUserName(currentUser)
  const availableDates = useMemo(() => buildAvailableDates(filters?.date_range), [filters?.date_range])
  const queryParams = useMemo(() => buildDashboardParams({ selectedDataName, selectedRoom, selectedConsumerClass, period, dateRange: filters?.date_range, selectedDate }), [filters?.date_range, period, selectedConsumerClass, selectedDataName, selectedDate, selectedRoom])
  const powerStats = useMemo(() => buildPowerStats(timeseries, summary), [summary, timeseries])
  const selectedDeviceLabel = selectedDataName === 'all' ? 'Все счетчики' : getDeviceLabel(filters?.devices.find((device) => device.data_name === selectedDataName), selectedDataName)

  useEffect(() => {
    let active = true
    async function loadFilters() {
      try {
        const nextFilters = await getDashboardFilters()
        if (active) {
          setFilters(normalizeFilters(nextFilters))
          setSelectedDate(nextFilters.date_range?.date_to?.slice(0, 10) ?? '')
          setError('')
        }
      } catch (requestError) {
        if (active) {
          setError(requestError.message)
          setLoading(false)
        }
      }
    }
    loadFilters()
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!filters || (period !== 'all' && !selectedDate)) return undefined
    let active = true
    async function loadDashboardData() {
      setLoading(true)
      try {
        const [summaryResult, timeseriesResult, topDevicesResult, roomLoadsResult] = await Promise.allSettled([
          getDashboardSummary(queryParams),
          getDashboardTimeseries({ ...queryParams, metric: 'active_power_w_avg' }),
          getTopDevices(queryParams),
          getRoomLoads(queryParams),
        ])
        const nextTimeseries = timeseriesResult.status === 'fulfilled' ? timeseriesResult.value : { points: [] }
        const nextTopDevices = topDevicesResult.status === 'fulfilled' ? topDevicesResult.value : []
        const nextRoomLoads = roomLoadsResult.status === 'fulfilled' ? roomLoadsResult.value : []
        const nextSummary = summaryResult.status === 'fulfilled' && summaryResult.value ? summaryResult.value : buildSummaryFromTimeseries(nextTimeseries.points ?? [], filters, queryParams)
        const nextDeviceDetail = selectedDataName !== 'all' ? await getDeviceDetail(selectedDataName, queryParams) : null
        if (active) {
          setSummary(nextSummary)
          setTimeseries(nextTimeseries.points ?? [])
          setTopDevices(nextTopDevices ?? [])
          setRoomLoads(nextRoomLoads ?? [])
          setDeviceDetail(nextDeviceDetail)
          setError(timeseriesResult.status === 'rejected' || topDevicesResult.status === 'rejected' ? 'Не удалось загрузить часть данных дашборда' : '')
        }
      } catch (requestError) {
        if (active) setError(requestError.message)
      } finally {
        if (active) setLoading(false)
      }
    }
    loadDashboardData()
    return () => { active = false }
  }, [filters, period, queryParams, selectedDataName, selectedDate])

  useEffect(() => {
    if (period !== 'all' && availableDates.length && (!selectedDate || !availableDates.includes(selectedDate))) setSelectedDate(availableDates.at(-1))
  }, [availableDates, period, selectedDate])

  useEffect(() => {
    if (analyticsPeriod1From || analyticsPeriod1To || analyticsPeriod2From || analyticsPeriod2To) return
    const defaultPeriods = buildDefaultAnalyticsPeriods(availableDates)
    if (!defaultPeriods) return
    setAnalyticsPeriod1From(defaultPeriods.period1From)
    setAnalyticsPeriod1To(defaultPeriods.period1To)
    setAnalyticsPeriod2From(defaultPeriods.period2From)
    setAnalyticsPeriod2To(defaultPeriods.period2To)
  }, [analyticsPeriod1From, analyticsPeriod1To, analyticsPeriod2From, analyticsPeriod2To, availableDates])

  function resetDependentFilters(nextDataName) {
    setSelectedDataName(nextDataName)
    if (nextDataName !== 'all') {
      setSelectedRoom('all')
      setSelectedConsumerClass('all')
    }
  }

  async function handleAnalyticsCompare(event) {
    event.preventDefault()
    const validationError = validateAnalyticsPeriods({ period1From: analyticsPeriod1From, period1To: analyticsPeriod1To, period2From: analyticsPeriod2From, period2To: analyticsPeriod2To })
    if (validationError) {
      setAnalyticsError(validationError)
      return
    }
    setAnalyticsLoading(true)
    setAnalyticsError('')
    try {
      setAnalyticsResult(await getPeriodComparison({
        period1_from: analyticsPeriod1From,
        period1_to: analyticsPeriod1To,
        period2_from: analyticsPeriod2From,
        period2_to: analyticsPeriod2To,
        alpha: analyticsAlpha,
        alternative: analyticsAlternative,
        data_name: selectedDataName,
        room: selectedRoom,
        consumer_class: selectedConsumerClass,
      }))
    } catch (requestError) {
      setAnalyticsError(requestError.message)
    } finally {
      setAnalyticsLoading(false)
    }
  }

  const pageContext = {
    filters, summary, timeseries, topDevices, roomLoads, deviceDetail,
    selectedDataName, selectedRoom, selectedConsumerClass, period, selectedDate, availableDates, powerStats, selectedDeviceLabel, queryParams,
    analyticsPeriod1From, analyticsPeriod1To, analyticsPeriod2From, analyticsPeriod2To, analyticsAlpha, analyticsAlternative, analyticsResult, analyticsLoading, analyticsError,
    resetDependentFilters, setSelectedDataName, setSelectedRoom, setSelectedConsumerClass, setPeriod, setSelectedDate,
    setAnalyticsPeriod1From, setAnalyticsPeriod1To, setAnalyticsPeriod2From, setAnalyticsPeriod2To, setAnalyticsAlpha, setAnalyticsAlternative, handleAnalyticsCompare,
  }

  return (
    <main className="energy-dashboard">
      <div className="energy-dashboard__glow energy-dashboard__glow--left" aria-hidden="true" />
      <div className="energy-dashboard__glow energy-dashboard__glow--right" aria-hidden="true" />
      <div className="energy-shell">
        <aside className="energy-sidebar">
          <div className="energy-brand"><div className="energy-brand__mark" /><div><div className="energy-brand__title">Smart Energy Consumption</div><div className="energy-brand__subtitle">Dashboard</div></div></div>
          <nav className="energy-nav" aria-label="Навигация">
            {NAV_ITEMS.map((item) => <NavLink className={({ isActive }) => isActive ? 'energy-nav__item active' : 'energy-nav__item'} to={{ pathname: item.path, search: preview ? '?preview=1' : '' }} key={item.id}><span className="energy-nav__icon"><SidebarIcon id={item.id} /></span><span>{item.label}</span></NavLink>)}
          </nav>
          <div className="energy-sidebar__footer">
            <ThemeToggle />
            <div className="energy-status-pill" role="status" aria-live="polite"><span className="energy-status-pill__dot" /><span>{error ? 'Ошибка подключения' : loading ? 'Загружаем данные' : 'База подключена'}</span></div>
            <button className="energy-user-card" type="button" onClick={onLogout} aria-label={`Выйти из аккаунта ${userName}`}><span className="energy-user-card__avatar">{getUserInitials(userName)}</span><span className="energy-user-card__meta"><strong>{userName}</strong><span>Выйти</span></span></button>
          </div>
        </aside>
        <section className="energy-main" aria-busy={loading}>
          {error ? <div className="energy-alert" role="alert">{error}</div> : null}
          <Outlet context={pageContext} />
        </section>
      </div>
    </main>
  )
}

export default DashboardPage
