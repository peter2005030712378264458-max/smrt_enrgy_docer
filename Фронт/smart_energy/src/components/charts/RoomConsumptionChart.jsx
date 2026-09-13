import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

export default function RoomConsumptionChart({ rooms }) {
  const data = rooms
    .map((room) => ({ room: String(room.room || 'Без названия'), energy: Number(room.energy_kwh ?? 0) }))
    .filter((room) => Number.isFinite(room.energy))

  if (!data.length) return <div className="energy-chart-empty" role="status">Нет агрегированных данных за выбранный период</div>

  return (
    <div className="energy-room-chart" role="img" aria-label="Столбчатая диаграмма потребления энергии по помещениям в киловатт-часах">
      <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={260}>
        <BarChart data={data} layout="vertical" margin={{ top: 8, right: 28, bottom: 8, left: 16 }}>
          <CartesianGrid stroke="var(--chart-grid)" horizontal={false} />
          <XAxis type="number" unit=" кВт·ч" stroke="var(--chart-axis)" />
          <YAxis type="category" dataKey="room" width={110} stroke="var(--chart-axis)" tick={{ fontSize: 12 }} />
          <Tooltip formatter={(value) => [`${Number(value).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} кВт·ч`, 'Энергия']} contentStyle={{ background: 'var(--surface-elevated)', border: '1px solid var(--border)', borderRadius: 12 }} />
          <Bar dataKey="energy" name="Энергия" fill="var(--chart-primary)" radius={[0, 7, 7, 0]} maxBarSize={28} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
