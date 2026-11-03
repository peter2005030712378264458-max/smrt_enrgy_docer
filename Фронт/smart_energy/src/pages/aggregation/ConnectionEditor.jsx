import { useEffect, useRef, useState } from 'react'
import { aggregationRequest } from '../../features/aggregation/api/aggregationApi.js'

const initial = { alias: '', host: '', port: '5432', database: '', user: '', password: '', sslmode: 'prefer' }

export default function ConnectionEditor({ onSaved, onClose }) {
  const dialogRef = useRef(null)
  const [draft, setDraft] = useState(initial)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    const dialog = dialogRef.current
    dialog.showModal()
    return () => dialog.close()
  }, [])

  function change(key, value) {
    setDraft(current => ({ ...current, [key]: value }))
    setMessage(''); setError('')
  }
  async function submit(save) {
    setBusy(true); setError(''); setMessage('')
    try {
      const result = await aggregationRequest(save ? 'connections' : 'connections/test',
        { ...draft, port: Number(draft.port) })
      if (save) {
        setDraft(initial)
        await onSaved(result.alias)
      } else setMessage('PostgreSQL доступен. Проверка выполнена в режиме чтения.')
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  return <dialog ref={dialogRef} className="aggregation-connection-dialog" aria-labelledby="connection-dialog-title" onCancel={event => {
    if (busy) event.preventDefault()
    else onClose()
  }}>
    <div className="aggregation-connection-dialog__header">
      <h2 id="connection-dialog-title">Новое подключение PostgreSQL</h2>
      <button type="button" disabled={busy} onClick={onClose} aria-label="Закрыть окно подключения">✕</button>
    </div>
    <p>Адрес должен быть доступен из контейнера адаптера. Для базы на вашем компьютере используйте host.docker.internal. Пароль хранится в зашифрованном серверном реестре отдельно от профиля.</p>
    <form onSubmit={event => { event.preventDefault(); submit(true) }}>
      <div className="aggregation-grid">
        <label>Имя подключения<input required pattern="[a-z][a-z0-9_-]{1,47}" maxLength={48} value={draft.alias} onChange={e => change('alias', e.target.value)} placeholder="company-db" /></label>
        <label>Сервер<input required value={draft.host} onChange={e => change('host', e.target.value)} placeholder="db.example.org" /></label>
        <label>Порт<input required type="number" min="1" max="65535" value={draft.port} onChange={e => change('port', e.target.value)} /></label>
        <label>База данных<input required value={draft.database} onChange={e => change('database', e.target.value)} /></label>
        <label>Пользователь<input required autoComplete="off" value={draft.user} onChange={e => change('user', e.target.value)} /></label>
        <label>Пароль<input required type="password" autoComplete="new-password" value={draft.password} onChange={e => change('password', e.target.value)} /></label>
        <label>SSL<select value={draft.sslmode} onChange={e => change('sslmode', e.target.value)}>
          <option value="prefer">Предпочитать SSL</option><option value="require">Требовать SSL</option>
          <option value="verify-full">Проверять сертификат и имя</option><option value="verify-ca">Проверять сертификат</option>
          <option value="disable">Без SSL</option>
        </select></label>
      </div>
      {error && <p className="aggregation-message error" role="alert">{error}</p>}
      {message && <p className="aggregation-message" role="status">{message}</p>}
      <div className="aggregation-actions">
        <button type="button" disabled={busy} onClick={() => submit(false)}>Проверить подключение</button>
        <button type="button" disabled={busy} onClick={onClose}>Отмена</button>
        <button type="submit" disabled={busy}>{busy ? 'Подключение…' : 'Проверить и сохранить'}</button>
      </div>
    </form>
  </dialog>
}
