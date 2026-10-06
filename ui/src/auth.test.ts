// Tests for the auth state machine: sign-in and its named errors, the
// CSRF token on every state-changing call, and the 401 path that signs the
// UI out and returns it to /login. Fetch is stubbed; no server is needed.

import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  LoginError,
  SessionEndedError,
  apiFetch,
  canAct,
  canEditAidQueue,
  canSeeAidQueue,
  canSeeAuditLog,
  clearSession,
  fetchMe,
  getSession,
  login,
  onSessionEnded,
  roleDisplayName,
  setSession,
  type Role,
  type Session,
} from './auth'

const SESSION: Session = {
  user: {
    id: 1,
    email: 'admin@example.edu',
    role: 'admin',
    institution_id: 1,
    institution: { slug: 'bootstrap', name: 'Bootstrap Institution' },
  },
  csrfToken: 'csrf-token-123',
}

function jsonResponse(status: number, body: unknown, headers?: HeadersInit): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

const loginBody = {
  user: {
    id: 1,
    email: 'admin@example.edu',
    role: 'admin',
    institution_id: 1,
    institution: { slug: 'bootstrap', name: 'Bootstrap Institution' },
  },
  csrf_token: 'csrf-token-123',
}

afterEach(() => {
  vi.unstubAllGlobals()
  clearSession()
  onSessionEnded(null)
})

describe('login — sign-in and its named errors', () => {
  it('stores the session and CSRF token from a successful sign-in', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, loginBody))
    vi.stubGlobal('fetch', fetchMock)

    const session = await login('admin@example.edu', 'correct-password')

    expect(fetchMock).toHaveBeenCalledWith('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: 'admin@example.edu', password: 'correct-password' }),
    })
    expect(session.user.email).toBe('admin@example.edu')
    expect(session.csrfToken).toBe('csrf-token-123')
    expect(getSession()).toEqual(session)
  })

  it('names a wrong email or password (which also covers a disabled account)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(401, { detail: 'invalid email or password' })),
    )

    const failure = await login('admin@example.edu', 'wrong').catch((error) => error)

    expect(failure).toBeInstanceOf(LoginError)
    expect(failure.message).toContain('email and password did not work')
    expect(failure.message).toContain('disabled')
    expect(getSession()).toBeNull()
  })

  it('names too many attempts, with the wait from Retry-After', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse(429, { detail: 'too many login attempts' }, { 'Retry-After': '900' }),
        ),
    )

    const failure = await login('admin@example.edu', 'wrong').catch((error) => error)

    expect(failure).toBeInstanceOf(LoginError)
    expect(failure.message).toContain('Too many sign in attempts')
    expect(failure.message).toContain('15 minutes')
  })

  it('names an unreachable service', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network down')))

    const failure = await login('admin@example.edu', 'x').catch((error) => error)

    expect(failure).toBeInstanceOf(LoginError)
    expect(failure.message).toContain('could not be reached')
  })
})

describe('fetchMe — the session check on load', () => {
  it('maps a 401 to signed out (null), never an error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(401, { detail: 'authentication required' })),
    )

    expect(await fetchMe()).toBeNull()
    expect(getSession()).toBeNull()
  })

  it('restores the session and its CSRF token for a live session', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, loginBody)))

    const session = await fetchMe()

    expect(session?.user.role).toBe('admin')
    expect(getSession()?.csrfToken).toBe('csrf-token-123')
  })
})

describe('apiFetch — CSRF on state-changing calls', () => {
  it('sends X-CSRF-Token from the in-memory session on a POST', async () => {
    setSession(SESSION)
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, {}))
    vi.stubGlobal('fetch', fetchMock)

    await apiFetch('/decisions/approve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })

    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect((init.headers as Headers).get('X-CSRF-Token')).toBe('csrf-token-123')
  })

  it('sends no CSRF header before sign-in', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, {}))
    vi.stubGlobal('fetch', fetchMock)

    await apiFetch('/x', { method: 'POST', headers: { 'Content-Type': 'application/json' } })

    // Without a session the init passes through untouched, so the headers
    // are still the caller's plain object with nothing added.
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' })
  })

  it('passes a plain GET through untouched (one argument, no header)', async () => {
    setSession(SESSION)
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, {}))
    vi.stubGlobal('fetch', fetchMock)

    await apiFetch('/findings')

    expect(fetchMock).toHaveBeenCalledWith('/api/findings')
    expect(fetchMock.mock.calls[0]).toHaveLength(1)
  })
})

describe('apiFetch — a 401 anywhere ends the session', () => {
  it('clears the session, fires the listener, and throws SessionEndedError', async () => {
    setSession(SESSION)
    const listener = vi.fn()
    onSessionEnded(listener)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(401, { detail: 'authentication required' })),
    )

    const failure = await apiFetch('/findings').catch((error) => error)

    expect(failure).toBeInstanceOf(SessionEndedError)
    expect(failure.message).toBe('Your session ended. Sign in again.')
    expect(listener).toHaveBeenCalledTimes(1)
    expect(getSession()).toBeNull()
  })

  it('fires on a 401 from a state-changing call too', async () => {
    setSession(SESSION)
    const listener = vi.fn()
    onSessionEnded(listener)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(401, { detail: 'x' })))

    const failure = await apiFetch('/ask', { method: 'POST' }).catch((error) => error)

    expect(failure).toBeInstanceOf(SessionEndedError)
    expect(listener).toHaveBeenCalledTimes(1)
  })
})

describe('canSeeAuditLog — the audit-log gate for every role', () => {
  it('shows the audit log to admin, reviewer, and executive, never staff or aid', () => {
    // The demo signs in as the executive (Beat 6 is the audit walkthrough),
    // so the executive must see the log the API already lets it read.
    const expected: Record<Role, boolean> = {
      admin: true,
      reviewer: true,
      executive: true,
      staff: false,
      aid: false,
    }
    for (const role of Object.keys(expected) as Role[]) {
      expect(canSeeAuditLog(role)).toBe(expected[role])
    }
  })
})

describe('the Financial Aid review queue gates, matching the API table', () => {
  it('reads for aid, admin, executive, reviewer; edits for aid and admin only', () => {
    const expected: Record<Role, { read: boolean; edit: boolean; act: boolean }> = {
      aid: { read: true, edit: true, act: false },
      admin: { read: true, edit: true, act: true },
      executive: { read: true, edit: false, act: true },
      reviewer: { read: true, edit: false, act: false },
      staff: { read: false, edit: false, act: false },
    }
    for (const role of Object.keys(expected) as Role[]) {
      expect(canSeeAidQueue(role)).toBe(expected[role].read)
      expect(canEditAidQueue(role)).toBe(expected[role].edit)
      expect(canAct(role)).toBe(expected[role].act)
    }
    expect(roleDisplayName('aid')).toBe('Financial Aid')
  })
})
