import { afterEach, describe, expect, it, vi } from 'vitest'

// theme.ts reads storage when it loads, so each case stubs a browser first and
// then imports a fresh copy of the module.

class FakeClassList {
  private names = new Set<string>()
  toggle(name: string, on: boolean): void {
    if (on) this.names.add(name)
    else this.names.delete(name)
  }
  contains(name: string): boolean {
    return this.names.has(name)
  }
}

function stubBrowser(stored: Record<string, string>, osDark = false) {
  const store = new Map(Object.entries(stored))
  const classList = new FakeClassList()
  vi.stubGlobal('window', {
    localStorage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
    },
    matchMedia: () => ({ matches: osDark, addEventListener: () => {} }),
  })
  vi.stubGlobal('document', { documentElement: { classList } })
  return { store, classList }
}

async function loadTheme() {
  vi.resetModules()
  return import('./theme')
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('display preferences', () => {
  it('follows a dark computer by default', async () => {
    const { classList } = stubBrowser({}, true)
    const theme = await loadTheme()
    expect(theme.getPrefs().theme).toBe('system')
    theme.applyPrefs()
    expect(classList.contains('dark')).toBe(true)
  })

  it('follows a light computer by default', async () => {
    const { classList } = stubBrowser({}, false)
    const theme = await loadTheme()
    theme.applyPrefs()
    expect(classList.contains('dark')).toBe(false)
  })

  it('keeps a stored "system" choice', async () => {
    stubBrowser({ 'cabinet-prefs': JSON.stringify({ theme: 'system', textSize: 'large' }) }, true)
    const theme = await loadTheme()
    expect(theme.getPrefs()).toEqual({ theme: 'system', textSize: 'large', motion: 'full' })
  })

  it('keeps an explicit light choice on a dark computer', async () => {
    const { classList } = stubBrowser({ 'cabinet-prefs': JSON.stringify({ theme: 'light' }) }, true)
    const theme = await loadTheme()
    theme.applyPrefs()
    expect(classList.contains('dark')).toBe(false)
  })

  it('keeps a stored dark choice and applies it', async () => {
    const { classList } = stubBrowser({ 'cabinet-prefs': JSON.stringify({ theme: 'dark' }) })
    const theme = await loadTheme()
    theme.applyPrefs()
    expect(classList.contains('dark')).toBe(true)
  })

  it('reads the legacy single-theme key', async () => {
    stubBrowser({ 'cabinet-theme': 'dark' })
    const theme = await loadTheme()
    expect(theme.getPrefs().theme).toBe('dark')
  })

  it('falls back to the defaults on unreadable storage', async () => {
    stubBrowser({ 'cabinet-prefs': 'not json' })
    const theme = await loadTheme()
    expect(theme.getPrefs().theme).toBe('system')
  })

  it('goes back to following the computer when "system" is chosen', async () => {
    const { store } = stubBrowser({ 'cabinet-prefs': JSON.stringify({ theme: 'dark' }) })
    const theme = await loadTheme()
    theme.setPrefs({ theme: 'system' })
    expect(theme.getPrefs().theme).toBe('system')
    expect(JSON.parse(store.get('cabinet-prefs') ?? '{}').theme).toBe('system')
  })

  it('remembers a dark choice and the other preferences', async () => {
    const { store, classList } = stubBrowser({})
    const theme = await loadTheme()
    theme.setPrefs({ theme: 'dark', motion: 'reduced' })
    expect(classList.contains('dark')).toBe(true)
    expect(classList.contains('reduce-motion')).toBe(true)
    expect(JSON.parse(store.get('cabinet-prefs') ?? '{}')).toEqual({
      theme: 'dark',
      textSize: 'standard',
      motion: 'reduced',
    })
  })
})
