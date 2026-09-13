import { useOutletContext } from 'react-router'
import PowerTrendChart from '../../components/charts/PowerTrendChart.jsx'
import RoomConsumptionChart from '../../components/charts/RoomConsumptionChart.jsx'
import {
  PERIOD_OPTIONS,
  formatDateLabel,
  formatDateTime,
  formatNumber,
  formatPercent,
  getDeviceLabel,
  getRoomLabel,
  textOrFallback,
} from './dashboardUtils.js'

export default function DashboardOverviewPage() {
  const {
    filters,
    summary,
    timeseries,
    topDevices,
    roomLoads,
    selectedDataName,
    selectedRoom,
    selectedConsumerClass,
    period,
    selectedDate,
    availableDates,
    powerStats,
    selectedDeviceLabel,
    queryParams,
    resetDependentFilters,
    setSelectedRoom,
    setSelectedConsumerClass,
    setSelectedDataName,
    setPeriod,
    setSelectedDate,
  } = useOutletContext()

  return (
    <section className="energy-view active">
      <header className="energy-topbar">
        <div><h1>Дашборд энергопотребления</h1></div>

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
              <option value="all">Все помещения</option>
              {filters?.consumer_classes.map((item) => <option value={item.consumer_class} key={item.consumer_class}>{textOrFallback(item.consumer_class)}</option>)}
            </select>
          </label>

          <label className="energy-control">
            <span>Период</span>
            <select value={period} onChange={(event) => setPeriod(event.target.value)}>
              {PERIOD_OPTIONS.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
            </select>
          </label>

          <label className="energy-control">
            <span>Дата</span>
            <select value={selectedDate} onChange={(event) => setSelectedDate(event.target.value)} disabled={!filters || period === 'all'}>
              {availableDates.map((dateKey) => <option value={dateKey} key={dateKey}>{formatDateLabel(dateKey)}</option>)}
            </select>
          </label>

          <div className="energy-update-chip">До: <strong>{formatDateTime(powerStats.lastTimestamp ?? summary?.date_to ?? filters?.date_range?.date_to)}</strong></div>
        </div>
      </header>

      <section className="energy-kpi-grid">
        <article className="energy-kpi-card accent-primary">
          <div className="energy-kpi-card__label">Средняя мощность</div>
          <div className="energy-kpi-card__value">{formatNumber(powerStats.avgKw)} <span>кВт</span></div>
          <div className="energy-kpi-card__meta">{selectedDeviceLabel}</div>
        </article>
        <article className="energy-kpi-card">
          <div className="energy-kpi-card__label">Энергия за период</div>
          <div className="energy-kpi-card__value">{formatNumber(summary?.total_energy_kwh, 0)} <span>кВт·ч</span></div>
          <div className="energy-kpi-card__meta">{formatDateTime(summary?.date_from)} - {formatDateTime(summary?.date_to)}</div>
        </article>
        <article className="energy-kpi-card accent-warning">
          <div className="energy-kpi-card__label">Максимальная мощность</div>
          <div className="energy-kpi-card__value">{formatNumber(powerStats.maxKw)} <span>кВт</span></div>
          <div className="energy-kpi-card__meta">максимум за выбранный период</div>
        </article>
        <article className="energy-kpi-card accent-danger">
          <div className="energy-kpi-card__label">Счетчики в выборке</div>
          <div className="energy-kpi-card__value">{formatNumber(summary?.devices_count, 0)} <span>шт.</span></div>
          <div className="energy-kpi-card__meta">Точек периода: {formatNumber(powerStats.pointsCount, 0)}</div>
        </article>
      </section>

      <section className="energy-content-grid energy-content-grid--main">
        <article className="energy-panel energy-chart-panel">
          <div className="energy-panel__head"><div><h2>Динамика активной мощности</h2><p>Почасовая, посуточная или понедельная агрегация по выбранному периоду</p></div></div>
          <div className="energy-chart-wrap"><PowerTrendChart points={timeseries} granularity={queryParams.granularity} /></div>
        </article>
        <article className="energy-panel">
          <div className="energy-panel__head compact"><div><h2>Топ счетчиков</h2><p>По суммарной энергии в выбранном периоде</p></div></div>
          <div className="energy-event-list">
            {topDevices.length ? topDevices.slice(0, 6).map((device, index) => (
              <article className="energy-event-item" key={device.data_name}>
                <div className="energy-event-item__time">#{index + 1}</div>
                <div className="energy-event-item__body"><div className="energy-event-item__title">{getDeviceLabel(device)}</div><div className="energy-event-item__text">{formatNumber(device.energy_kwh, 0)} кВт·ч · максимум {formatNumber(device.max_power_kw)} кВт</div></div>
              </article>
            )) : <div className="energy-list-empty" role="status">Нет данных о счётчиках за выбранный период</div>}
          </div>
        </article>
      </section>

      <section className="energy-content-grid energy-content-grid--secondary">
        <article className="energy-panel">
          <div className="energy-panel__head compact"><div><h2>Потребление по помещениям</h2><p>Помещения, связанные со счетчиками через справочники</p></div></div>
          <RoomConsumptionChart rooms={roomLoads} />
        </article>
        <article className="energy-panel">
          <div className="energy-panel__head compact"><div><h2>Краткая сводка</h2><p>Параметры выбранной выборки из базы</p></div></div>
          <div className="energy-summary-list">
            <SummaryItem label="Средняя мощность периода" value={`${formatNumber(powerStats.avgKw)} кВт`} note="среднее значение агрегированной линии" />
            <SummaryItem label="Минимальная мощность" value={`${formatNumber(powerStats.minKw)} кВт`} note="минимум в выбранном периоде" />
            <SummaryItem label="Максимальная мощность" value={`${formatNumber(powerStats.maxKw)} кВт`} note="максимум в выбранном периоде" />
            <SummaryItem label="Коэффициент загрузки" value={formatPercent(powerStats.loadFactor)} note="средняя мощность / максимум периода" />
            <SummaryItem label="Среднее напряжение" value={`${formatNumber(summary?.avg_voltage_v)} В`} note="по доступным фазам счетчиков" />
            <SummaryItem label="Средняя частота" value={`${formatNumber(summary?.avg_frequency_hz, 2)} Гц`} note="из `frequency_hz_avg`" />
            <SummaryItem label="Самый энергоемкий счетчик" value={topDevices[0] ? getDeviceLabel(topDevices[0]) : 'нет данных'} note={`${formatNumber(topDevices[0]?.energy_kwh, 0)} кВт·ч`} />
            <SummaryItem label="Диапазон выборки" value={formatDateTime(summary?.date_from)} note={`до ${formatDateTime(summary?.date_to)}`} />
            <SummaryItem label="Строк измерений" value={formatNumber(summary?.points, 0)} note="агрегированные записи счетчиков" />
          </div>
        </article>
      </section>
    </section>
  )
}

function SummaryItem({ label, value, note }) {
  return <div className="energy-summary-item"><span className="energy-summary-item__label">{label}</span><strong className="energy-summary-item__value">{value}</strong><span className="energy-summary-item__note">{note}</span></div>
}
