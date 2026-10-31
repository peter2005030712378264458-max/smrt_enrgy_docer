import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { buildPowerSeries } from '../../pages/dashboard/dashboardUtils.js'

const TIME_ZONE = 'Europe/Moscow'

function axisLabel(value, granularity) {
  const options = granularity === 'hour'
    ? { hour: '2-digit', minute: '2-digit', timeZone: TIME_ZONE }
    : { day: '2-digit', month: '2-digit', timeZone: TIME_ZONE }
  return new Intl.DateTimeFormat('ru-RU', options).format(new Date(value))
}

function tooltipLabel(value) {
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: TIME_ZONE,
  }).format(new Date(value))
}

export default function PowerTrendChart({ points, granularity }) {
  const values = buildPowerSeries(points)
  const known = values.filter(point => point.kw !== null)
  const average = known.length ? known.reduce((sum, point) => sum + point.kw, 0) / known.length : null
  const partial = values.some(point => point.isPartial)
  const data = values.map(point => ({ ...point, actual: point.kw, average: point.kw === null ? null : average }))

  if (!known.length) return <div className="energy-chart-empty" role="status">Нет данных активной мощности для выбранных фильтров</div>

  return (
    <div className="energy-rechart" role="img" aria-label="График доступной и средней активной мощности в киловаттах, время московское">
      <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={280}>
        <LineChart data={data} margin={{ top: 16, right: 20, bottom: 12, left: 6 }}>
          <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
          <XAxis dataKey="timestamp" tickFormatter={(value) => axisLabel(value, granularity)} stroke="var(--chart-axis)" minTickGap={28} />
          <YAxis stroke="var(--chart-axis)" unit=" кВт" width={72} />
          <Tooltip labelFormatter={(value, items) => {
            const point = items?.[0]?.payload
            const coverage = point?.totalMeters > 0 ? ` · счётчиков ${point.availableMeters} из ${point.totalMeters}` : ''
            return `${tooltipLabel(value)}${coverage}`
          }} formatter={(value, name) => [`${Number(value).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} кВт`, name]} contentStyle={{ background: 'var(--surface-elevated)', border: '1px solid var(--border)', borderRadius: 12 }} />
          <Legend />
          <Line name={partial ? 'Доступная мощность' : 'Факт'} type="monotone" dataKey="actual" stroke="var(--chart-primary)" strokeWidth={3} dot={false} activeDot={{ r: 5 }} />
          <Line name="Среднее" type="monotone" dataKey="average" stroke="var(--chart-secondary)" strokeWidth={2} strokeDasharray="7 7" dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
