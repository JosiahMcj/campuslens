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
    icons: { src: string; sizes: string; type: string; purpose: string }[]
  }

  it('names the app and starts standalone at /', () => {
    expect(manifest.name).toBe('Golden Eagle AI Cabinet')
    expect(manifest.short_name).toBe('Cabinet')
    expect(manifest.start_url).toBe('/')
    expect(manifest.display).toBe('standalone')
  })

  it('takes its colours from the light theme (the default)', () => {
    // --paper in the light token block: the window and splash match the app.
    expect(manifest.theme_color.toUpperCase()).toBe('#FFFFFF')
    expect(manifest.background_color.toUpperCase()).toBe('#FFFFFF')
  })

  it('lists separate "any" and "maskable" icons, never one doing both', () => {
    const byPurpose = (purpose: string) =>
      manifest.icons
        .filter((icon) => icon.purpose === purpose)
        .map((icon) => icon.sizes)
        .sort()
    expect(byPurpose('any')).toEqual(['192x192', '512x512'])
    expect(byPurpose('maskable')).toEqual(['192x192', '512x512'])
    for (const icon of manifest.icons) {
      expect(icon.purpose.split(' ')).toHaveLength(1)
    }
  })

  it('points every icon at a PNG of the stated size on disk', () => {
    for (const icon of manifest.icons) {
      expect(icon.type).toBe('image/png')
      const size = pngSize(`../public${icon.src}`)
      expect(`${size.width}x${size.height}`).toBe(icon.sizes)
    }
  })
})

/** Reads a PNG's width and height from its header (and checks the magic bytes). */
function pngSize(path: string): { width: number; height: number } {
  const bytes = readFileSync(new URL(path, import.meta.url))
  // The PNG magic bytes: 0x89 'P' 'N' 'G'.
  expect(bytes[0]).toBe(0x89)
  expect(bytes.subarray(1, 4).toString('latin1')).toBe('PNG')
  return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) }
}

describe('the page head', () => {
  const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8')

  it('links the manifest and the light theme colour', () => {
    expect(html).toContain('rel="manifest"')
    expect(html).toContain('href="/manifest.webmanifest"')
    expect(html).toContain('name="theme-color" content="#FFFFFF"')
  })

  it('links a 180 px apple-touch-icon that exists on disk', () => {
    expect(html).toContain(
      '<link rel="apple-touch-icon" sizes="180x180" href="/icons/apple-touch-icon.png" />',
    )
    expect(pngSize('../public/icons/apple-touch-icon.png')).toEqual({ width: 180, height: 180 })
  })

  it('describes the app in plain words', () => {
    const description = /name="description"\s+content="([^"]+)"/.exec(html)?.[1] ?? ''
    expect(description.length).toBeGreaterThan(20)
    expect(description).not.toMatch(/governance|API|JSON/)
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
