// Tests for the installable surface: the web-app manifest is served
// with the required fields, its icons exist on disk as PNGs, the page links
// the manifest and the theme color, and the service worker caches the shell
// while never caching API responses.

/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

function readPublic(name: string): string {
  return readFileSync(new URL(`../public/${name}`, import.meta.url), 'utf8')
}

describe('the web-app manifest', () => {
  const manifest = JSON.parse(readPublic('manifest.webmanifest')) as {
    name: string
    short_name: string
    start_url: string
    display: string
    theme_color: string
    background_color: string
    icons: { src: string; sizes: string; type: string }[]
  }

  it('names the app and starts standalone at /', () => {
    expect(manifest.name).toBe('Golden Eagle AI Cabinet')
    expect(manifest.short_name.length).toBeGreaterThan(0)
    expect(manifest.start_url).toBe('/')
    expect(manifest.display).toBe('standalone')
  })

  it('carries the navy theme color on the paper ground', () => {
    expect(manifest.theme_color.toLowerCase()).toBe('#1f3a5f')
    expect(manifest.background_color.toLowerCase()).toBe('#f7f6f2')
  })

  it('lists 192 and 512 PNG icons that exist on disk', () => {
    const sizes = manifest.icons.map((icon) => icon.sizes).sort()
    expect(sizes).toEqual(['192x192', '512x512'])
    for (const icon of manifest.icons) {
      expect(icon.type).toBe('image/png')
      const bytes = readFileSync(
        new URL(`../public${icon.src}`, import.meta.url),
      )
      // The PNG magic bytes: 0x89 'P' 'N' 'G'.
      expect(bytes[0]).toBe(0x89)
      expect(bytes.subarray(1, 4).toString('latin1')).toBe('PNG')
    }
  })
})

describe('the page head', () => {
  const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8')

  it('links the manifest and the navy theme color', () => {
    expect(html).toContain('rel="manifest"')
    expect(html).toContain('href="/manifest.webmanifest"')
    expect(html).toContain('name="theme-color" content="#1F3A5F"')
  })
})

describe('the service worker', () => {
  const sw = readPublic('sw.js')

  it('caches the shell and explicitly never caches API responses', () => {
    expect(sw).toContain('caches.open')
    expect(sw).toContain('/manifest.webmanifest')
    // The cache is an allow-list (shell, /assets/, /icons/): the API, which
    // the production server also serves unprefixed, is never intercepted.
    expect(sw).toContain('cacheableStaticPath')
    expect(sw).toContain("'/assets/'")
    expect(sw).toContain('NEVER stored')
    expect(sw).toContain('cabinet-shell-v2')
  })
})
