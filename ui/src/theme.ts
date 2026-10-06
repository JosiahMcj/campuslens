import { useSyncExternalStore } from 'react'

/**
 * Display preferences, set from the sidebar's theme switch and the Settings
 * panel: the theme (light or dark), the text size, and motion. Each maps to a
 * class on <html> that the stylesheets read (`dark`, `text-large`,
 * `reduce-motion`). They are per-browser conveniences kept in localStorage;
 * every access is guarded because storage can be blocked (private windows,
 * strict settings), and the app works without it.
 *
 * Light is the default for everyone: the demo is projected in a lit room, so
 * the operating system's dark setting does not switch the theme on its own.
 * Dark applies only when someone chooses it here.
 */

export type Theme = 'light' | 'dark'
/**
 * What callers may pass when setting the theme. 'system' is accepted only so
 * an older caller keeps compiling; it is no longer an option and is stored as
 * 'light'.
 */
export type ThemeChoice = Theme | 'system'
export type TextSize = 'standard' | 'large'
export type Motion = 'full' | 'reduced'

export interface Prefs {
  theme: Theme
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
const DEFAULTS: Prefs = { theme: 'light', textSize: 'standard', motion: 'full' }

/** Anything but an explicit 'dark' (including an old stored 'system') is light. */
function toTheme(value: unknown): Theme {
  return value === 'dark' ? 'dark' : 'light'
}

function readStored(): Prefs {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (raw !== null) {
      const parsed = JSON.parse(raw) as Partial<Record<keyof Prefs, unknown>> | null
      if (parsed !== null && typeof parsed === 'object') {
        return {
          theme: toTheme(parsed.theme),
          textSize: parsed.textSize === 'large' ? 'large' : 'standard',
          motion: parsed.motion === 'reduced' ? 'reduced' : 'full',
        }
      }
      return DEFAULTS
    }
    const legacy = window.localStorage.getItem(LEGACY_THEME_KEY)
    if (legacy !== null) return { ...DEFAULTS, theme: toTheme(legacy) }
  } catch {
    // Storage blocked or unreadable: the defaults apply.
  }
  return DEFAULTS
}

let prefs: Prefs = typeof window === 'undefined' ? DEFAULTS : readStored()
const listeners = new Set<() => void>()

/** The theme shown. Kept as a function so callers need not know the rule. */
export function effectiveTheme(choice: ThemeChoice = prefs.theme): Theme {
  return toTheme(choice)
}

export function applyPrefs(): void {
  const root = document.documentElement.classList
  root.toggle('dark', prefs.theme === 'dark')
  root.toggle('text-large', prefs.textSize === 'large')
  root.toggle('reduce-motion', prefs.motion === 'reduced')
}

export function getPrefs(): Prefs {
  return prefs
}

export function setPrefs(next: PrefsUpdate): void {
  prefs = {
    theme: next.theme === undefined ? prefs.theme : toTheme(next.theme),
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
  return prefs.theme
}

export function setTheme(theme: Theme): void {
  setPrefs({ theme })
}

export function applyTheme(): void {
  applyPrefs()
}
