import { useSyncExternalStore } from 'react'

/**
 * Display preferences, set from the sidebar's theme switch and the Settings
 * panel: the theme (light or dark), the text size, and motion. Each maps to a
 * class on <html> that the stylesheets read (`dark`, `text-large`,
 * `reduce-motion`). They are per-browser conveniences kept in localStorage;
 * every access is guarded because storage can be blocked (private windows,
 * strict settings), and the app works without it.
 *
 * By default the theme follows the computer's own light or dark setting, and
 * changes with it. Choosing light or dark from the theme switch overrides
 * that for this browser.
 */

export type Theme = 'light' | 'dark'
/** A stored theme choice: light, dark, or 'system' (follow the computer). */
export type ThemeChoice = Theme | 'system'
export type TextSize = 'standard' | 'large'
export type Motion = 'full' | 'reduced'

export interface Prefs {
  theme: ThemeChoice
  textSize: TextSize
  motion: Motion
}

export interface PrefsUpdate {
  theme?: ThemeChoice
  textSize?: TextSize
  motion?: Motion
}

const STORAGE_KEY = 'cabinet-prefs'
const LEGACY_THEME_KEY = 'cabinet-theme'
const DEFAULTS: Prefs = { theme: 'system', textSize: 'standard', motion: 'full' }

/** An explicit 'light' or 'dark' is kept; anything else follows the computer. */
function toChoice(value: unknown): ThemeChoice {
  return value === 'dark' || value === 'light' ? value : 'system'
}

function systemTheme(): Theme {
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

function readStored(): Prefs {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (raw !== null) {
      const parsed = JSON.parse(raw) as Partial<Record<keyof Prefs, unknown>> | null
      if (parsed !== null && typeof parsed === 'object') {
        return {
          theme: toChoice(parsed.theme),
          textSize: parsed.textSize === 'large' ? 'large' : 'standard',
          motion: parsed.motion === 'reduced' ? 'reduced' : 'full',
        }
      }
      return DEFAULTS
    }
    const legacy = window.localStorage.getItem(LEGACY_THEME_KEY)
    if (legacy !== null) return { ...DEFAULTS, theme: toChoice(legacy) }
  } catch {
    // Storage blocked or unreadable: the defaults apply.
  }
  return DEFAULTS
}

let prefs: Prefs = typeof window === 'undefined' ? DEFAULTS : readStored()
const listeners = new Set<() => void>()

/** The theme shown. Kept as a function so callers need not know the rule. */
export function effectiveTheme(choice: ThemeChoice = prefs.theme): Theme {
  return choice === 'system' ? systemTheme() : choice
}

let watchingSystem = false

/** Re-apply when the computer switches between light and dark. */
function watchSystemTheme(): void {
  if (watchingSystem) return
  try {
    const query = window.matchMedia('(prefers-color-scheme: dark)')
    query.addEventListener('change', () => {
      if (prefs.theme !== 'system') return
      applyPrefs()
      listeners.forEach((listener) => listener())
    })
    watchingSystem = true
  } catch {
    // No media queries here: the stored or default theme stays as it is.
  }
}

export function applyPrefs(): void {
  watchSystemTheme()
  const root = document.documentElement.classList
  root.toggle('dark', effectiveTheme() === 'dark')
  root.toggle('text-large', prefs.textSize === 'large')
  root.toggle('reduce-motion', prefs.motion === 'reduced')
}

export function getPrefs(): Prefs {
  return prefs
}

export function setPrefs(next: PrefsUpdate): void {
  prefs = {
    theme: next.theme === undefined ? prefs.theme : toChoice(next.theme),
    textSize: next.textSize ?? prefs.textSize,
    motion: next.motion ?? prefs.motion,
  }
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
