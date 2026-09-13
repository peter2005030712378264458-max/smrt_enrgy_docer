function ProfilePanel({ currentUser, onLogout }) {
  return (
    <section className="profile-panel">
      <header className="auth-header">
        <h1>Сессия активна</h1>
        <p>Авторизация работает через `access` в памяти и `refresh` в HttpOnly cookie.</p>
      </header>

      <div className="profile-grid">
        <div className="profile-item">
          <span>Имя</span>
          <strong>{currentUser.first_name || 'Не указано'}</strong>
        </div>
        <div className="profile-item">
          <span>Email</span>
          <strong>{currentUser.email}</strong>
        </div>
        <div className="profile-item">
          <span>Дата регистрации</span>
          <strong>{new Date(currentUser.registered_at).toLocaleString('ru-RU')}</strong>
        </div>
      </div>

      <button className="primary-button" type="button" onClick={onLogout}>
          Выйти
      </button>
    </section>

  )
}
export default ProfilePanel
