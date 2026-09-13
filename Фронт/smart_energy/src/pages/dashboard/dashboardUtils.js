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

export function buildPowerStats(points, summary) {
  const series = points
    .map((point) => ({ timestamp: point.timestamp, kw: Number(point.value ?? 0) }))
    .filter((point) => Number.isFinite(point.kw))

  if (!series.length) {
    const hasSummary = summary !== null
    const fallbackMax = Number(summary?.max_power_kw ?? 0)
    const fallbackAvg = Number(summary?.avg_power_kw ?? 0)
    return {
      currentKw: hasSummary ? Number(summary?.current_power_kw ?? 0) : null,
      maxKw: hasSummary ? fallbackMax : null,
      avgKw: hasSummary ? fallbackAvg : null,
      minKw: hasSummary ? 0 : null,
      loadFactor: hasSummary ? (fallbackMax > 0 ? (fallbackAvg / fallbackMax) * 100 : 0) : null,
      pointsCount: hasSummary ? 0 : null,
      lastTimestamp: summary?.date_to ?? null,
      peakTimestamp: summary?.date_to ?? null,
    }
  }

  const current = series.at(-1)
  const peak = series.reduce((best, point) => (point.kw > best.kw ? point : best), series[0])
  const min = series.reduce((best, point) => (point.kw < best.kw ? point : best), series[0])
  const avgKw = series.reduce((sum, point) => sum + point.kw, 0) / series.length

  return {
    currentKw: current.kw,
    maxKw: peak.kw,
    avgKw,
    minKw: min.kw,
    loadFactor: peak.kw > 0 ? (avgKw / peak.kw) * 100 : 0,
    pointsCount: series.length,
    lastTimestamp: current.timestamp,
    peakTimestamp: peak.timestamp,
  }
}

export function buildSummaryFromTimeseries(points, filters, queryParams) {
  const values = points.map((point) => Number(point.value ?? 0)).filter(Number.isFinite)
  const totalEnergy = points.reduce((total, point) => {
    const energy = Number(point.energy_kwh ?? 0)
    return Number.isFinite(energy) ? total + energy : total
  }, 0)

  if (!values.length) {
    return {
      points: 0,
      devices_count: 0,
      date_from: queryParams.from ?? filters?.date_range?.date_from ?? null,
      date_to: queryParams.to ?? filters?.date_range?.date_to ?? null,
      total_energy_kwh: 0,
      avg_power_kw: 0,
      max_power_kw: 0,
      avg_voltage_v: 0,
      avg_frequency_hz: 0,
      current_power_kw: 0,
      timestamp_iso: queryParams.to ?? filters?.date_range?.date_to ?? null,
    }
  }

  const sum = values.reduce((total, value) => total + value, 0)
  return {
    points: points.length,
    devices_count: filters?.devices?.length ?? 0,
    date_from: points[0]?.timestamp ?? queryParams.from ?? filters?.date_range?.date_from ?? null,
    date_to: points.at(-1)?.timestamp ?? queryParams.to ?? filters?.date_range?.date_to ?? null,
    total_energy_kwh: totalEnergy,
    avg_power_kw: sum / values.length,
    max_power_kw: Math.max(...values),
    avg_voltage_v: 0,
    avg_frequency_hz: 0,
    current_power_kw: values.at(-1),
    timestamp_iso: points.at(-1)?.timestamp ?? queryParams.to ?? filters?.date_range?.date_to ?? null,
  }
}
