import { useEffect, useState } from 'react'
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router'
import { login, register } from '../features/auth/api/authApi.js'
import { apiRequest, refreshAccessToken } from '../features/auth/api/authClient.js'
import LoginForm from './LoginForm.jsx'
import RegisterForm from './RegisterForm.jsx'
import {
  clearAccessToken,
  setAccessToken,
  subscribeAccessToken,
} from '../features/auth/model/authStore.js'
import { logout } from '../features/auth/api/authApi.js'
import { DASHBOARD_ROUTES, ROUTES } from '../app/routes.js'
import ThemeToggle from '../shared/theme/ThemeToggle.jsx'
import '../auth.css'

const initialLogin = {
  email: '',
  password: '',
  remember: false,
}

const initialRegister = {
  firstName: '',
  lastName: '',
  email: '',
  password: '',
  confirmPassword: '',
  acceptTerms: false,
}

function getRegisterErrorMessage(response, data) {
  if (response.status === 400 && Array.isArray(data.email) && data.email.length > 0) {
    return 'Пользователь с таким email уже существует'
  }

  const firstError = Object.values(data)[0]

  return Array.isArray(firstError)
    ? firstError[0]
    : firstError ?? 'Не удалось зарегистрироваться'
}

function AuthPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const searchParams = new URLSearchParams(location.search)
  const showDashboardPreview =
    searchParams.get('dashboard') === '1' || searchParams.get('preview') === '1'
  const [mode, setMode] = useState(location.pathname === ROUTES.register ? 'register' : 'login')
  const [loginForm, setLoginForm] = useState(initialLogin)
  const [registerForm, setRegisterForm] = useState(initialRegister)
  const [loginPasswordVisible, setLoginPasswordVisible] = useState(false)
  const [registerPasswordVisible, setRegisterPasswordVisible] = useState(false)
  const [registerConfirmVisible, setRegisterConfirmVisible] = useState(false)
  const [currentUser, setCurrentUser] = useState(null)
  const [status, setStatus] = useState('Проверяем сохраненную сессию...')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(true)

  async function handleLogout() {
    try {
      await logout()
    } catch (e) {
      console.error('Ошибка logout', e)
    } finally {
      clearAccessToken()
      setCurrentUser(null)
      navigate(ROUTES.login, { replace: true })
    }
  }

  useEffect(() => {
    let active = true

    async function bootstrapSession() {
      try {
        await refreshAccessToken()
        const response = await apiRequest('/me/')

        if (!response.ok) {
          throw new Error('Сессия не найдена')
        }

        const user = await response.json()

        if (active) {
          setCurrentUser(user)
          setStatus('Сессия восстановлена')
        }
      } catch {
        clearAccessToken()
        if (active) {
          setStatus('Войдите, чтобы продолжить')
        }
      } finally {
        if (active) {
          setBusy(false)
        }
      }
    }

    bootstrapSession()

    return () => {
      active = false
    }
  }, [])

  useEffect(
    () => subscribeAccessToken((token) => {
      if (!token) {
        setCurrentUser(null)
      }
    }),
    [],
  )

  useEffect(() => {
    if (location.pathname === ROUTES.login) setMode('login')
    if (location.pathname === ROUTES.register) setMode('register')
  }, [location.pathname])

  useEffect(() => {
    if (busy) return

    const isAuthRoute = location.pathname === ROUTES.login || location.pathname === ROUTES.register
    const isDashboardRoute = Boolean(DASHBOARD_ROUTES[location.pathname])

    if (showDashboardPreview) {
      if (location.pathname !== ROUTES.root && !isDashboardRoute) {
        navigate(`${ROUTES.dashboard}?preview=1`, { replace: true })
      }
      return
    }

    if (currentUser && !isDashboardRoute) {
      navigate(ROUTES.dashboard, { replace: true })
    } else if (!currentUser && !isAuthRoute) {
      navigate(ROUTES.login, { replace: true })
    }
  }, [busy, currentUser, location.pathname, navigate, showDashboardPreview])

  function updateLoginField(field, value) {
    setLoginForm((current) => ({ ...current, [field]: value }))
  }

  function updateRegisterField(field, value) {
    setRegisterForm((current) => ({ ...current, [field]: value }))
  }

  async function loadCurrentUser(nextStatus = 'Успешный вход') {
    const response = await apiRequest('/me/')

    if (!response.ok) {
      throw new Error('Не удалось получить профиль')
    }

    const user = await response.json()
    setCurrentUser(user)
    setStatus(nextStatus)
  }

  async function handleLogin(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    setStatus('Выполняем вход...')

    try {
      const response = await login({
        email: loginForm.email.trim(),
        password: loginForm.password,
      })
      const data = await response.json().catch(() => ({}))

      if (!response.ok) {
        throw new Error(data.detail ?? 'Не удалось войти')
      }

      setAccessToken(data.access)
      await loadCurrentUser('Вы вошли в систему')
      setLoginForm(initialLogin)
      navigate(ROUTES.dashboard, { replace: true })
    } catch (requestError) {
      clearAccessToken()
      setCurrentUser(null)
      setError(requestError.message)
      setStatus('Ошибка входа')
    } finally {
      setBusy(false)
    }
  }

  async function handleRegister(event) {
    event.preventDefault()

    if (registerForm.password !== registerForm.confirmPassword) {
      setError('Пароли не совпадают')
      return
    }

    if (!registerForm.acceptTerms) {
      setError('Нужно принять условия использования')
      return
    }

    setBusy(true)
    setError('')
    setStatus('Создаем аккаунт...')

    try {
      const response = await register({
        email: registerForm.email.trim(),
        password: registerForm.password,
        first_name: registerForm.firstName.trim(),
        last_name: registerForm.lastName.trim(),
      })
      const data = await response.json().catch(() => ({}))

      if (!response.ok) {
        throw new Error(getRegisterErrorMessage(response, data))
      }

      const loginResponse = await login({
        email: registerForm.email.trim(),
        password: registerForm.password,
      })
      const loginData = await loginResponse.json().catch(() => ({}))

      if (!loginResponse.ok) {
        throw new Error(loginData.detail ?? 'Регистрация прошла, но вход не выполнен')
      }

      setAccessToken(loginData.access)
      await loadCurrentUser('Аккаунт создан')
      setRegisterForm(initialRegister)
      navigate(ROUTES.dashboard, { replace: true })
    } catch (requestError) {
      clearAccessToken()
      setCurrentUser(null)
      setError(requestError.message)
      setStatus('Ошибка регистрации')
    } finally {
      setBusy(false)
    }
  }

  function switchMode(nextMode) {
    setMode(nextMode)
    setError('')
    setStatus('Войдите, чтобы продолжить')
    navigate(nextMode === 'register' ? ROUTES.register : ROUTES.login)
  }

  const isAuthenticated = currentUser !== null

  if (showDashboardPreview && !DASHBOARD_ROUTES[location.pathname]) {
    return <Navigate to={`${ROUTES.dashboard}?preview=1`} replace />
  }

  if (isAuthenticated && !DASHBOARD_ROUTES[location.pathname]) {
    return <Navigate to={ROUTES.dashboard} replace />
  }

  if ((showDashboardPreview || isAuthenticated) && DASHBOARD_ROUTES[location.pathname]) {
    return (
      <Outlet
        context={{
          currentUser: showDashboardPreview
            ? { first_name: 'Demo', last_name: 'User', email: 'demo@example.com' }
            : currentUser,
          onLogout: showDashboardPreview
            ? () => navigate(ROUTES.login, { replace: true })
            : handleLogout,
          preview: showDashboardPreview,
        }}
      />
    )
  }

  return (
    <main className="auth-shell">
      <div className="auth-background auth-background-left" aria-hidden="true" />
      <div className="auth-background auth-background-right" aria-hidden="true" />

      <section className="auth-card">
        <div className="auth-theme-toggle"><ThemeToggle compact /></div>
        <>
          <header className="auth-header">
            <h1>{mode === 'login' ? 'Вход в систему' : 'Регистрация'}</h1>
            <p>
              {mode === 'login'
                ? 'Введите свои данные для входа'
                : 'Создайте новый аккаунт'}
            </p>
          </header>

          {mode === 'login' ? (
            <LoginForm
              busy={busy}
              email={loginForm.email}
              password={loginForm.password}
              remember={loginForm.remember}
              passwordVisible={loginPasswordVisible}
              onEmailChange={(event) => updateLoginField('email', event.target.value)}
              onPasswordChange={(event) => updateLoginField('password', event.target.value)}
              onRememberChange={(event) =>
                updateLoginField('remember', event.target.checked)
              }
              onTogglePassword={() =>
                setLoginPasswordVisible((current) => !current)
              }
              onSubmit={handleLogin}
              onSwitchMode={() => switchMode('register')}
            />
          ) : (
            <RegisterForm
              busy={busy}
              firstName={registerForm.firstName}
              lastName={registerForm.lastName}
              email={registerForm.email}
              password={registerForm.password}
              confirmPassword={registerForm.confirmPassword}
              acceptTerms={registerForm.acceptTerms}
              passwordVisible={registerPasswordVisible}
              confirmVisible={registerConfirmVisible}
              onFirstNameChange={(event) =>
                updateRegisterField('firstName', event.target.value)
              }
              onLastNameChange={(event) =>
                updateRegisterField('lastName', event.target.value)
              }
              onEmailChange={(event) => updateRegisterField('email', event.target.value)}
              onPasswordChange={(event) =>
                updateRegisterField('password', event.target.value)
              }
              onConfirmPasswordChange={(event) =>
                updateRegisterField('confirmPassword', event.target.value)
              }
              onAcceptTermsChange={(event) =>
                updateRegisterField('acceptTerms', event.target.checked)
              }
              onTogglePassword={() =>
                setRegisterPasswordVisible((current) => !current)
              }
              onToggleConfirmPassword={() =>
                setRegisterConfirmVisible((current) => !current)
              }
              onSubmit={handleRegister}
              onSwitchMode={() => switchMode('login')}
            />
          )}
        </>

        <footer className="auth-footer" aria-live="polite" aria-busy={busy}>
          <p>{status}</p>
          {error ? <p className="auth-error" role="alert">{error}</p> : null}
        </footer>
      </section>
    </main>
  )
}

export default AuthPage
