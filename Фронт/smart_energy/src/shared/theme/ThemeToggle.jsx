import { useTheme } from './themeContext.js'

export default function ThemeToggle({ compact = false }) {
  const { theme, toggleTheme } = useTheme()
  const nextThemeLabel = theme === 'dark' ? 'светлую' : 'тёмную'

  return (
    <button
      className={compact ? 'theme-toggle theme-toggle--compact' : 'theme-toggle'}
      type="button"
      onClick={toggleTheme}
      aria-pressed={theme === 'dark'}
      aria-label={`Включить ${nextThemeLabel} тему`}
      title={`Включить ${nextThemeLabel} тему`}
    >
      <span aria-hidden="true">{theme === 'dark' ? '☀' : '☾'}</span>
      {!compact ? <span>{theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'}</span> : null}
    </button>
  )
}
