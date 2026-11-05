import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { ROUTES } from '../../app/routes.js'
import { notificationsRequest, previewNotifications } from '../../features/notifications/api/notificationsApi.js'
import { formatDateTime, formatNumber } from '../dashboard/dashboardUtils.js'
import ThemeToggle from '../../shared/theme/ThemeToggle.jsx'
import ConsumptionCurve from './ConsumptionCurve.jsx'
import { DEFAULT_RULE, moscowDay, percentage, ruleErrors } from './curveUtils.js'
import './notifications.css'

const STATUS = { insufficient: 'Недостаточно данных', normal: 'В пределах порога', critical: 'Критическое отклонение' }
const MAIL = { not_required: 'Не требуется', pending: 'Ожидает отправки', sending: 'Отправляется',
  sent: 'Передано почтовому серверу', failed: 'Ошибка подключения к почте', unknown: 'Доставка неизвестна' }
const METHODS = { mean: 'Среднее по каждому часу', median: 'Медиана по каждому часу', linear: 'Линейный тренд по каждому часу' }
const dateLabel = value => new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeZone: 'Europe/Moscow' }).format(new Date(`${value}T12:00:00+03:00`))

export default function NotificationsPage() {
  const [settings, setSettings] = useState(null)
  const [draft, setDraft] = useState(null)
  const [dates, setDates] = useState({})
  const [day, setDay] = useState(moscowDay(-1))
  const [preview, setPreview] = useState(null)
  const [stale, setStale] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [reports, setReports] = useState({ results: [], count: 0, page_size: 20 })
  const [page, setPage] = useState(1)
  const [error, setError] = useState('')
  const [previewError, setPreviewError] = useState('')
  const [notice, setNotice] = useState('')
  const [saving, setSaving] = useState(false)
  const sequence = useRef(0)

  const calculate = useCallback(async (nextDay, nextDraft) => {
    if (nextDraft.baseline.mode === 'live') { nextDay = moscowDay(); setDay(nextDay) }
    const errors = ruleErrors(nextDraft, nextDay)
    if (errors.length) { setPreviewError(errors.join(' ')); return }
    const id = ++sequence.current
    setPreviewing(true); setPreviewError('')
    try {
      const result = await previewNotifications({ day: nextDay, baseline: nextDraft.baseline, threshold_percent: nextDraft.threshold_percent })
      if (id === sequence.current) { setPreview(result); setStale(false) }
    } catch (e) { if (id === sequence.current) setPreviewError(e.message) }
    finally { if (id === sequence.current) setPreviewing(false) }
  }, [])

  useEffect(() => {
    let active = true
    async function load() {
      try {
        const value = await notificationsRequest('settings/')
        const available = await notificationsRequest('preview/').catch(() => ({}))
        if (!active) return
        const nextDraft = { enabled: value.enabled, threshold_percent: value.threshold_percent,
          baseline: { ...DEFAULT_RULE, ...value.baseline } }
        const nextDay = nextDraft.baseline.mode === 'live' ? moscowDay() : available.last_day ?? moscowDay(-1)
        setSettings(value); setDraft(nextDraft); setDates(available); setDay(nextDay)
        calculate(nextDay, nextDraft)
      } catch (e) { if (active) setError(e.message) }
    }
    load()
    return () => { active = false; sequence.current += 1 }
  }, [calculate])

  useEffect(() => {
    let active = true
    const refresh = () => notificationsRequest(`reports/?page=${page}`)
      .then(value => { if (active) setReports(value) })
      .catch(e => { if (active) setError(e.message) })
    refresh()
    const timer = setInterval(refresh, 15000)
    return () => { active = false; clearInterval(timer) }
  }, [page])

  const previewDay = preview?.day
  useEffect(() => {
    if (draft?.baseline.mode !== 'live' || stale || !previewDay) return undefined
    const timer = setInterval(() => calculate(moscowDay(), draft), 60000)
    return () => clearInterval(timer)
  }, [calculate, draft, stale, previewDay])

  function invalidate() { sequence.current += 1; setPreviewing(false); setStale(true); setNotice(''); setPreviewError('') }
  function changeRule(key, value) {
    invalidate()
    setDraft(current => {
      const baseline = { ...current.baseline, [key]: value }
      if (key === 'history_days' && value > 0) baseline.minimum_days = Math.min(baseline.minimum_days, value)
      return { ...current, baseline }
    })
    if (key === 'mode') setDay(value === 'live' ? moscowDay() : dates.last_day ?? moscowDay(-1))
  }

  async function save(event) {
    event.preventDefault()
    const errors = ruleErrors(draft, day)
    if (errors.length) { setError(errors.join(' ')); return }
    setSaving(true); setError(''); setNotice('')
    try {
      const value = await notificationsRequest('settings/', draft)
      setSettings(value)
      setNotice('Правило сохранено. Оно применяется в фоне; предварительный просмотр писем не отправляет.')
    } catch (e) { setError(e.message) } finally { setSaving(false) }
  }

  return <main className="notifications-page">
    <header className="notifications-header"><div><Link to={ROUTES.dashboard}>← Дашборд</Link><h1>Уведомления</h1><p>Почасовая кривая потребления и настраиваемые отклонения</p></div><ThemeToggle /></header>
    {error && <div className="notifications-message error" role="alert">{error}</div>}
    {notice && <div className="notifications-message" role="status">{notice}</div>}
    {!draft ? <p role="status">Загрузка настроек…</p> : <>
      <section className="notifications-card">
        <h2>Данные и база сравнения</h2>
        {settings.legacy_rule && <p className="notifications-message">Пока сохранено прежнее суточное правило. Настройте кривую и нажмите «Сохранить правило», чтобы применять её в фоне.</p>}
        <p>Каждый час сравнивается с тем же часом выбранных прошлых дней. N — число дней для кривой; процентный порог — чувствительность уведомлений.</p>
        <form onSubmit={save} onKeyDown={event => {
          if (event.key === 'Enter' && event.target.tagName === 'INPUT') {
            event.preventDefault()
            calculate(day, draft)
          }
        }}>
          <div className="notifications-controls">
            <label>Режим проверки<select value={draft.baseline.mode} onChange={e => changeRule('mode', e.target.value)}>
              <option value="daily">После завершения суток</option><option value="live">В течение дня — завершённые часы</option>
            </select></label>
            <label>Анализируемый день<input type="date" required value={day} max={moscowDay(draft.baseline.mode === 'live' ? 0 : -1)}
              disabled={draft.baseline.mode === 'live'} onChange={e => { invalidate(); setDay(e.target.value) }} /></label>
            <label>Функция сравнения<select value={draft.baseline.method} onChange={e => changeRule('method', e.target.value)}>
              {Object.entries(METHODS).map(([value,label]) => <option key={value} value={value}>{label}</option>)}
            </select></label>
            <label>Период для функции<select value={draft.baseline.window_mode} onChange={e => changeRule('window_mode', e.target.value)}>
              <option value="last_n">Последние N сопоставимых дней</option><option value="range">Заданный диапазон</option>
            </select></label>
            {draft.baseline.window_mode === 'last_n' ? <label>N — число дней<input type="number" required min="1" max="60" value={draft.baseline.history_days} onChange={e => changeRule('history_days', Number(e.target.value))} /></label> : <>
              <label>Начало диапазона<input type="date" required value={draft.baseline.date_from ?? ''} max={day} onChange={e => changeRule('date_from', e.target.value)} /></label>
              <label>Конец диапазона включительно<input type="date" required value={draft.baseline.date_to ?? ''} max={day} onChange={e => changeRule('date_to', e.target.value)} /></label>
            </>}
            <label>Сопоставимость дней<select value={draft.baseline.day_group} onChange={e => changeRule('day_group', e.target.value)}>
              <option value="day_type">Рабочие с рабочими, выходные с выходными</option><option value="weekday">Тот же день недели</option><option value="all">Все дни</option>
            </select></label>
            <label>Минимум дней на каждый час<input type="number" required min="1" max="60" value={draft.baseline.minimum_days} onChange={e => changeRule('minimum_days', Number(e.target.value))} /></label>
            <label>Порог отклонения, %<input type="number" required min="0.01" max="10000" step="0.01" value={draft.threshold_percent} onChange={e => { invalidate(); setDraft({ ...draft, threshold_percent: e.target.value }) }} /></label>
            <label>Минимальное отклонение, кВт·ч<input type="number" required min="0" max="1000000000" step="any" value={draft.baseline.absolute_threshold} onChange={e => changeRule('absolute_threshold', e.target.value)} /></label>
          </div>
          <p>Рабочие дни — пн–пт, выходные — сб–вс. Праздники отдельным календарём пока не учитываются. Сам анализируемый день и последующие даты в обучение не входят.</p>
          {draft.baseline.method === 'linear' && <p>Для тренда нужно минимум два подходящих значения на час. Прогноз строится по фактическим датам, без включения проверяемого дня.</p>}
          {draft.baseline.mode === 'live' && <p>Проверяются только завершённые часы по Москве. Источник читается без записи агрегатов; обновление данных — до пяти минут. Незавершённый час не вызывает тревогу.</p>}
          <div className="notifications-actions">
            <button type="button" disabled={previewing} onClick={() => calculate(day, draft)}>{previewing ? 'Получение данных…' : 'Рассчитать и показать'}</button>
          </div>
          {previewError && <p className="notifications-message error" role="alert">{previewError}</p>}
          {stale && <p className="notifications-message" role="status">Настройки изменены. Пересчитайте график — ниже предыдущий результат.</p>}
          {preview && <div aria-busy={previewing} className={stale ? 'notification-preview stale' : 'notification-preview'}>
            <h3>Проверка за {dateLabel(preview.day)}: {STATUS[preview.status]}</h3>
            <div className="notifications-summary">
              <p>Факт за проверяемые часы: <strong>{formatNumber(preview.energy_kwh, 3)} кВт·ч</strong></p>
              <p>Ожидается по кривой: <strong>{formatNumber(preview.average_kwh, 3)} кВт·ч</strong></p>
              <p>Сопоставлено часов: <strong>{preview.checked_hours} из {preview.closed_hours}</strong></p>
              <p>Дней в выборке: <strong>{preview.baseline_dates.length}</strong></p>
            </div>
            <ConsumptionCurve points={preview.curve} />
            <p>{preview.explanation} Пропуски остаются пропусками; измеренный ноль отображается как ноль. Получено: {formatDateTime(preview.as_of)}.</p>
            <details><summary>Почасовые данные и отклонения</summary><div className="notifications-table"><table>
              <thead><tr><th>Час</th><th>Факт, кВт·ч</th><th>Ожидается, кВт·ч</th><th>Отклонение</th><th>Дней</th><th>Результат</th></tr></thead>
              <tbody>{preview.curve.map(point => <tr key={point.hour} className={point.anomaly ? 'notification-anomaly' : ''}>
                <td>{point.time}</td><td>{formatNumber(point.actual_kwh, 3)}</td><td>{formatNumber(point.expected_kwh, 3)}</td>
                <td>{percentage(point.deviation_percent)}</td><td>{point.sample_count}</td><td>{point.reason || (point.anomaly ? 'Превышен порог' : 'В пределах порога')}</td>
              </tr>)}</tbody>
            </table></div></details>
            <details><summary>Данные дней, вошедших в функцию ({preview.reference_days.length})</summary><div className="notifications-table"><table>
              <thead><tr><th>Дата</th>{Array.from({ length: 24 }, (_,hour) => <th key={hour}>{hour}:00</th>)}</tr></thead>
              <tbody>{preview.reference_days.map(item => <tr key={item.day}><td>{dateLabel(item.day)}</td>{item.hours.map((value,hour) => <td key={hour}>{formatNumber(value, 3)}</td>)}</tr>)}</tbody>
            </table></div></details>
          </div>}
          <h3>Автоматическая проверка</h3>
          <label className="notifications-toggle"><input type="checkbox" checked={draft.enabled} onChange={e => setDraft({ ...draft, enabled: e.target.checked })} />Включить фоновую проверку и письма</label>
          <p>Отчёты сохраняют версию настроек. В оперативном режиме отчёт обновляется по завершённым часам; письмо — не чаще одного раза за день для одной версии правила.</p>
          {!settings.mail_configured && <p className="notifications-message">SMTP не настроен: отчёты доступны в приложении, письма ожидают настройки почты.</p>}
          <p>Почта: {settings.email}. Сохранённое состояние: {settings.enabled ? 'Включено' : 'Выключено'}. Последняя проверка: {formatDateTime(settings.last_check_at)}.</p>
          <button type="submit" disabled={saving}>{saving ? 'Сохранение…' : 'Сохранить правило'}</button>
        </form>
      </section>
    </>}
    <section className="notifications-card"><h2>Мои отчёты</h2>
      <div className="notifications-table"><table><thead><tr><th>День</th><th>Режим / функция</th><th>Факт, кВт·ч</th><th>Ожидается, кВт·ч</th><th>Результат</th><th>Письмо</th></tr></thead>
        <tbody>{reports.results.map(report => <tr key={report.id}>
          <td>{dateLabel(report.day)}</td><td>{report.baseline_snapshot?.rule ? `${report.baseline_snapshot.rule.mode === 'live' ? 'В течение дня' : 'За сутки'} · ${METHODS[report.baseline_snapshot.rule.method]}` : 'Прежнее суточное правило'}</td>
          <td>{formatNumber(report.energy_kwh, 3)}</td><td>{formatNumber(report.average_kwh, 3)}</td>
          <td>{STATUS[report.status]}<details><summary>Подробнее</summary><p>{report.explanation}</p><p>Порог: {report.threshold_percent}%. Даты: {report.baseline_dates.map(dateLabel).join(', ') || 'Нет подходящих дней'}.</p>
            {report.delivery_snapshot?.anomaly_hours && <p>В письме отмечены часы: {report.delivery_snapshot.anomaly_hours.map(h => `${h}:00`).join(', ')}.</p>}
          </details></td><td>{MAIL[report.email_status] ?? report.email_status}{report.email_error && <p>{report.email_error}</p>}</td>
        </tr>)}</tbody></table></div>
      {!reports.results.length && <p>Отчётов пока нет. Данные и график доступны выше без включения писем.</p>}
      <div className="notifications-pagination"><button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>Назад</button><span>Страница {page} · Всего: {reports.count}</span><button type="button" disabled={page * reports.page_size >= reports.count} onClick={() => setPage(page + 1)}>Далее</button></div>
    </section>
  </main>
}
