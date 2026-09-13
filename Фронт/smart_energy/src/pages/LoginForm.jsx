function FieldIcon({ type }) {
  if (type === 'email') {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M4 7.5A2.5 2.5 0 0 1 6.5 5h11A2.5 2.5 0 0 1 20 7.5v9a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 16.5z" />
        <path d="m5.5 7 6.5 5 6.5-5" />
      </svg>
    )
  }

  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M8 10V8a4 4 0 1 1 8 0v2" />
      <rect x="5" y="10" width="14" height="10" rx="2.5" />
    </svg>
  )
}

function PasswordToggle({ visible, onClick }) {
  return (
    <button
      className="field-action"
      type="button"
      onClick={onClick}
      aria-label={visible ? 'Скрыть пароль' : 'Показать пароль'}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" />
        <circle cx="12" cy="12" r="3" />
      </svg>
    </button>
  )
}

function AuthField({
  label,
  type,
  placeholder,
  value,
  onChange,
  autoComplete,
  action,
}) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <span className="field-control">
        <span className="field-icon">
          <FieldIcon type={type} />
        </span>
        <input
          type={type}
          placeholder={placeholder}
          value={value}
          onChange={onChange}
          autoComplete={autoComplete}
          required
        />
        {action}
      </span>
    </label>
  )
}

function LoginForm({
  busy,
  email,
  password,
  remember,
  passwordVisible,
  onEmailChange,
  onPasswordChange,
  onRememberChange,
  onTogglePassword,
  onSubmit,
  onSwitchMode,
}) {
  return (
    <form className="auth-form" onSubmit={onSubmit}>
      <AuthField
        label="Электронная почта"
        type="email"
        placeholder="name@example.com"
        value={email}
        onChange={onEmailChange}
        autoComplete="email"
      />

      <AuthField
        label="Пароль"
        type={passwordVisible ? 'text' : 'password'}
        placeholder="••••••••"
        value={password}
        onChange={onPasswordChange}
        autoComplete="current-password"
        action={
          <PasswordToggle visible={passwordVisible} onClick={onTogglePassword} />
        }
      />

      <div className="auth-row">
        <label className="check-control">
          <input type="checkbox" checked={remember} onChange={onRememberChange} />
          <span>Запомнить меня</span>
        </label>
        <button type="button" className="text-link muted-link">
          Забыли пароль?
        </button>
      </div>

      <button className="primary-button" type="submit" disabled={busy}>
        {busy ? 'Подождите...' : 'Войти'}
      </button>

      <div className="divider" aria-hidden="true">
        <span>или</span>
      </div>

      <p className="switch-copy">
        Нет аккаунта?{' '}
        <button type="button" className="text-link" onClick={onSwitchMode}>
          Зарегистрироваться
        </button>
      </p>
    </form>
  )
}

export default LoginForm
