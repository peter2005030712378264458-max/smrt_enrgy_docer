export const PERIOD_OPTIONS = [
  { value: 'all', label: 'Весь период' },
  { value: '24h', label: '24 часа', hours: 24 },
  { value: '7d', label: '7 дней', hours: 24 * 7 },
]

export const ANALYTICS_ALTERNATIVE_OPTIONS = [
  { value: 'two_sided', label: 'Средние не равны' },
  { value: 'greater', label: 'Первый период больше второго' },
  { value: 'less', label: 'Первый период меньше второго' },
]

const DAY_MS = 24 * 60 * 60 * 1000
const DASHBOARD_TIME_ZONE = 'Europe/Moscow'
const DASHBOARD_TIME_OFFSET = '+03:00'

export function formatNumber(value, digits = 1) {
  if (value === null || value === undefined || value === '') return '—'

  const safeValue = Number(value)
  if (!Number.isFinite(safeValue)) return '—'

  return new Intl.NumberFormat('ru-RU', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(safeValue)
}

export function formatPercent(value, digits = 0) {
  if (value === null || value === undefined || value === '') return '—'
  return `${formatNumber(value, digits)}%`
}

export function textOrFallback(value, fallback = '-') {
  const text = String(value ?? '').trim()
  return text || fallback
}

export function formatDateTime(value) {
  if (!value) return 'нет данных'

  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: DASHBOARD_TIME_ZONE,
  }).format(new Date(value))
}

function toUtcDateKey(date) {
  return date.toISOString().slice(0, 10)
}

function getDateKeyInDashboardTimeZone(value) {
  if (!value) return ''

  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: DASHBOARD_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date(value))
  const partMap = Object.fromEntries(parts.map((part) => [part.type, part.value]))

  return `${partMap.year}-${partMap.month}-${partMap.day}`
}

function dateKeyToUtcDate(dateKey) {
  return new Date(`${dateKey}T00:00:00Z`)
}

function addDaysToDateKey(dateKey, days) {
  return toUtcDateKey(new Date(dateKeyToUtcDate(dateKey).getTime() + days * DAY_MS))
}

export function buildAvailableDates(dateRange) {
  if (!dateRange?.date_from || !dateRange?.date_to) return []

  const start = dateKeyToUtcDate(getDateKeyInDashboardTimeZone(dateRange.date_from))
  const end = dateKeyToUtcDate(getDateKeyInDashboardTimeZone(dateRange.date_to))
  const dates = []

  for (let current = start; current <= end; current = new Date(current.getTime() + DAY_MS)) {
    dates.push(toUtcDateKey(current))
  }

  return dates
}

export function formatDateLabel(dateKey) {
  if (!dateKey) return 'Дата'

  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    timeZone: DASHBOARD_TIME_ZONE,
  }).format(new Date(`${dateKey}T00:00:00${DASHBOARD_TIME_OFFSET}`))
}

export function getAnalyticsTestLabel(alternative) {
  return alternative === 'two_sided' ? 'двухсторонний z-тест' : 'односторонний z-тест'
}

export function getPeriodParams(period, dateRange, selectedDate) {
  const periodOption = PERIOD_OPTIONS.find((option) => option.value === period)
  if (!periodOption?.hours || !dateRange?.date_to) return {}

  const availableDates = buildAvailableDates(dateRange)
  const fallbackDate = availableDates.at(-1) ?? getDateKeyInDashboardTimeZone(dateRange.date_to)
  const dateKey = availableDates.includes(selectedDate) ? selectedDate : fallbackDate

  if (period === '24h') {
    return {
      from: `${dateKey}T00:00:00${DASHBOARD_TIME_OFFSET}`,
      to: `${dateKey}T23:59:59${DASHBOARD_TIME_OFFSET}`,
    }
  }

  const days = Math.max(1, Math.round(periodOption.hours / 24))
  const dateFromKey = addDaysToDateKey(dateKey, -(days - 1))
  const minDateKey = getDateKeyInDashboardTimeZone(dateRange.date_from)

  return {
    from: `${dateFromKey < minDateKey ? minDateKey : dateFromKey}T00:00:00${DASHBOARD_TIME_OFFSET}`,
    to: `${dateKey}T23:59:59${DASHBOARD_TIME_OFFSET}`,
  }
}

export function getAggregationGranularity(period) {
  if (period === '24h') return 'hour'
  if (period === '7d') return 'day'
  return 'week'
}

export function buildDashboardParams({ selectedDataName, selectedRoom, selectedConsumerClass, period, dateRange, selectedDate }) {
  return {
    data_name: selectedDataName,
    room: selectedRoom,
    consumer_class: selectedConsumerClass,
    granularity: getAggregationGranularity(period),
    ...getPeriodParams(period, dateRange, selectedDate),
  }
}

export function buildDefaultAnalyticsPeriods(availableDates) {
  if (availableDates.length < 14) return null

  return {
    period1From: availableDates[0],
    period1To: availableDates[6],
    period2From: availableDates.at(-7),
    period2To: availableDates.at(-1),
  }
}

export function validateAnalyticsPeriods({ period1From, period1To, period2From, period2To }) {
  if (!period1From || !period1To || !period2From || !period2To) return 'Заполните оба периода'
  if (period1From > period1To) return 'В первом периоде дата начала позже даты конца'
  if (period2From > period2To) return 'Во втором периоде дата начала позже даты конца'
  if (!(period1To < period2From || period2To < period1From)) return 'Периоды не должны пересекаться'
  return ''
}

export function normalizeFilters(filters = {}) {
  return {
    date_range: filters.date_range ?? null,
    devices: filters.devices ?? [],
    rooms: filters.rooms ?? [],
    consumer_classes: filters.consumer_classes ?? [],
    buildings: filters.buildings ?? [],
    floors: filters.floors ?? [],
    locations: filters.locations ?? [],
    metrics: filters.metrics ?? [],
  }
}

export function getDeviceLabel(device, fallback = '-') {
  return textOrFallback(device?.label ?? device?.data_name, fallback)
}

export function getRoomLabel(room) {
  return textOrFallback(room?.room)
}

function knownNumber(value) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

export function buildPowerSeries(points) {
  return points.filter(point => point.timestamp && Number.isFinite(Date.parse(point.timestamp)))
    .map(point => {
      const fullValue = knownNumber(point.value)
      const availableValue = knownNumber(point.available_value)
      const availableMeters = knownNumber(point.available_meters)
      const totalMeters = knownNumber(point.total_meters)
      return {
        timestamp: point.timestamp,
        kw: fullValue ?? availableValue,
        availableMeters,
        totalMeters,
        isPartial: (availableMeters !== null && totalMeters !== null && availableMeters < totalMeters)
          || (fullValue === null && availableValue !== null),
      }
    })
}

export function buildPowerStats(points, summary) {
  const timeline = buildPowerSeries(points)
  const series = timeline.filter(point => point.kw !== null)
  const coveragePoints = timeline.filter(point => point.availableMeters !== null && point.totalMeters > 0)
  const coverage = {
    isPartial: timeline.some(point => point.isPartial),
    minAvailableMeters: coveragePoints.length ? Math.min(...coveragePoints.map(point => point.availableMeters)) : null,
    maxAvailableMeters: coveragePoints.length ? Math.max(...coveragePoints.map(point => point.availableMeters)) : null,
    totalMeters: coveragePoints.length ? Math.max(...coveragePoints.map(point => point.totalMeters)) : null,
  }

  if (!series.length) {
    const fallbackMax = knownNumber(summary?.max_power_kw)
    const fallbackAvg = knownNumber(summary?.avg_power_kw)
    return {
      coverage,
      currentKw: knownNumber(summary?.current_power_kw),
      maxKw: fallbackMax,
      avgKw: fallbackAvg,
      minKw: null,
      loadFactor: fallbackMax !== null && fallbackAvg !== null ? (fallbackMax > 0 ? (fallbackAvg / fallbackMax) * 100 : 0) : null,
      pointsCount: timeline.length || knownNumber(summary?.points),
      lastTimestamp: timeline.at(-1)?.timestamp ?? summary?.date_to ?? null,
      peakTimestamp: null,
    }
  }

  const current = timeline.at(-1)
  const peak = series.reduce((best, point) => (point.kw > best.kw ? point : best), series[0])
  const min = series.reduce((best, point) => (point.kw < best.kw ? point : best), series[0])
  const avgKw = series.reduce((sum, point) => sum + point.kw, 0) / series.length

  return {
    coverage,
    currentKw: current.kw,
    maxKw: peak.kw,
    avgKw,
    minKw: min.kw,
    loadFactor: peak.kw > 0 ? (avgKw / peak.kw) * 100 : 0,
    pointsCount: timeline.length,
    lastTimestamp: current.timestamp,
    peakTimestamp: peak.timestamp,
  }
}

export function buildSummaryFromTimeseries(points, filters) {
  const timeline = buildPowerSeries(points)
  const values = timeline.map(point => point.kw).filter(value => value !== null)
  const energyValues = points.map(point => knownNumber(point.energy_kwh))
  const totalEnergy = energyValues.length && energyValues.every(value => value !== null)
    ? energyValues.reduce((total, value) => total + value, 0) : null

  if (!values.length) {
    return {
      points: points.length,
      devices_count: filters?.devices?.length ?? 0,
      date_from: timeline[0]?.timestamp ?? null,
      date_to: timeline.at(-1)?.timestamp ?? null,
      total_energy_kwh: null,
      avg_power_kw: null,
      max_power_kw: null,
      avg_voltage_v: null,
      avg_frequency_hz: null,
      current_power_kw: null,
      timestamp_iso: timeline.at(-1)?.timestamp ?? null,
    }
  }

  const sum = values.reduce((total, value) => total + value, 0)
  return {
    points: points.length,
    devices_count: filters?.devices?.length ?? 0,
    date_from: timeline[0]?.timestamp ?? null,
    date_to: timeline.at(-1)?.timestamp ?? null,
    total_energy_kwh: totalEnergy,
    avg_power_kw: sum / values.length,
    max_power_kw: Math.max(...values),
    avg_voltage_v: null,
    avg_frequency_hz: null,
    current_power_kw: timeline.at(-1)?.kw ?? null,
    timestamp_iso: timeline.at(-1)?.timestamp ?? null,
  }
}
