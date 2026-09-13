import { useEffect, useMemo, useState } from 'react'
import { ThemeContext } from './themeContext.js'

const STORAGE_KEY = 'smart-energy-theme'
function getInitialTheme() {
  const storedTheme = window.localStorage.getItem(STORAGE_KEY)
  if (storedTheme === 'light' || storedTheme === 'dark') return storedTheme
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(getInitialTheme)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    document.documentElement.style.colorScheme = theme
    document.querySelector('meta[name="theme-color"]')?.setAttribute(
      'content',
      theme === 'dark' ? '#06162f' : '#f3f7fb',
    )
  }, [theme])

  useEffect(() => {
    if (window.localStorage.getItem(STORAGE_KEY)) return undefined
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const handleChange = (event) => {
      if (!window.localStorage.getItem(STORAGE_KEY)) {
        setTheme(event.matches ? 'dark' : 'light')
      }
    }
    media.addEventListener('change', handleChange)
    return () => media.removeEventListener('change', handleChange)
  }, [])

  const value = useMemo(
    () => ({
      theme,
      toggleTheme: () => setTheme((current) => {
        const next = current === 'dark' ? 'light' : 'dark'
        window.localStorage.setItem(STORAGE_KEY, next)
        return next
      }),
    }),
    [theme],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}
