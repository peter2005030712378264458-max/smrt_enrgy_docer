import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { ROUTES } from '../../app/routes.js'
import { notificationsRequest } from '../../features/notifications/api/notificationsApi.js'
import { formatDateTime, formatNumber } from '../dashboard/dashboardUtils.js'
import ThemeToggle from '../../shared/theme/ThemeToggle.jsx'
import './notifications.css'

const STATUS = { insufficient: 'Недостаточно данных', normal: 'В пределах порога', critical: 'Критическое отклонение' }
const MAIL = { not_required: 'Не требуется', pending: 'Ожидает отправки', sending: 'Отправляется',
  sent: 'Передано почтовому серверу', failed: 'Ошибка подключения к почте', unknown: 'Доставка неизвестна' }
const dateLabel = value => new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeZone: 'Europe/Moscow' }).format(new Date(`${value}T12:00:00+03:00`))

export default function NotificationsPage() {
  const [settings, setSettings] = useState(null)
  const [enabled, setEnabled] = useState(false)
  const [threshold, setThreshold] = useState('30')
  const [reports, setReports] = useState({ results: [], count: 0, page_size: 20 })
  const [page, setPage] = useState(1)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let active = true
    notificationsRequest('settings/').then(value => {
      if (!active) return
      setSettings(value)
      setEnabled(value.enabled)
      setThreshold(value.threshold_percent)
    }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [])

  useEffect(() => {
    let active = true
    const refresh = () => Promise.all([
      notificationsRequest(`reports/?page=${page}`), notificationsRequest('settings/'),
    ]).then(([nextReports, nextSettings]) => {
      if (active) { setReports(nextReports); setSettings(nextSettings); setError('') }
    }).catch(e => { if (active) setError(e.message) })
    refresh()
    const timer = setInterval(refresh, 15000)
    return () => { active = false; clearInterval(timer) }
  }, [page])

  async function save(event) {
    event.preventDefault()
    setSaving(true); setError(''); setNotice('')
    try {
      const value = await notificationsRequest('settings/', { enabled, threshold_percent: threshold })
      setSettings(value); setThreshold(value.threshold_percent)
      setNotice(value.enabled ? 'Уведомления включены. Проверка выполняется в фоне; страницу можно закрыть.' : 'Уведомления выключены. Отчёты сохранены.')
    } catch (e) { setError(e.message) } finally { setSaving(false) }
  }

  return <main className="notifications-page">
    <header className="notifications-header"><div><Link to={ROUTES.dashboard}>← Дашборд</Link><h1>Уведомления</h1><p>Суточное потребление: отклонения, письма и история отчётов</p></div><ThemeToggle /></header>
    {error && <div className="notifications-message error" role="alert">{error}</div>}
    {notice && <div className="notifications-message" role="status">{notice}</div>}
    {!settings ? <p role="status">Загрузка настроек…</p> : <section className="notifications-card">
      <h2>Мои настройки</h2>
      <p>Письма отправляются на адрес вашей учётной записи: <strong>{settings.email}</strong>.</p>
      {!settings.mail_configured && <div className="notifications-message" role="status">Почтовый сервер ещё не настроен. Отчёты будут доступны здесь; для отправки писем администратору нужно настроить почту.</div>}
      <form onSubmit={save}>
        <label className="notifications-toggle"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />Включить проверку и письма о критических отклонениях</label>
        <label className="notifications-threshold">Критическое отклонение, %<input type="number" min="0.01" max="10000" step="0.01" required value={threshold} onChange={e => setThreshold(e.target.value)} /></label>
        <p>Отклонение = (энергия суток − среднее) / |среднее| × 100%. Уведомление создаётся при росте или снижении на заданный процент и больше.</p>
        <p>Среднее считается по полным сопоставимым суткам за предыдущие {settings.baseline_days} календарных дней; нужно не менее {settings.minimum_baseline_days} таких суток с теми же счётчиками. Все даты — по Москве, текущие сутки не проверяются.</p>
        <p>При включении проверяется вчерашний день, затем новые завершённые дни. Неизвестная энергия не считается нулём. При недостатке данных сохраняется отчёт с объяснением без тревожного письма.</p>
        <button disabled={saving} type="submit">{saving ? 'Сохранение…' : 'Сохранить настройки'}</button>
      </form>
      <p>Сохранённое состояние: <strong>{settings.enabled ? 'Включено' : 'Выключено'}</strong>. Последняя фоновая проверка: {formatDateTime(settings.last_check_at)}.</p>
    </section>}
    <section className="notifications-card"><h2>Мои отчёты</h2>
      <p>Порог и значения сохраняются на момент проверки. Статус письма «Передано почтовому серверу» означает принятие SMTP-сервером; папка «Спам» и дальнейшая доставка зависят от почтового сервиса.</p>
      <div className="notifications-table"><table><thead><tr><th>Сутки</th><th>Энергия, кВт·ч</th><th>Среднее, кВт·ч</th><th>Отклонение</th><th>Результат</th><th>Письмо</th></tr></thead>
        <tbody>{reports.results.map(report => <tr key={report.id}>
          <td>{dateLabel(report.day)}</td><td>{formatNumber(report.energy_kwh, 3)}</td><td>{formatNumber(report.average_kwh, 3)}</td>
          <td>{report.deviation_percent == null ? '—' : `${Number(report.deviation_percent) > 0 ? '+' : ''}${formatNumber(report.deviation_percent, 2)}%`}</td>
          <td><strong className={`notifications-status ${report.status}`}>{STATUS[report.status]}</strong><details><summary>Подробнее</summary>
            <p>Порог: {formatNumber(report.threshold_percent, 2)}%. Счётчиков: {report.meter_count}. Полных суток для среднего: {report.baseline_days}.</p>
            {report.explanation && <p>{report.explanation}</p>}
            {report.baseline_dates.length > 0 && <p>Даты среднего: {report.baseline_dates.map(dateLabel).join(', ')}.</p>}
          </details></td>
          <td>{MAIL[report.email_status] ?? report.email_status}{report.email_error && <p>{report.email_error}</p>}{report.email_sent_at && <p>{formatDateTime(report.email_sent_at)}</p>}</td>
        </tr>)}</tbody></table></div>
      {!reports.results.length && <p role="status">Отчётов пока нет. Включите уведомления и сохраните настройки; результат появится после фоновой проверки.</p>}
      <div className="notifications-pagination"><button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>Назад</button><span>Страница {page} · Всего отчётов: {reports.count}</span><button type="button" disabled={page * reports.page_size >= reports.count} onClick={() => setPage(page + 1)}>Далее</button></div>
    </section>
  </main>
}
