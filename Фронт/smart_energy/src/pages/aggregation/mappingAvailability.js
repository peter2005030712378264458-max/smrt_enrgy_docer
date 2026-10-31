const NUMERIC_TYPES = new Set(['int2', 'int4', 'int8', 'numeric', 'float4', 'float8'])
const TIME_TYPES = new Set(['timestamp', 'timestamptz'])

export const METRIC_LABELS = {
  active_power_w_avg: 'Общая активная мощность',
  phase1_power_w_avg: 'Активная мощность L1', phase2_power_w_avg: 'Активная мощность L2', phase3_power_w_avg: 'Активная мощность L3',
  reactive_power_var_avg: 'Реактивная мощность', apparent_power_va_avg: 'Полная мощность',
  current_avg_a: 'Ток по доступным фазам', voltage_avg_v: 'Напряжение по доступным фазам',
  frequency_hz_avg: 'Частота', meter_temperature_avg: 'Температура',
}
const INPUTS = {
  active_power_w_avg: [['pt'], ['p1', 'p2', 'p3']],
  phase1_power_w_avg: [['p1']], phase2_power_w_avg: [['p2']], phase3_power_w_avg: [['p3']],
  reactive_power_var_avg: [['qt'], ['q1', 'q2', 'q3']], apparent_power_va_avg: [['st'], ['s1', 's2', 's3']],
  current_avg_a: [['i1'], ['i2'], ['i3']], voltage_avg_v: [['u1'], ['u2'], ['u3']],
  frequency_hz_avg: [['frequency']], meter_temperature_avg: [['t']],
}

export function mappingAvailability(assigned, inspection) {
  const source = new Map((inspection?.columns ?? []).map(column => [column.name, column]))
  const available = new Set()
  const errors = []
  const invalidTypes = new Set()
  for (const [field, name] of Object.entries(assigned)) {
    if (!name) continue
    const column = source.get(name)
    if (inspection && (!column || column.readable === false)) continue
    if (column?.type && field !== 'sensor_name' && !(field === 'ts' ? TIME_TYPES : NUMERIC_TYPES).has(column.type)) {
      errors.push(`${name}: ${field === 'ts' ? 'нужен тип timestamp или timestamptz' : 'нужен числовой тип'}, получен ${column.declared_type ?? column.type}.`)
      invalidTypes.add(field)
      continue
    }
    available.add(field)
  }
  for (const [field, label] of [['ts', 'время'], ['sensor_name', 'идентификатор счётчика']]) {
    if (!available.has(field) && !invalidTypes.has(field)) errors.push(`Назначьте доступный столбец: ${label}. Без него агрегаты не строятся.`)
  }
  const metrics = Object.entries(INPUTS).filter(([, alternatives]) => alternatives.some(group => group.every(field => available.has(field)))).map(([metric]) => metric)
  return { available, metrics, errors, invalidTypes,
    unavailable: Object.keys(METRIC_LABELS).filter(metric => !metrics.includes(metric)) }
}
