import { useOutletContext } from 'react-router'
import { getDeviceLabel, textOrFallback } from './dashboardUtils.js'

export default function ConnectionsPage() {
  const { filters, selectedDataName, deviceDetail, resetDependentFilters } = useOutletContext()

  return (
    <section className="energy-view active">
      <header className="energy-topbar">
        <div><h1>Связи счетчика</h1><p>Помещения, связанные с выбранным счетчиком через справочники базы данных.</p></div>
        <div className="energy-toolbar">
          <label className="energy-control energy-control--wide">
            <span>Счетчик</span>
            <select value={selectedDataName} onChange={(event) => resetDependentFilters(event.target.value)} disabled={!filters}>
              <option value="all">Выберите счетчик</option>
              {filters?.devices.map((device) => <option value={device.data_name} key={device.data_name}>{getDeviceLabel(device)}</option>)}
            </select>
          </label>
        </div>
      </header>

      {selectedDataName === 'all' ? (
        <section className="energy-panel"><div className="energy-panel__head compact"><div><h2>Выберите счетчик</h2><p>После выбора появятся связанные помещения из базы.</p></div></div></section>
      ) : (
        <section className="energy-content-grid">
          <article className="energy-panel">
            <div className="energy-panel__head compact"><div><h2>Помещения</h2><p>{deviceDetail?.consumers?.length ?? 0} записей</p></div></div>
            <div className="energy-table">
              {(deviceDetail?.consumers ?? []).map((consumer, index) => (
                <div className="energy-table__row energy-table__row--rooms" key={`${consumer.power_consumer ?? consumer.consumer_class}-${index}`}>
                  <span>{textOrFallback(consumer.power_consumer)}</span>
                  <strong>{textOrFallback(consumer.consumer_class)}</strong>
                  <em>{textOrFallback(consumer.room)}</em>
                  <em>{textOrFallback([consumer.floor, consumer.building].filter(Boolean).join(' · '))}</em>
                </div>
              ))}
            </div>
          </article>
          <article className="energy-panel">
            <div className="energy-panel__head compact"><div><h2>Автоматы и помещения</h2><p>{deviceDetail?.breakers?.length ?? 0} записей</p></div></div>
            <div className="energy-table">
              {(deviceDetail?.breakers ?? []).map((breaker, index) => (
                <div className="energy-table__row" key={`${breaker.breaker ?? breaker.room}-${index}`}>
                  <span>{textOrFallback(breaker.breaker)}</span>
                  <strong>{textOrFallback(breaker.room)}</strong>
                  <em>{textOrFallback([breaker.floor, breaker.building].filter(Boolean).join(' · '))}</em>
                </div>
              ))}
            </div>
          </article>
        </section>
      )}
    </section>
  )
}
