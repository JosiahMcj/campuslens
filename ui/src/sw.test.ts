import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it, vi } from 'vitest'

// The service worker (public/sw.js) is not a module — it runs against the
// worker globals. These tests evaluate the real source with mocked
// self/caches/fetch and drive the registered fetch handler, so the
// navigation-caching logic itself is under test, not a copy of it.

type FakeResponse = {
  ok: boolean
  headers: Headers
  clone: () => FakeResponse
}

function fakeResponse(ok: boolean, contentType: string): FakeResponse {
  const response: FakeResponse = {
    ok,
    headers: new Headers({ 'content-type': contentType }),
    clone: () => response,
  }
  return response
}

function loadWorker(
  fetchImpl: (request: unknown) => Promise<unknown>,
  existingCaches: string[] = [],
) {
  const handlers: Record<string, (event: never) => void> = {}
  const put = vi.fn().mockResolvedValue(undefined)
  const deleteCache = vi.fn().mockResolvedValue(true)
  const cache = { put, match: vi.fn().mockResolvedValue(undefined) }
  const caches = {
    open: vi.fn().mockResolvedValue(cache),
    match: vi.fn().mockResolvedValue(undefined),
    keys: vi.fn().mockResolvedValue(existingCaches),
    delete: deleteCache,
  }
  const self = {
    location: { origin: 'https://cabinet.test' },
    addEventListener: (type: string, handler: (event: never) => void) => {
      handlers[type] = handler
    },
    skipWaiting: vi.fn(),
    clients: { claim: vi.fn() },
  }
  const source = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), '../public/sw.js'),
    'utf-8',
  )
  const factory = new Function('self', 'caches', 'fetch', 'Response', source)
  factory(self, caches, fetchImpl, Response)
  return { handlers, put, deleteCache }
}

function navigationEvent() {
  let responded: Promise<unknown> | undefined
  const event = {
    request: {
      url: 'https://cabinet.test/some/page',
      method: 'GET',
      mode: 'navigate',
    },
    respondWith: (promise: Promise<unknown>) => {
      responded = promise
    },
  }
  return { event, responded: () => responded }
}

function getEvent(path: string) {
  let responded: Promise<unknown> | undefined
  const request = {
    url: `https://cabinet.test${path}`,
    method: 'GET',
    mode: 'cors',
  }
  const event = {
    request,
    respondWith: (promise: Promise<unknown>) => {
      responded = promise
    },
  }
  return { event, request, responded: () => responded }
}

describe('service worker navigation caching', () => {
  it('caches a good HTML navigation response as the shell', async () => {
    const page = fakeResponse(true, 'text/html; charset=utf-8')
    const { handlers, put } = loadWorker(() => Promise.resolve(page))
    const nav = navigationEvent()
    handlers.fetch(nav.event as never)
    await nav.responded()
    expect(put).toHaveBeenCalledWith('/', page)
  })

  it('never caches a non-ok navigation response as the shell', async () => {
    // A 500/503 page cached as '/' would replace the app shell for every
    // later offline navigation — the bug this guards.
    const errorPage = fakeResponse(false, 'text/html')
    const { handlers, put } = loadWorker(() => Promise.resolve(errorPage))
    const nav = navigationEvent()
    handlers.fetch(nav.event as never)
    const returned = await nav.responded()
    expect(put).not.toHaveBeenCalled()
    expect(returned).toBe(errorPage)
  })

  it('never caches a non-HTML navigation response as the shell', async () => {
    const notHtml = fakeResponse(true, 'application/json')
    const { handlers, put } = loadWorker(() => Promise.resolve(notHtml))
    const nav = navigationEvent()
    handlers.fetch(nav.event as never)
    await nav.responded()
    expect(put).not.toHaveBeenCalled()
  })
})

describe('service worker cache allow-list', () => {
  // The production server serves the API unprefixed, so a GET /auth/me or
  // /events must never be cache-stored — a cached /auth/me kept serving a
  // signed-out user's object and CSRF token (the audit finding).
  it.each(['/auth/me', '/events'])(
    'never caches a GET to %s and never intercepts it',
    async (path) => {
      const apiResponse = fakeResponse(true, 'application/json')
      const fetchSpy = vi.fn(() => Promise.resolve(apiResponse))
      const { handlers, put } = loadWorker(fetchSpy)
      const req = getEvent(path)
      handlers.fetch(req.event as never)
      // No respondWith: the browser takes the request to the network
      // untouched, so the worker can never store the response.
      expect(req.responded()).toBeUndefined()
      expect(put).not.toHaveBeenCalled()
      expect(fetchSpy).not.toHaveBeenCalled()
    },
  )

  it('caches a hashed build asset under /assets/', async () => {
    const asset = fakeResponse(true, 'text/javascript')
    const { handlers, put } = loadWorker(() => Promise.resolve(asset))
    const req = getEvent('/assets/index-abc123.js')
    handlers.fetch(req.event as never)
    await req.responded()
    expect(put).toHaveBeenCalledWith(req.request, asset)
  })

  it('never caches a non-allow-listed path', async () => {
    const data = fakeResponse(true, 'application/json')
    const { handlers, put } = loadWorker(() => Promise.resolve(data))
    const req = getEvent('/findings')
    handlers.fetch(req.event as never)
    expect(req.responded()).toBeUndefined()
    expect(put).not.toHaveBeenCalled()
  })

  it('drops the old shell cache on activate', async () => {
    const { handlers, deleteCache } = loadWorker(
      () => Promise.resolve(fakeResponse(true, 'text/html')),
      ['cabinet-shell-v1', 'cabinet-shell-v2', 'cabinet-shell-v3', 'cabinet-shell-v4'],
    )
    let waited: Promise<unknown> | undefined
    handlers.activate({
      waitUntil: (promise: Promise<unknown>) => {
        waited = promise
      },
    } as never)
    await waited
    expect(deleteCache).toHaveBeenCalledWith('cabinet-shell-v1')
    expect(deleteCache).toHaveBeenCalledWith('cabinet-shell-v2')
    expect(deleteCache).toHaveBeenCalledWith('cabinet-shell-v3')
    expect(deleteCache).not.toHaveBeenCalledWith('cabinet-shell-v4')
  })
})
