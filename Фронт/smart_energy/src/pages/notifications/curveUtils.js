export const DEFAULT_RULE = { method: 'mean', window_mode: 'last_n', history_days: 7, date_from: null,
  date_to: null, day_group: 'day_type', minimum_days: 1, mode: 'daily', absolute_threshold: '0' }

export function moscowDay(offset = 0, now = new Date()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Moscow',
    year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now).map(p => [p.type, p.value]))
  const value = new Date(`${parts.year}-${parts.month}-${parts.day}T12:00:00Z`)
  value.setUTCDate(value.getUTCDate() + offset)
  return value.toISOString().slice(0, 10)
}

export function ruleErrors(draft, day, today = moscowDay()) {
  const rule = draft.baseline
  const errors = []
  if (!day || day > today || (rule.mode === 'daily' && day >= today)) errors.push('Выберите завершённый день или оперативный режим.')
  if (rule.mode === 'live' && day !== today) errors.push('Оперативный режим использует текущий день.')
  if (!Number.isFinite(Number(draft.threshold_percent)) || Number(draft.threshold_percent) < 0.01 || Number(draft.threshold_percent) > 10000) errors.push('Процентный порог должен быть не меньше 0,01 и не больше 10000.')
  if (!Number.isInteger(rule.history_days) || rule.history_days < 1 || rule.history_days > 60) errors.push('N должно быть целым числом от 1 до 60.')
  if (!Number.isInteger(rule.minimum_days) || rule.minimum_days < 1 || rule.minimum_days > 60) errors.push('Укажите минимум дней от 1 до 60.')
  if (rule.window_mode === 'last_n' && rule.minimum_days > rule.history_days) errors.push('Минимум дней не может превышать N.')
  if (rule.window_mode === 'range') {
    if (!rule.date_from || !rule.date_to || rule.date_from > rule.date_to) errors.push('Укажите корректный диапазон сравнения.')
    else if ((new Date(rule.date_to) - new Date(rule.date_from)) / 86400000 >= 366) errors.push('Диапазон сравнения — не больше 366 дней.')
  }
  if (rule.absolute_threshold === '' || !Number.isFinite(Number(rule.absolute_threshold)) || Number(rule.absolute_threshold) < 0 || Number(rule.absolute_threshold) > 1e9) errors.push('Абсолютный порог должен быть от 0 до 1000000000.')
  return errors
}

export function chartPoints(points) {
  const finite = value => value == null || !Number.isFinite(Number(value)) ? null : Number(value)
  return points.map(p => ({ ...p, actual_kwh: finite(p.actual_kwh), expected_kwh: finite(p.expected_kwh),
    band: p.lower == null || p.upper == null ? null : [finite(p.lower), finite(p.upper)] }))
}

export function percentage(value) {
  return value == null || !Number.isFinite(Number(value)) ? '—' : `${Number(value) > 0 ? '+' : ''}${Number(value).toLocaleString('ru-RU', { maximumFractionDigits: 2 })}%`
}
