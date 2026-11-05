import { Area, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { chartPoints } from './curveUtils.js'

export default function ConsumptionCurve({ points }) {
  return <div className="notification-curve" role="img" aria-label="Фактическое и ожидаемое почасовое потребление с допустимым диапазоном">
    <ResponsiveContainer width="100%" height={320} minWidth={0}>
      <ComposedChart data={chartPoints(points)} margin={{ left: 8, right: 24, top: 16, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
        <XAxis dataKey="time" minTickGap={24} />
        <YAxis width={72} label={{ value: 'кВт·ч', position: 'insideTopLeft', offset: -5 }} />
        <Tooltip />
        <Legend />
        <Area dataKey="band" name="Допустимый диапазон" stroke="none" fill="#2688bb" fillOpacity={0.12} isAnimationActive={false} connectNulls={false} />
        <Line dataKey="expected_kwh" name="Ожидаемая кривая" stroke="#dc9537" strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
        <Line dataKey="actual_kwh" name="Фактическое потребление" stroke="#2688bb" strokeWidth={2} isAnimationActive={false} connectNulls={false}
          dot={props => props.payload.anomaly
            ? <circle key={props.key} cx={props.cx} cy={props.cy} r={5} fill="#d54b4b" />
            : <circle key={props.key} cx={props.cx} cy={props.cy} r={2} fill="#2688bb" />} />
      </ComposedChart>
    </ResponsiveContainer>
  </div>
}
