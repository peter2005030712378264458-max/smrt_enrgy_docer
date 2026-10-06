import { useEffect, useState } from 'react'
import { getWeekdayHeatmap } from '../../features/dashboard/api/dashboardApi.js'
import { formatNumber, getDeviceLabel } from '../../pages/dashboard/dashboardUtils.js'

const WEEKDAYS = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
const PAGE_SIZE = 8

export default function WeekdayConsumptionHeatmap({ params, ready }) {
  const [page, setPage] = useState(1)
  const [attempt, setAttempt] = useState(0)
  const [result, setResult] = useState(null)
  const loading = !result || result.page !== page || result.attempt !== attempt

  useEffect(() => {
    if (!ready) return undefined
    let active = true
    getWeekdayHeatmap({ ...params, page, page_size: PAGE_SIZE })
      .then((data) => {
        if (active) setResult({ page, attempt, data })
      })
      .catch((error) => {
        if (active) setResult({ page, attempt, error: error.message })
      })
    return () => { active = false }
  }, [params, ready, page, attempt])

  const data = result?.data
  const minimum = Number(data?.min_energy_kwh ?? 0)
  const maximum = Number(data?.max_energy_kwh ?? 0)

  return (
    <article className="energy-panel energy-weekday-heatmap" aria-busy={loading}>
      <div className="energy-panel__head compact">
        <div><h2>Потребление по дням недели</h2><p>Суммарная энергия каждого счетчика за выбранный период, кВт·ч. Общая шкала для всех страниц.</p></div>
      </div>
      {loading ? <div className="energy-list-empty" role="status">Загружаем тепловую карту…</div> : result.error ? (
        <div className="energy-alert" role="alert">
          {result.error}
          <button className="energy-ghost-button" type="button" onClick={() => setAttempt((value) => value + 1)}>Повторить</button>
        </div>
      ) : !data.meters.length ? <div className="energy-list-empty" role="status">Нет счетчиков для выбранных фильтров</div> : (
        <>
          <div className="energy-legend energy-heatmap-legend">
            <span>{formatNumber(minimum)} кВт·ч</span>
            <span className="energy-heatmap-scale" aria-hidden="true" />
            <span>{formatNumber(maximum)} кВт·ч</span>
            <span>— нет данных</span>
          </div>
          <div className="energy-heatmap-scroll" tabIndex={0} role="region" aria-label="Таблица потребления счетчиков по дням недели">
            <table className="energy-heatmap-table">
              <caption className="energy-heatmap-caption">Энергия, кВт·ч; дни недели по московскому времени</caption>
              <thead><tr><th scope="col">День недели</th>{data.meters.map((meter) => <th scope="col" key={meter.data_name}>{getDeviceLabel(meter)}</th>)}</tr></thead>
              <tbody>
                {WEEKDAYS.map((weekday, index) => (
                  <tr key={weekday}>
                    <th scope="row">{weekday}</th>
                    {data.meters.map((meter) => {
                      const value = meter.days[index].energy_kwh
                      const intensity = value === null || maximum === minimum ? 0 : Math.max(0, Math.min(1, (Number(value) - minimum) / (maximum - minimum)))
                      return (
                        <td key={meter.data_name} title={`${getDeviceLabel(meter)} · ${weekday}: ${value === null ? 'нет данных' : `${formatNumber(value, 3)} кВт·ч`}`}>
                          <span className={value === null ? 'energy-heatmap-cell energy-heatmap-cell--empty' : 'energy-heatmap-cell'} style={value === null ? undefined : { '--heat-intensity': `${8 + intensity * 52}%` }}>{formatNumber(value)}</span>
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!data.meters.some((meter) => meter.days.some((day) => day.energy_kwh !== null)) ? <p className="energy-heatmap-caption" role="status">Нет агрегированных данных за выбранный период</p> : null}
        </>
      )}
      <nav className="energy-heatmap-pagination" aria-label="Страницы счетчиков">
        <button className="energy-ghost-button" type="button" disabled={loading || page === 1} onClick={() => setPage((value) => value - 1)}>Назад</button>
        <span role="status">{!loading && data ? `Страница ${page} из ${Math.max(1, data.total_pages)} · Счетчиков: ${formatNumber(data.count, 0)}` : `Страница ${page}`}</span>
        <button className="energy-ghost-button" type="button" disabled={loading || !data || page >= data.total_pages || Boolean(result?.error)} onClick={() => setPage((value) => value + 1)}>Далее</button>
      </nav>
    </article>
  )
}
