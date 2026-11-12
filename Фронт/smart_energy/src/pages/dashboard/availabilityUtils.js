export const AVAILABILITY_STATUSES = {
  no_data: 'Нет данных',
  low_data: 'Мало данных',
  unknown: 'Не удалось оценить',
  ok: 'Достаточно данных',
}

export function filterAvailability(rows, { status = 'problems', kind = 'all', search = '' }) {
  const query = search.trim().toLocaleLowerCase('ru')
  return rows.filter(row => {
    const matchesStatus = status === 'problems'
      ? row.status === 'no_data' || row.status === 'low_data'
      : row.status === status
    return matchesStatus && (kind === 'all' || row.kind === kind)
      && (!query || `${row.sensor_name} ${row.id}`.toLocaleLowerCase('ru').includes(query))
  })
}

export function availabilityDate(value, dateOnly = false) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('ru-RU', {
    timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', year: 'numeric',
    ...(dateOnly ? {} : { hour: '2-digit', minute: '2-digit' }),
  }).format(date)
}
