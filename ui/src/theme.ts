import { useSyncExternalStore } from 'react'

/**
 * Display preferences, set from the sidebar's theme switch and the Settings
 * panel: the theme (light, dark, or following the system), the text size,
 * and motion. Each maps to a class on <html> that the stylesheets read
 * (`dark`, `text-large`, `reduce-motion`). They are per-browser conveniences
 * kept in localStorage; every access is guarded because storage can be
 * blocked (private windows, strict settings), and the app works without it.
 */

export type Theme = 'light' | 'dark'
export type ThemeChoice = Theme | 'system'
export type TextSize = 'standard' | 'large'
export type Motion = 'full' | 'reduced'

export interface Prefs {
  theme: ThemeChoice
  textSize: TextSize
  motion: Motion
}

const STORAGE_KEY = 'cabinet-prefs'
const LEGACY_THEME_KEY = 'cabinet-theme'
const DEFAULTS: Prefs = { theme: 'system', textSize: 'standard', motion: 'full' }

function readStored(): Prefs {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (raw !== null) {
      const parsed = JSON.parse(raw) as Partial<Prefs>
      return {
        theme: parsed.theme === 'light' || parsed.theme === 'dark' ? parsed.theme : 'system',
        textSize: parsed.textSize === 'large' ? 'large' : 'standard',
        motion: parsed.motion === 'reduced' ? 'reduced' : 'full',
      }
    }
    const legacy = window.localStorage.getItem(LEGACY_THEME_KEY)
    if (legacy === 'light' || legacy === 'dark') return { ...DEFAULTS, theme: legacy }
  } catch {
    // Storage blocked or unreadable: the defaults apply.
  }
  return DEFAULTS
}

let prefs: Prefs = typeof window === 'undefined' ? DEFAULTS : readStored()
const listeners = new Set<() => void>()

function systemTheme(): Theme {
  return typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches
    ? 'dark'
    : 'light'
}

/** The theme actually shown: the choice, or the system's when following it. */
export function effectiveTheme(choice: ThemeChoice = prefs.theme): Theme {
  return choice === 'system' ? systemTheme() : choice
}

export function applyPrefs(): void {
  const root = document.documentElement.classList
  root.toggle('dark', effectiveTheme() === 'dark')
  root.toggle('text-large', prefs.textSize === 'large')
  root.toggle('reduce-motion', prefs.motion === 'reduced')
}

export function getPrefs(): Prefs {
  return prefs
}

export function setPrefs(next: Partial<Prefs>): void {
  prefs = { ...prefs, ...next }
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs))
  } catch {
    // Storage blocked: the preference still applies for this page view.
  }
  applyPrefs()
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** The current preferences, re-rendering whenever any of them changes. */
export function usePrefs(): Prefs {
  return useSyncExternalStore(subscribe, getPrefs, getPrefs)
}

/** Follow the system theme live while the choice is "system". */
export function watchSystemTheme(): void {
  if (typeof window.matchMedia !== 'function') return
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (prefs.theme === 'system') {
      applyPrefs()
      listeners.forEach((listener) => listener())
    }
  })
}

// The original single-theme API, kept for the Institution page's masthead.
export function currentTheme(): Theme {
  return effectiveTheme()
}

export function setTheme(theme: Theme): void {
  setPrefs({ theme })
}

export function applyTheme(): void {
  applyPrefs()
}
