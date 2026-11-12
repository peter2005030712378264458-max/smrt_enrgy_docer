import { useEffect, useMemo, useState } from 'react'
import { getDeviceAvailability } from '../../features/dashboard/api/dashboardApi.js'
import { AVAILABILITY_STATUSES, availabilityDate, filterAvailability } from './availabilityUtils.js'
import './deviceAvailability.css'

const PAGE_SIZE = 10
const number = value => value == null ? '—' : new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(value)

export default function DeviceAvailabilityPanel() {
  const [period, setPeriod] = useState('day')
  const [status, setStatus] = useState('problems')
  const [kind, setKind] = useState('all')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [request, setRequest] = useState({ period: 'day', revision: 0 })
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true
    let timer
    const abort = new AbortController()
    async function load(force = false) {
      try {
        const data = await getDeviceAvailability(request.period, { signal: abort.signal, refresh: force })
        if (!active) return
        if (data.state === 'pending' || data.state === 'busy') {
          timer = setTimeout(() => load(), Math.max(2, data.poll_after_seconds ?? 3) * 1000)
          return
        }
        if (!['ready', 'partial'].includes(data.state)) throw new Error(data.detail ?? 'Не удалось проверить данные датчиков')
        setResult(data)
        setLoading(false)
      } catch (e) {
        if (!active) return
        setError(e.message)
        setLoading(false)
      }
    }
    load(request.revision > 0)
    return () => { active = false; abort.abort(); clearTimeout(timer) }
  }, [request])

  function changePeriod(next) {
    setPeriod(next)
    setPage(1)
    setResult(null)
    setError('')
    setLoading(true)
    setRequest({ period: next, revision: 0 })
  }

  function retry() {
    setResult(null)
    setError('')
    setLoading(true)
    setRequest(current => ({ ...current, revision: current.revision + 1 }))
  }

  const rows = result?.rows ?? []
  const filtered = useMemo(() => filterAvailability(result?.rows ?? [], { status, kind, search }), [result, status, kind, search])
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const currentPage = Math.min(page, pages)
  const visible = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE)
  const summary = result?.summary
  const unknown = rows.filter(row => row.status === 'unknown')
  const endDay = result?.to_exclusive ? new Date(new Date(result.to_exclusive).getTime() - 1).toISOString() : null

  return <section className="availability-panel" aria-labelledby="availability-title" aria-busy={loading}>
    <div className="availability-heading">
      <div><span className="availability-eyebrow">Качество поступающих измерений</span><h2 id="availability-title">Состояние датчиков</h2><p>Электросчётчики, климатические датчики и метеостанция</p></div>
      <div className="availability-segments" role="group" aria-label="Период проверки датчиков">
        {[['day', 'День'], ['week', 'Неделя'], ['month', 'Месяц']].map(([value, label]) => <button type="button" key={value} aria-pressed={period === value} onClick={() => changePeriod(value)}>{label}</button>)}
      </div>
    </div>
    <div className="availability-meta">
      <span>{result ? `${availabilityDate(result.from, true)} — ${availabilityDate(endDay, true)} · Москва` : 'Последние завершённые дни по Москве'}</span>
      {result && <span>Проверка: {availabilityDate(result.checked_at)}</span>}
      {result?.reused_days > 0 && <span>Использованы сохранённые проверки от {availabilityDate(result.data_checked_from)}</span>}
      <button type="button" className="availability-refresh" onClick={retry} disabled={loading}>Обновить проверку</button>
    </div>
    <p className="availability-note">NULL и нулевые измерения не учитываются в этом отчёте. Мало данных — менее 90% эталона по предыдущей истории. Это оценка доступности измерений за период.</p>
    {loading ? <div className="availability-loading" role="status"><span className="availability-spinner" />Проверяем исходные данные и историю. Проверка может занять несколько минут.</div>
      : error ? <div className="availability-error" role="alert">{error}<button type="button" onClick={retry}>Повторить</button></div>
        : result && <>
          <div className="availability-stats">
            {[['no_data', 'Нет данных'], ['low_data', 'Мало данных'], ['unknown', 'Не удалось оценить']].map(([key, label]) => <div key={key} className={`availability-stat availability-stat--${key}`}><span>{label}</span><strong>{number(summary?.[key])}</strong></div>)}
            <div className="availability-stat"><span>Устройств в справочнике</span><strong>{number(summary?.total)}</strong></div>
          </div>
          <div className="availability-filters">
            <div className="availability-segments" role="group" aria-label="Состояние датчиков">
              {[['problems', 'Все проблемы'], ['no_data', 'Нет данных'], ['low_data', 'Мало данных']].map(([value, label]) => <button type="button" key={value} aria-pressed={status === value} onClick={() => { setStatus(value); setPage(1) }}>{label}</button>)}
            </div>
            <label className="availability-search"><span className="sr-only">Поиск датчика по имени или ID</span><input type="search" placeholder="Поиск по имени или ID…" value={search} onChange={e => { setSearch(e.target.value); setPage(1) }} /></label>
            <label className="availability-type"><span className="sr-only">Тип датчика</span><select value={kind} onChange={e => { setKind(e.target.value); setPage(1) }}><option value="all">Все типы</option><option value="electricity">Электросчётчики</option><option value="climate">Климатические</option><option value="meteo">Метеостанция</option></select></label>
          </div>
          {unknown.length > 0 && <details className="availability-unknown"><summary>Не удалось оценить: {number(unknown.length)}</summary><p>Причина может быть в недостатке истории для эталона или в недоступности источника.</p><ul>{unknown.map(row => <li key={row.id}><strong>{row.sensor_name}</strong> — {row.reason}</li>)}</ul></details>}
          {visible.length ? <div className="availability-table-scroll"><table className="availability-table"><thead><tr><th>Устройство</th><th>Помещение</th><th>Состояние</th><th>Данные за период</th><th>Последнее пригодное за период</th></tr></thead><tbody>{visible.map(row => <tr key={row.id}>
            <td data-label="Устройство"><strong className="availability-name">{row.sensor_name}</strong><small>{row.model ?? row.device_type} · ID {row.id}</small></td>
            <td data-label="Помещение">{row.room ?? 'Не указано'}</td>
            <td data-label="Состояние"><span className={`availability-badge availability-badge--${row.status}`}>{AVAILABILITY_STATUSES[row.status]}</span><small>{row.reason}</small></td>
            <td data-label="Данные за период"><strong>Пригодных записей: {number(row.valid_records)}</strong><small>{row.coverage_percent == null ? 'Эталон не определён' : `${number(row.coverage_percent)}% от эталона`}</small>{row.reference_records != null && <small>Эталон: {number(row.reference_records)} записей</small>}{row.reference_method === 'own_history' && <small>По собственной истории</small>}</td>
            <td data-label="Последнее пригодное за период">{row.last_valid_at ? availabilityDate(row.last_valid_at) : 'Нет за период'}</td>
          </tr>)}</tbody></table></div> : <div className="availability-empty" role="status">{!summary.no_data && !summary.low_data && !summary.unknown ? 'За выбранный период проблем с данными не обнаружено.' : 'По выбранным фильтрам устройства не найдены.'}</div>}
          <div className="availability-pagination"><span>Найдено: {number(filtered.length)} · Достаточно данных: {number(summary.ok)}</span><div><button type="button" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)} aria-label="Предыдущая страница датчиков">←</button><span>{currentPage} / {pages}</span><button type="button" disabled={currentPage >= pages} onClick={() => setPage(currentPage + 1)} aria-label="Следующая страница датчиков">→</button></div></div>
        </>}
  </section>
}
