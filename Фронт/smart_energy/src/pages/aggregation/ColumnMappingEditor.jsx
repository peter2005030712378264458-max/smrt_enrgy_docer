import { useState } from 'react'
import { mappingVisibility } from './columnMappingVisibility.js'
import { mappingAvailability, METRIC_LABELS } from './mappingAvailability.js'

const COLUMN_MIME = 'application/x-energy-source-column'

function sampleValue(rows, name) {
  const value = rows?.find((row) => row[name] !== null && row[name] !== undefined)?.[name]
  if (value === undefined) return 'Нет примера значения'
  const text = typeof value === 'object' ? JSON.stringify(value) : String(value)
  return text || '(пустая строка)'
}

export default function ColumnMappingEditor({ fields, profile, inspection, onChange }) {
  const [query, setQuery] = useState('')
  const [selectedName, setSelectedName] = useState('')
  const [draggedName, setDraggedName] = useState('')
  const [dropField, setDropField] = useState('')
  const [announcement, setAnnouncement] = useState('')
  const [showAll, setShowAll] = useState(false)
  const columns = inspection?.columns ?? []
  const selected = columns.find((column) => column.name === selectedName)
  const search = query.trim().toLocaleLowerCase()
  const visibleColumns = columns.filter((column) =>
    `${column.name} ${column.type} ${column.comment ?? ''}`.toLocaleLowerCase().includes(search))
  const mappedCount = Object.keys(fields).filter((field) => profile.columns[field]).length
  const visibility = mappingVisibility(fields, profile.columns, inspection)
  const availability = mappingAvailability(profile.columns, inspection)
  const targetFields = showAll ? Object.entries(fields) : visibility.visible

  function assign(field, name) {
    onChange('columns', { ...profile.columns, [field]: name || null })
    setSelectedName('')
    setDraggedName('')
    setDropField('')
    setAnnouncement(name ? `${fields[field]}: назначен столбец ${name}` : `${fields[field]}: соответствие удалено`)
  }

  function resetDrag() {
    setDraggedName('')
    setDropField('')
  }

  return (
    <section className="column-mapping" aria-label="Сопоставление столбцов и показателей">
      <div className="column-mapping__intro">
        <h3>Сопоставление показателей</h3>
        <p>Перетащите карточку столбца на нужный показатель. Или выберите карточку и нажмите «Назначить» у показателя — это работает и с клавиатуры.</p>
      </div>
      <p className="column-mapping__announcement" role="status" aria-live="polite">{announcement}</p>
      <p className="column-mapping__sensor-note">Обязательны время и счётчик. Строки без времени нельзя распределить по суткам: расчёт остановится, прежние агрегаты сохранятся. Недоступные измерения остаются NULL, а доступные рассчитываются.</p>
      {inspection && <div className="aggregation-quality" aria-label="Доступность показателей">
        {availability.errors.map(message => <p className="aggregation-message error" role="alert" key={message}>{message}</p>)}
        <p><strong>Можно рассчитать по структуре:</strong> {availability.metrics.length ? availability.metrics.map(metric => METRIC_LABELS[metric]).join(', ') : 'только число показаний, если назначены время и счётчик'}.</p>
        {availability.unavailable.length > 0 && <p><strong>Нет данных для показателей:</strong> {availability.unavailable.map(metric => METRIC_LABELS[metric]).join(', ')}. Для них будет сохранён NULL.</p>}
        {!availability.metrics.includes('active_power_w_avg') && <p className="aggregation-message warning">Без общей мощности или всех трёх фаз полное потребление неизвестно. Отдельные фазы и остальные измерения можно агрегировать; сумма неполного набора фаз не подставляется вместо общей мощности.</p>}
      </div>}
      <div className="column-mapping__layout">
        <aside className="column-mapping__source" aria-label="Столбцы исходной таблицы">
          <h4>Столбцы источника <span>{columns.length}</span></h4>
          {columns.length ? <>
            <label>Поиск столбца<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Название, тип или описание" /></label>
            <p className="column-mapping__hint">{selected ? `Выбран ${selected.name}. Назначьте его показателю справа.` : 'Карточки можно перетаскивать или выбирать кликом.'}</p>
            <div className="column-mapping__columns">
              {visibleColumns.map((column) => {
                const assignments = Object.entries(fields).filter(([field]) => profile.columns[field] === column.name)
                const example = sampleValue(inspection.sample, column.name)
                return <button
                  type="button"
                  key={column.name}
                  className={`column-mapping__column${selected?.name === column.name ? ' is-selected' : ''}${draggedName === column.name ? ' is-dragging' : ''}`}
                  draggable
                  aria-pressed={selected?.name === column.name}
                  aria-label={`Выбрать столбец ${column.name}, тип ${column.type}`}
                  onClick={() => setSelectedName(selected?.name === column.name ? '' : column.name)}
                  onDragStart={(event) => {
                    event.dataTransfer.setData(COLUMN_MIME, column.name)
                    event.dataTransfer.setData('text/plain', column.name)
                    event.dataTransfer.effectAllowed = 'copy'
                    setDraggedName(column.name)
                  }}
                  onDragEnd={resetDrag}
                >
                  <span className="column-mapping__column-heading"><strong>{column.name}</strong><span className="column-mapping__type">{column.declared_type ?? column.type}</span></span>
                  {column.readable === false && <span className="column-mapping__missing-note">Нет права SELECT; значение недоступно</span>}
                  {column.comment && <span className="column-mapping__comment">{column.comment}</span>}
                  <span className="column-mapping__example" title={example}>Пример: {example}</span>
                  {assignments.length > 0 && <span className="column-mapping__assigned">Назначен: {assignments.map(([, label]) => label).join(', ')}</span>}
                </button>
              })}
              {!visibleColumns.length && <p>Столбцы не найдены. Измените поисковый запрос.</p>}
            </div>
          </> : <p className="column-mapping__hint">Выберите подключение и таблицу, затем нажмите «Обследовать источник». Здесь появятся карточки столбцов. До обследования можно ввести соответствия вручную.</p>}
        </aside>
        <div className="column-mapping__targets">
          <h4>Показатели <span>Назначено: {mappedCount} · Показано: {targetFields.length} из {Object.keys(fields).length}</span></h4>
          {inspection && <div className="column-mapping__visibility">
            <div>
              <strong>{showAll ? 'Все показатели' : 'По данным источника'}</strong>
              <p>{visibility.hiddenCount
                ? `${showAll ? 'Дополнительно доступны' : 'Скрыты'} показатели без найденного столбца: ${visibility.hiddenCount}. Их можно назначить вручную.`
                : 'Все поддерживаемые показатели доступны для настройки.'}</p>
            </div>
            {visibility.hiddenCount > 0 && <button type="button" aria-expanded={showAll} aria-controls="column-mapping-targets" onClick={() => setShowAll(value => !value)}>{showAll ? 'Скрыть дополнительные' : `Показать остальные (${visibility.hiddenCount})`}</button>}
          </div>}
          <p className="column-mapping__sensor-note">У разных датчиков могут быть разные наборы показаний. Пустое значение не означает ноль и не скрывает показатель для остальных датчиков.</p>
          <div className="aggregation-table-scroll"><table>
            <thead><tr><th scope="col">Показатель</th><th scope="col">Столбец источника</th><th scope="col">Множитель</th></tr></thead>
            <tbody id="column-mapping-targets">{targetFields.map(([field, label]) => {
              const assigned = profile.columns[field]
              return <tr
                key={field}
                className={`column-mapping__target${assigned ? ' is-mapped' : ''}${dropField === field ? ' is-over' : ''}${visibility.missing.has(field) ? ' is-missing' : ''}`}
                onDragOver={(event) => {
                  if (!draggedName && !event.dataTransfer.types.includes(COLUMN_MIME)) return
                  event.preventDefault()
                  event.dataTransfer.dropEffect = 'copy'
                  setDropField(field)
                }}
                onDragLeave={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget)) setDropField('')
                }}
                onDrop={(event) => {
                  event.preventDefault()
                  const name = event.dataTransfer.getData(COLUMN_MIME)
                  if (columns.some((column) => column.name === name)) assign(field, name)
                  resetDrag()
                }}
              >
                <th scope="row"><span>{label}</span><code>{field}</code>{visibility.required.has(field) && <span className="column-mapping__required">Для расчёта</span>}</th>
                <td>
                  <div className="column-mapping__drop-zone">
                    <input list="source-columns" aria-label={`Столбец для показателя: ${label}`} aria-invalid={visibility.missing.has(field) || availability.invalidTypes.has(field) || undefined} aria-describedby={visibility.missing.has(field) ? `column-mapping-missing-${field}` : undefined} value={assigned ?? ''} placeholder="Перетащите столбец сюда" onChange={(event) => assign(field, event.target.value)} />
                    {visibility.missing.has(field) && <p id={`column-mapping-missing-${field}`} className="column-mapping__missing-note">Столбец отсутствует или недоступен. Для измерения будет сохранён NULL; время и счётчик обязательны.</p>}
                    <div className="column-mapping__target-actions">
                      {selected && <button type="button" onClick={() => assign(field, selected.name)}>Назначить {selected.name}</button>}
                      {assigned && <button type="button" className="column-mapping__clear" aria-label={`Удалить соответствие: ${label}`} onClick={() => assign(field, '')}>Убрать</button>}
                    </div>
                  </div>
                </td>
                <td>{!['ts', 'sensor_name'].includes(field) && <input type="number" min="0.000001" step="any" aria-label={`Множитель: ${label}`} value={profile.scales?.[field] ?? 1} onChange={(event) => onChange('scales', { ...profile.scales, [field]: Number(event.target.value) })} />}</td>
              </tr>
            })}</tbody>
          </table></div>
        </div>
      </div>
      <datalist id="source-columns">{columns.map((column) => <option key={column.name} value={column.name}>{column.type} {column.comment}</option>)}</datalist>
    </section>
  )
}
