/**
 * Light and dark theme, LibreChat style: a `dark` class on <html> switches
 * the colour tokens in theme-librechat.css. With no saved choice the theme
 * follows the system setting. The choice is a per-browser convenience kept
 * in localStorage; every access is guarded because storage can be blocked
 * (private windows, strict settings), and the app works without it.
 */

export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'cabinet-theme'

function savedTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY)
    return value === 'light' || value === 'dark' ? value : null
  } catch {
    return null
  }
}

function systemTheme(): Theme {
  return typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches
    ? 'dark'
    : 'light'
}

export function currentTheme(): Theme {
  return savedTheme() ?? systemTheme()
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle('dark', theme === 'dark')
}

export function setTheme(theme: Theme): void {
  applyTheme(theme)
  try {
    window.localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    // Storage blocked: the theme still applies for this page view.
  }
}
