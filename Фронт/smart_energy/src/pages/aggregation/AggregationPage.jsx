import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router'
import { aggregationRequest, getSourceTables } from '../../features/aggregation/api/aggregationApi.js'
import { ROUTES } from '../../app/routes.js'
import { formatDateTime, formatNumber } from '../dashboard/dashboardUtils.js'
import ThemeToggle from '../../shared/theme/ThemeToggle.jsx'
import './aggregation.css'

const FIELDS = {
  ts: 'Время', sensor_name: 'Счётчик', pt: 'Общая активная мощность',
  p1: 'Активная мощность L1', p2: 'Активная мощность L2', p3: 'Активная мощность L3',
  qt: 'Реактивная мощность', q1: 'Реактивная L1', q2: 'Реактивная L2', q3: 'Реактивная L3',
  st: 'Полная мощность', s1: 'Полная L1', s2: 'Полная L2', s3: 'Полная L3',
  i1: 'Ток L1', i2: 'Ток L2', i3: 'Ток L3', u1: 'Напряжение L1', u2: 'Напряжение L2', u3: 'Напряжение L3',
  frequency: 'Частота', t: 'Температура',
}
const STATUS = { queued: 'В очереди', running: 'Выполняется', succeeded: 'Готово', failed: 'Ошибка' }
const KIND = { inspect: 'Обследование', preview: 'Проверка без записи', apply: 'Запись', scheduled: 'По расписанию' }
const initialProfile = {
  name: 'Новый источник', connection: '', source_table: '', timezone: 'Europe/Moscow',
  source_timezone: 'Europe/Moscow', sample_seconds: 5, columns: {}, scales: {}, sensor_map: {},
  directory_table: 'public.sensor_directory', directory_column: 'sensor_name', confirmed: false,
}

function moscowDate(offset = 0) {
  const now = new Date()
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now).map((part) => [part.type, part.value]))
  const day = `${parts.year}-${parts.month}-${parts.day}`
  const result = new Date(`${day}T00:00:00Z`)
  result.setUTCDate(result.getUTCDate() + offset)
  return result.toISOString().slice(0, 10)
}

export default function AggregationPage() {
  const [capabilities, setCapabilities] = useState(null)
  const [aliases, setAliases] = useState([])
  const [tables, setTables] = useState([])
  const [profiles, setProfiles] = useState([])
  const [jobs, setJobs] = useState([])
  const [schedule, setSchedule] = useState(null)
  const [profile, setProfile] = useState(initialProfile)
  const [selectedId, setSelectedId] = useState('')
  const [inspection, setInspection] = useState(null)
  const [inspectionId, setInspectionId] = useState('')
  const [from, setFrom] = useState(() => moscowDate(-1))
  const [to, setTo] = useState(() => moscowDate())
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [focusedJob, setFocusedJob] = useState('')
  const [tab, setTab] = useState('source')
  const [advanced, setAdvanced] = useState('')
  const [replaceConfirmed, setReplaceConfirmed] = useState(false)
  const [repeatDraft, setRepeatDraft] = useState(null)

  const refresh = useCallback(async () => {
    const [nextProfiles, nextJobs, nextSchedule] = await Promise.all([
      aggregationRequest('profiles'), aggregationRequest('jobs'), aggregationRequest('schedule'),
    ])
    setProfiles(nextProfiles)
    setJobs(nextJobs)
    setSchedule(nextSchedule)
  }, [])

  useEffect(() => {
    let alive = true
    async function load() {
      try {
        const caps = await aggregationRequest('capabilities')
        if (!alive) return
        setCapabilities(caps)
        if (!caps.can_manage) return
        const connections = await aggregationRequest('connections')
        if (!alive) return
        setAliases(connections)
        await refresh()
      } catch (e) { if (alive) setError(e.message) }
    }
    load()
    return () => { alive = false }
  }, [refresh])

  useEffect(() => {
    if (!capabilities?.can_manage || !aliases.length) return undefined
    const timer = setInterval(() => refresh().catch((e) => setError(e.message)), 3000)
    return () => clearInterval(timer)
  }, [aliases.length, capabilities, refresh])

  const inspectionJob = jobs.find((job) => job.id === inspectionId)
  const inspected = inspectionJob?.events.find((event) => event.event === 'inspection')
  const effectiveInspection = inspected ?? inspection
  const selected = profiles.find((item) => item.id === selectedId)
  const preview = jobs.find((job) => job.kind === 'preview' && job.status === 'succeeded' &&
    job.profile_id === selectedId && job.date_from === from && job.date_to === to)
  const detail = jobs.find((job) => job.id === focusedJob) ?? jobs[0]

  async function action(fn) {
    setBusy(true)
    setError('')
    setNotice('')
    try { await fn(); await refresh() } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  function changeProfile(key, value) {
    setProfile((current) => ({ ...current, [key]: value, confirmed: key === 'confirmed' ? value : false }))
    setSelectedId('')
    setReplaceConfirmed(false)
  }

  function useSuggestions() {
    if (!effectiveInspection) return
    const columns = Object.fromEntries(Object.entries(effectiveInspection.suggestions).map(([key, value]) => [key, value.column]))
    changeProfile('columns', columns)
    setInspection(effectiveInspection)
    setNotice('Соответствия предложены. Проверьте единицы, время, интервал и имена счётчиков.')
  }

  async function submitJob(kind) {
    const job = await aggregationRequest('jobs', { profile_id: selectedId, kind, from, to,
      ...(kind === 'apply' ? { preview_id: preview.id } : {}),
    })
    setFocusedJob(job.id)
    setNotice('Задание поставлено в очередь. Страницу можно закрыть.')
    setReplaceConfirmed(false)
  }

  return (
    <main className="aggregation-page">
      <header className="aggregation-header"><div><Link to={ROUTES.dashboard}>← Дашборд</Link><h1>Источники и агрегации</h1><p>Настройка измерений, проверка качества и история обработки</p></div><ThemeToggle /></header>
      {error && <div className="aggregation-message error" role="alert">{error}</div>}
      {notice && <div className="aggregation-message" role="status">{notice}</div>}
      {capabilities && !capabilities.can_manage ? <p>Управление доступно операторам с правами сотрудника.</p> : capabilities?.can_manage ? <>
        {!capabilities.stored_energy_enabled && <div className="aggregation-message">Приложение пока использует прежний расчёт энергии. После проверки истории включите ENERGY_USE_STORED_ENERGY для Django и аналитического сервиса.</div>}
        <nav className="aggregation-tabs" aria-label="Разделы адаптера">{[['source', 'Источник и соответствия'], ['refresh', 'Пересчёт'], ['history', 'Расписание и история']].map(([id, label]) => <button type="button" key={id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{label}</button>)}</nav>
        {tab === 'source' && <section className="aggregation-card">
          <h2>Профиль источника</h2>
          <label>Сохранённые версии<select value={selectedId} onChange={(e) => {
            const next = profiles.find((p) => p.id === e.target.value)
            setSelectedId(next?.id ?? '')
            if (next) { setProfile(next.document); setAdvanced(JSON.stringify({ sensor_map: next.document.sensor_map ?? {}, quality: next.document.quality ?? {}, directory_connection: next.document.directory_connection ?? next.document.connection, directory_table: next.document.directory_table ?? 'public.sensor_directory', directory_column: next.document.directory_column ?? 'sensor_name' }, null, 2)); setInspection(null); setInspectionId('') }
          }}><option value="">Новая версия</option>{profiles.filter((p) => p.document.columns).map((p) => <option key={p.id} value={p.id}>{p.document.name} · {p.id.slice(0, 8)}{p.active ? ' · активен' : ''}{p.validated ? ' · проверен' : ''}</option>)}</select></label>
          <div className="aggregation-grid">
            <label>Название<input value={profile.name} onChange={(e) => changeProfile('name', e.target.value)} /></label>
            <label>Подключение<select value={profile.connection} onChange={(e) => { changeProfile('connection', e.target.value); setTables([]); setInspectionId(''); setInspection(null) }}><option value="">Выберите подключение</option>{aliases.map((alias) => <option key={alias}>{alias}</option>)}</select></label>
            <label>Таблица<input list="source-tables" value={profile.source_table} onChange={(e) => { changeProfile('source_table', e.target.value); setInspectionId(''); setInspection(null) }} placeholder="schema.table" /><datalist id="source-tables">{tables.map((table) => <option key={table.name} value={table.name} />)}</datalist></label>
            <label>Часовой пояс исходных timestamp<input value={profile.source_timezone} onChange={(e) => changeProfile('source_timezone', e.target.value)} /></label>
            <label>Интервал измерений, секунд<input type="number" min="0.001" step="any" value={profile.sample_seconds} onChange={(e) => changeProfile('sample_seconds', Number(e.target.value))} /></label>
          </div>
          <div className="aggregation-actions">
            <button disabled={busy || !profile.connection} onClick={() => action(async () => setTables(await getSourceTables(profile.connection)))}>Показать таблицы</button>
            <button disabled={busy || !profile.connection || !profile.source_table} onClick={() => action(async () => {
              const job = await aggregationRequest('inspect', { connection: profile.connection, source_table: profile.source_table })
              setInspectionId(job.id); setFocusedJob(job.id); setNotice('Обследование запущено. Результат появится на этой странице.')
            })}>Обследовать источник</button>
            <button disabled={!effectiveInspection} onClick={useSuggestions}>Применить предложения</button>
          </div>
          {inspectionJob && <p role="status">Обследование: {STATUS[inspectionJob.status]} {inspectionJob.error}</p>}
          <p>Преобразование в Вт, вар, В·А, А, В и Гц: множитель 1 сохраняет единицу, 1000 переводит кВт в Вт. Название столбца не подтверждает единицу.</p>
          <div className="aggregation-table-scroll"><table><thead><tr><th>Назначение</th><th>Столбец источника</th><th>Множитель</th></tr></thead><tbody>{Object.entries(FIELDS).map(([key, label]) => <tr key={key}><td>{label}</td><td><input list="source-columns" value={profile.columns[key] ?? ''} placeholder="Не задан" onChange={(e) => changeProfile('columns', { ...profile.columns, [key]: e.target.value || null })} /></td><td>{!['ts', 'sensor_name'].includes(key) && <input type="number" min="0.000001" step="any" aria-label={`Множитель: ${label}`} value={profile.scales?.[key] ?? 1} onChange={(e) => changeProfile('scales', { ...profile.scales, [key]: Number(e.target.value) })} />}</td></tr>)}</tbody></table></div>
          <datalist id="source-columns">{effectiveInspection?.columns.map((column) => <option key={column.name} value={column.name}>{column.type} {column.comment}</option>)}</datalist>
          {effectiveInspection && <details><summary>Структура и пример показаний</summary><pre>{JSON.stringify({ columns: effectiveInspection.columns, sample: effectiveInspection.sample }, null, 2)}</pre></details>}
          <details><summary>Справочник, соответствия ID и пороги качества</summary><p>JSON: sensor_map связывает ID заказчика с именами приложения. Порог min_coverage — минимальная доля показаний за сутки.</p><textarea rows="12" aria-label="Дополнительная конфигурация" value={advanced} onChange={(e) => { setAdvanced(e.target.value); setSelectedId(''); setProfile({ ...profile, confirmed: false }) }} placeholder={'{"sensor_map": {}, "quality": {"min_coverage": 0.9}}'} /></details>
          <label className="aggregation-check"><input type="checkbox" checked={profile.confirmed} onChange={(e) => changeProfile('confirmed', e.target.checked)} />Подтверждаю соответствия, единицы, интервал и часовой пояс</label>
          <div className="aggregation-actions"><button disabled={busy || !profile.confirmed} onClick={() => action(async () => {
            const extra = advanced.trim() ? JSON.parse(advanced) : {}
            if (Object.keys(extra).some((key) => !['sensor_map', 'quality', 'directory_connection', 'directory_table', 'directory_column'].includes(key))) throw new Error('В дополнительных настройках допустимы только справочник, sensor_map и quality')
            const saved = await aggregationRequest('profiles', { ...profile, ...extra })
            setSelectedId(saved.id); setProfile(saved.document); setNotice('Новая версия сохранена. Выполните предварительный расчёт во вкладке «Пересчёт».')
          })}>Сохранить новую версию</button><button disabled={busy || !selected?.validated || selected.active} onClick={() => action(async () => { await aggregationRequest(`profiles/${selectedId}/activate`, {}); setNotice('Профиль активирован. Расписание включается отдельно.') })}>Активировать проверенную версию</button></div>
        </section>}
        {tab === 'refresh' && <section className="aggregation-card"><h2>Пересчёт периода</h2><p>Профиль: <strong>{selected?.document.name ?? 'Сначала выберите или сохраните профиль'}</strong> {selectedId && `(${selectedId.slice(0, 8)})`}</p><div className="aggregation-grid"><label>С даты включительно<input type="date" value={from} onChange={(e) => { setFrom(e.target.value); setReplaceConfirmed(false) }} /></label><label>До даты, не включая<input type="date" value={to} onChange={(e) => { setTo(e.target.value); setReplaceConfirmed(false) }} /></label></div><p>Сегодняшние и будущие сутки пропускаются. При записи существующие строки всех счётчиков выбранных дней заменяются.</p><button disabled={busy || !selectedId || !from || !to} onClick={() => action(() => submitJob('preview'))}>Предварительный расчёт без записи</button>
          {preview && <div className="aggregation-message">Предварительный расчёт пройден. Проверьте результаты задания перед записью.</div>}
          <label className="aggregation-check"><input type="checkbox" checked={replaceConfirmed} onChange={(e) => setReplaceConfirmed(e.target.checked)} />Результаты проверены, существующие агрегаты выбранных дней можно заменить</label><button disabled={busy || !preview || !replaceConfirmed} onClick={() => action(() => submitJob('apply'))}>Записать агрегаты</button>
        </section>}
        {tab === 'history' && <section className="aggregation-card"><h2>Ежедневная обработка</h2><p>В 00:00 по Москве — предыдущие завершённые сутки. После простоя — пропущенные дни.</p><p>Активный профиль: {schedule?.active_profile?.document.name ?? 'Не выбран'} · Обработано до: {schedule?.active_profile?.processed_until ?? 'Ещё не запускалось'} (не включая)</p><p>Следующий основной запуск: {schedule?.next_run ? formatDateTime(schedule.next_run) : 'Расписание выключено'}</p><div className="aggregation-actions"><button disabled={busy || !schedule || (!schedule.enabled && !schedule.active_profile)} onClick={() => action(async () => { await aggregationRequest('schedule', { enabled: !schedule.enabled }) })}>{schedule?.enabled ? 'Выключить расписание' : 'Включить расписание'}</button><label>Повтор вчерашнего дня, минут после 00:00 (0 — выключен)<input type="number" min="0" max="1439" value={repeatDraft ?? schedule?.repeat_minutes ?? 15} onChange={(e) => setRepeatDraft(Number(e.target.value))} /></label><button disabled={busy || !schedule} onClick={() => action(async () => { await aggregationRequest('schedule', { repeat_minutes: repeatDraft ?? schedule.repeat_minutes }); setRepeatDraft(null) })}>Сохранить время повтора</button></div><p>При включении после простоя обработка может начаться сразу. Пустой или неполный источник останавливает запись; ошибки видны ниже.</p></section>}
        <section className="aggregation-card"><h2>Задания</h2><div className="aggregation-table-scroll"><table><thead><tr><th>Создано</th><th>Операция / период</th><th>Состояние</th><th>Результат</th></tr></thead><tbody>{jobs.map((job) => {
          const days = job.events.filter((event) => event.event === 'day')
          return <tr key={job.id}><td><button className="aggregation-link" onClick={() => setFocusedJob(job.id)}>{formatDateTime(job.created_at)}</button></td><td>{KIND[job.kind]}<br />{job.date_from} {job.date_to && `→ ${job.date_to}`}</td><td>{STATUS[job.status]}</td><td>{days.length ? `${days.length} дн., ${days.reduce((n, d) => n + d.hourly_rows, 0)} часовых строк` : job.error ?? '—'}</td></tr>
        })}</tbody></table></div>{!jobs.length && <p>Заданий пока нет.</p>}
          {detail && <div><h3>{KIND[detail.kind]} · {detail.id.slice(0, 8)}</h3>{detail.error && <p className="aggregation-message error">{detail.error}</p>}{detail.events.filter((event) => event.event === 'quality').slice(-5).map((event, index) => <div className="aggregation-quality" key={`${event.date}-${index}`}><strong>{event.date}</strong><span>{formatNumber(event.points, 0)} показаний · медиана интервала {formatNumber(event.median_seconds, 3)} с · дубликаты {event.duplicates}</span><div className="aggregation-coverage">{Object.entries(event.coverage).map(([name, coverage]) => <div key={name}><span>{name}</span><meter min="0" max="1" value={Math.min(coverage, 1)} /><span>{formatNumber(coverage * 100, 1)}%</span></div>)}</div>{event.errors.map((message) => <p key={message} className="aggregation-message error">{message}</p>)}</div>)}<details><summary>Подробный журнал</summary><pre>{JSON.stringify(detail.events, null, 2)}</pre></details></div>}
        </section>
      </> : !error && <p role="status">Проверяем доступ…</p>}
    </main>
  )
}
