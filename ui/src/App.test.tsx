// @vitest-environment jsdom

// The application shell against a mocked API: friendly error screens, the
// 429 retry, a failed ask that leaves no orphan row, load errors that never
// look like "nothing here", unknown routes, and page titles.

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'
import { resetBriefingOnce } from './api'
import { clearSession } from './auth'
import { BUSY_MESSAGE, NETWORK_MESSAGE } from './errors'

const SESSION = {
  user: {
    id: 2,
    email: 'president@demo.test',
    role: 'executive',
    institution_id: 1,
    institution: { slug: 'bootstrap', name: 'Demonstration University' },
  },
  csrf_token: 'token',
}

const FINDINGS = {
  meta: {
    as_of: null,
    fixture: 'demo',
    terms: {},
    fictional: true,
    dataset: { id: 1, name: 'Demonstration (fictional)', sha256: 'x' },
  },
}

const QUESTION = 'What should I know about spring registration?'

type Handler = (url: string, init?: RequestInit) => Promise<Response> | Response

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

/** The API: every route answers like a healthy server unless overridden. */
function mockApi(overrides: Record<string, Handler> = {}) {
  const calls: string[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push(url)
    const path = url.replace(/^\/api/, '').split('?')[0]
    if (overrides[path] !== undefined) return overrides[path](url, init)
    switch (path) {
      case '/auth/me':
        return json(SESSION)
      case '/findings':
        return json(FINDINGS)
      case '/questions':
        return json([{ id: 'spring-registration', text: QUESTION }])
      case '/decisions':
        return json({ question_id: null, decisions: [] })
      case '/events':
        return json({ events: [] })
      case '/briefing':
        return json({ detail: 'none' }, 404)
      default:
        return json({ detail: 'not found' }, 404)
    }
  })
  vi.stubGlobal('fetch', fetchMock)
  return calls
}

beforeEach(() => {
  clearSession()
  resetBriefingOnce()
  window.history.replaceState(null, '', '/')
  Element.prototype.scrollIntoView = vi.fn()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('the session check', () => {
  it('shows a plain sentence and Try again when the Cabinet cannot be reached', async () => {
    mockApi({
      '/auth/me': () => {
        throw new TypeError('Failed to fetch')
      },
    })
    render(<App />)
    expect(await screen.findByText(NETWORK_MESSAGE)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/make api|8910|Failed to fetch|HTTP/)
  })

  it('says the Cabinet is busy on 429 and checks again after Retry-After', async () => {
    let answers = 0
    const calls = mockApi({
      '/auth/me': () => {
        answers += 1
        return answers === 1
          ? json({ detail: 'rate limit exceeded' }, 429, { 'Retry-After': '1' })
          : json(SESSION)
      },
    })
    render(<App />)
    expect(await screen.findByText(BUSY_MESSAGE)).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/429|HTTP/)
    await waitFor(() => expect(calls.filter((c) => c.endsWith('/auth/me')).length).toBe(2), {
      timeout: 2500,
    })
    expect(await screen.findByText('What should the Cabinet look into?')).toBeTruthy()
  })
})

describe('the conversation', () => {
  it('drops a failed ask, says why in plain words, and offers Ask again', async () => {
    let asks = 0
    mockApi({
      '/ask': () => {
        asks += 1
        throw new TypeError('Failed to fetch')
      },
    })
    render(<App />)
    const input = (await screen.findByLabelText(
      'Ask the Cabinet an approved question',
    )) as HTMLInputElement
    fireEvent.change(input, { target: { value: QUESTION } })
    fireEvent.submit(input.closest('form') as HTMLFormElement)
    expect(await screen.findByText(NETWORK_MESSAGE)).toBeTruthy()
    // No orphan exchange is left in the thread.
    expect(document.querySelectorAll('.exchange').length).toBe(0)
    expect(document.body.textContent).not.toContain('Failed to fetch')
    fireEvent.click(screen.getByRole('button', { name: 'Ask again' }))
    await waitFor(() => expect(asks).toBe(2))
  })

  it('has no "Or open" chip row and no "Test a refusal" in the navigation', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('What should the Cabinet look into?')
    expect(document.body.textContent).not.toContain('Or open')
    expect(document.body.textContent).not.toContain('Test a refusal')
    // One fictional-data mark, in the top bar.
    expect(screen.getAllByText('Fictional data').length).toBe(1)
  })

  it('shows a failed audit log load as an error with Retry, never Loading forever', async () => {
    let fail = true
    mockApi({
      '/events': () => (fail ? json({ detail: 'boom' }, 500) : json({ events: [] })),
    })
    render(<App />)
    await screen.findByText('What should the Cabinet look into?')
    fireEvent.click(screen.getByRole('button', { name: 'Audit log' }))
    expect(await screen.findByText(/Couldn't load the audit log/)).toBeTruthy()
    fail = false
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.queryByText(/Couldn't load the audit log/)).toBeNull())
  })

  it('shows a failed decision load as Retry instead of the decision card', async () => {
    mockApi({ '/decisions': () => json({ detail: 'boom' }, 503) })
    render(<App />)
    await screen.findByText('What should the Cabinet look into?')
    fireEvent.click(screen.getByRole('button', { name: 'Decision' }))
    expect(await screen.findByText(/Couldn't load the decision/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  })
})

describe('routes and titles', () => {
  it('sends an unknown address to the conversation and names the screen', async () => {
    window.history.replaceState(null, '', '/nowhere')
    mockApi()
    render(<App />)
    await screen.findByText('What should the Cabinet look into?')
    expect(window.location.pathname).toBe('/')
    expect(document.title).toBe('Briefing · Golden Eagle AI Cabinet')
  })

  it('titles the sign-in screen and keeps the address at /login', async () => {
    mockApi({ '/auth/me': () => json({ detail: 'signed out' }, 401) })
    render(<App />)
    await waitFor(() => expect(document.title).toBe('Sign in · Golden Eagle AI Cabinet'))
    expect(window.location.pathname).toBe('/login')
  })

  it('names the open panel in the title and closes it on Escape', async () => {
    mockApi()
    render(<App />)
    await screen.findByText('What should the Cabinet look into?')
    fireEvent.click(screen.getByRole('button', { name: 'Key figures' }))
    await waitFor(() => expect(document.title).toBe('Key figures · Golden Eagle AI Cabinet'))
    const dialog = screen.getByRole('dialog', { name: 'Key figures' })
    act(() => {
      fireEvent.keyDown(dialog, { key: 'Escape' })
    })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Key figures' })).toBeNull())
    expect(document.title).toBe('Briefing · Golden Eagle AI Cabinet')
  })
})
