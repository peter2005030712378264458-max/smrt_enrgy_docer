import { useOutletContext } from 'react-router'
import {
  ANALYTICS_ALTERNATIVE_OPTIONS,
  formatDateLabel,
  formatDateTime,
  formatNumber,
  getAnalyticsTestLabel,
  getDeviceLabel,
  getRoomLabel,
  textOrFallback,
} from './dashboardUtils.js'

export default function AnalyticsPage() {
  const {
    filters,
    selectedDataName,
    selectedRoom,
    selectedConsumerClass,
    availableDates,
    analyticsPeriod1From,
    analyticsPeriod1To,
    analyticsPeriod2From,
    analyticsPeriod2To,
    analyticsAlpha,
    analyticsAlternative,
    analyticsResult,
    analyticsLoading,
    analyticsError,
    resetDependentFilters,
    setSelectedRoom,
    setSelectedConsumerClass,
    setSelectedDataName,
    setAnalyticsPeriod1From,
    setAnalyticsPeriod1To,
    setAnalyticsPeriod2From,
    setAnalyticsPeriod2To,
    setAnalyticsAlpha,
    setAnalyticsAlternative,
    handleAnalyticsCompare,
  } = useOutletContext()

  return (
    <section className="energy-view active">
      <header className="energy-topbar">
        <div><h1>Аналитика</h1></div>
        <div className="energy-toolbar">
          <label className="energy-control energy-control--wide">
            <span>Счетчик</span>
            <select value={selectedDataName} onChange={(event) => resetDependentFilters(event.target.value)} disabled={!filters}>
              <option value="all">Все счетчики</option>
              {filters?.devices.map((device) => <option value={device.data_name} key={device.data_name}>{getDeviceLabel(device)}</option>)}
            </select>
          </label>
          <label className="energy-control">
            <span>Помещение</span>
            <select value={selectedRoom} onChange={(event) => { setSelectedRoom(event.target.value); setSelectedDataName('all') }} disabled={!filters || selectedDataName !== 'all'}>
              <option value="all">Все помещения</option>
              {filters?.rooms.map((room) => <option value={room.room} key={room.room}>{getRoomLabel(room)}</option>)}
            </select>
          </label>
          <label className="energy-control">
            <span>Расширенное название помещения</span>
            <select value={selectedConsumerClass} onChange={(event) => { setSelectedConsumerClass(event.target.value); setSelectedDataName('all') }} disabled={!filters || selectedDataName !== 'all'}>
              <option value="all">Все классы</option>
              {filters?.consumer_classes.map((item) => <option value={item.consumer_class} key={item.consumer_class}>{textOrFallback(item.consumer_class)}</option>)}
            </select>
          </label>
        </div>
      </header>

      {analyticsError ? <div className="energy-alert" role="alert">{analyticsError}</div> : null}

      <form className="energy-panel energy-analysis-form" onSubmit={handleAnalyticsCompare}>
        <div className="energy-analysis-periods">
          <PeriodFieldset legend="Первый период" from={analyticsPeriod1From} to={analyticsPeriod1To} setFrom={setAnalyticsPeriod1From} setTo={setAnalyticsPeriod1To} dates={availableDates} />
          <PeriodFieldset legend="Второй период" from={analyticsPeriod2From} to={analyticsPeriod2To} setFrom={setAnalyticsPeriod2From} setTo={setAnalyticsPeriod2To} dates={availableDates} />
        </div>
        <div className="energy-analysis-actions">
          <label className="energy-control energy-control--wide">
            <span>Альтернативная гипотеза</span>
            <select value={analyticsAlternative} onChange={(event) => setAnalyticsAlternative(event.target.value)}>
              {ANALYTICS_ALTERNATIVE_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
            </select>
          </label>
          <label className="energy-control">
            <span>Уровень значимости</span>
            <select value={analyticsAlpha} onChange={(event) => setAnalyticsAlpha(event.target.value)}>
              <option value="0.10">0.10</option><option value="0.05">0.05</option><option value="0.01">0.01</option>
            </select>
          </label>
          <button className="energy-ghost-button" type="submit" disabled={analyticsLoading || !filters}>{analyticsLoading ? 'Считаем...' : 'Сравнить'}</button>
        </div>
      </form>

      {analyticsResult ? <AnalyticsResult result={analyticsResult} /> : (
        <section className="energy-panel energy-analysis-placeholder"><div className="energy-panel__head compact"><div><h2>Выберите два периода</h2><p>После сравнения здесь появятся z-статистика, критическое значение и решение по выбранной гипотезе.</p></div></div></section>
      )}
    </section>
  )
}

function PeriodFieldset({ legend, from, to, setFrom, setTo, dates }) {
  return (
    <fieldset className="energy-analysis-period">
      <legend>{legend}</legend>
      <label className="energy-control"><span>Начало</span><input type="date" value={from} min={dates[0] ?? undefined} max={dates.at(-1) ?? undefined} onChange={(event) => setFrom(event.target.value)} /></label>
      <label className="energy-control"><span>Конец</span><input type="date" value={to} min={dates[0] ?? undefined} max={dates.at(-1) ?? undefined} onChange={(event) => setTo(event.target.value)} /></label>
    </fieldset>
  )
}

function AnalyticsResult({ result }) {
  const nullHypothesis = {
    two_sided: 'H0: среднее потребление в двух периодах одинаковое',
    greater: 'H0: среднее потребление первого периода не больше второго',
    less: 'H0: среднее потребление первого периода не меньше второго',
  }[result.alternative] ?? result.hypothesis

  return (
    <section className="energy-analysis-result">
      <section className="energy-kpi-grid">
        <Kpi className={result.reject_null ? 'energy-kpi-card accent-danger' : 'energy-kpi-card accent-primary'} label="Решение" value={result.reject_null ? 'Отвергаем H0' : 'Не отвергаем H0'} note={`${nullHypothesis}. ${result.alternative_hypothesis ?? ''}`} />
        <Kpi className={result.reject_null ? 'energy-kpi-card accent-danger' : 'energy-kpi-card accent-primary'} label="z статистическое" value={formatNumber(result.z_statistic, 3)} note="разность средних / стандартная ошибка" />
        <Kpi className="energy-kpi-card" label="z критическое" value={formatNumber(result.z_critical, 3)} note={`таблица: CDF ${formatNumber(result.table_lookup?.matched_cdf, 5)}`} />
        <Kpi className="energy-kpi-card accent-warning" label="Уровень значимости" value={formatNumber(result.alpha, 2)} note={getAnalyticsTestLabel(result.alternative)} />
      </section>
      <section className="energy-content-grid energy-content-grid--secondary">
        {(result.periods ?? []).map((periodStats, index) => (
          <article className="energy-panel" key={periodStats.key}>
            <div className="energy-panel__head compact"><div><h2>{index === 0 ? 'Первый период' : 'Второй период'}</h2><p>{formatDateLabel(periodStats.date_from)} - {formatDateLabel(periodStats.date_to)}</p></div></div>
            <div className="energy-summary-list">
              <SummaryItem label="Средняя мощность" value={`${formatNumber(periodStats.mean_kw, 2)} кВт`} note="часовые агрегаты выбранной выборки" />
              <SummaryItem label="Энергия за период" value={`${formatNumber(periodStats.total_energy_kwh, 0)} кВт·ч`} note="сумма часовых значений" />
              <SummaryItem label="Наблюдения" value={formatNumber(periodStats.observations, 0)} note={`исходных точек: ${formatNumber(periodStats.source_points, 0)}`} />
              <SummaryItem label="Стандартное отклонение" value={`${formatNumber(periodStats.stddev_kw, 2)} кВт`} note={`фактически: ${formatDateTime(periodStats.actual_from)} - ${formatDateTime(periodStats.actual_to)}`} />
            </div>
          </article>
        ))}
      </section>
      <article className="energy-panel energy-analysis-conclusion">
        <div className="energy-panel__head compact"><div><h2>Вывод</h2><p>{result.conclusion}</p></div></div>
        <div className="energy-summary-list">
          <SummaryItem label="Разность средних" value={`${formatNumber(result.difference_mean_kw, 3)} кВт`} />
          <SummaryItem label="Правило решения" value={result.decision_rule ?? 'abs(z_statistic) > z_critical'} />
        </div>
      </article>
    </section>
  )
}

function Kpi({ className, label, value, note }) {
  return <article className={className}><div className="energy-kpi-card__label">{label}</div><div className={`energy-kpi-card__value${label === 'Решение' ? ' energy-analysis-decision' : ''}`}>{value}</div><div className="energy-kpi-card__meta">{note}</div></article>
}

function SummaryItem({ label, value, note }) {
  return <div className="energy-summary-item"><span className="energy-summary-item__label">{label}</span><strong className="energy-summary-item__value">{value}</strong>{note ? <span className="energy-summary-item__note">{note}</span> : null}</div>
}
