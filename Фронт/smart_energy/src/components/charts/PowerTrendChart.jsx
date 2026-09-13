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
  const values = points
    .map((point) => ({ timestamp: point.timestamp, actual: Number(point.value ?? 0) }))
    .filter((point) => point.timestamp && Number.isFinite(point.actual))
  const average = values.length ? values.reduce((sum, point) => sum + point.actual, 0) / values.length : 0
  const data = values.map((point) => ({ ...point, average }))

  if (!data.length) return <div className="energy-chart-empty" role="status">Нет данных для выбранных фильтров</div>

  return (
    <div className="energy-rechart" role="img" aria-label="График фактической и средней активной мощности в киловаттах, время московское">
      <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={280}>
        <LineChart data={data} margin={{ top: 16, right: 20, bottom: 12, left: 6 }}>
          <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
          <XAxis dataKey="timestamp" tickFormatter={(value) => axisLabel(value, granularity)} stroke="var(--chart-axis)" minTickGap={28} />
          <YAxis stroke="var(--chart-axis)" unit=" кВт" width={72} />
          <Tooltip labelFormatter={tooltipLabel} formatter={(value, name) => [`${Number(value).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} кВт`, name]} contentStyle={{ background: 'var(--surface-elevated)', border: '1px solid var(--border)', borderRadius: 12 }} />
          <Legend />
          <Line name="Факт" type="monotone" dataKey="actual" stroke="var(--chart-primary)" strokeWidth={3} dot={false} activeDot={{ r: 5 }} />
          <Line name="Среднее" type="monotone" dataKey="average" stroke="var(--chart-secondary)" strokeWidth={2} strokeDasharray="7 7" dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
