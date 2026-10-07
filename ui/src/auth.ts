// Sign-in and session logic. The session cookie is HttpOnly, so the UI
// knows the session only through POST /api/auth/login and GET /api/auth/me.
// The session's CSRF token lives in memory here, never in storage: every
// state-changing request sends it back as X-CSRF-Token, and a 401 anywhere
// ends the session in the UI and returns the app to the sign-in screen.

export type Role =
  | 'admin'
  | 'executive'
  | 'staff'
  | 'reviewer'
  | 'aid'
  | 'finance'
  | 'registrar'
  | 'studentlife'
  | 'it'

/** Every role, in the order the account lists show them. */
export const ALL_ROLES: readonly Role[] = [
  'executive',
  'admin',
  'it',
  'finance',
  'aid',
  'registrar',
  'studentlife',
  'staff',
  'reviewer',
]

/** The department accounts (docs/ROLES.md): each has its own overview. */
export type Department = 'finance' | 'registrar' | 'studentlife'
export const DEPARTMENTS: readonly Department[] = ['finance', 'registrar', 'studentlife']

export function isDepartment(role: Role): role is Department {
  return role === 'finance' || role === 'registrar' || role === 'studentlife'
}

/** The accounts IT may create, enable, disable and re-role. Matches
 * IT_MANAGED_ROLES in the API. */
export const IT_MANAGED_ROLES: readonly Role[] = ['finance', 'registrar', 'studentlife', 'staff']

export interface SessionUser {
  id: number
  email: string
  role: Role
  institution_id: number
  institution: { slug: string; name: string } | null
}

export interface Session {
  user: SessionUser
  csrfToken: string
}

let session: Session | null = null
let sessionEndedListener: (() => void) | null = null

export function getSession(): Session | null {
  return session
}

export function setSession(next: Session): void {
  session = next
}

export function clearSession(): void {
  session = null
}

/** The one callback fired when any API call answers 401 (the App signs out). */
export function onSessionEnded(listener: (() => void) | null): void {
  sessionEndedListener = listener
}

/** A 401 from any API call: the session expired, was disabled, or ended. */
export class SessionEndedError extends Error {
  constructor() {
    super('Your session ended. Sign in again.')
    this.name = 'SessionEndedError'
  }
}

/** A non-401 API failure, carrying the response's detail sentence when it has one. */
export class ApiError extends Error {
  status: number
  /** Seconds from the response's Retry-After header (429s), when it had one. */
  retryAfter: number | undefined

  constructor(status: number, message: string, retryAfter?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.retryAfter = retryAfter
  }
}

/** The Retry-After header in seconds, when it is a plain number. */
export function retryAfterFrom(response: Response): number | undefined {
  const raw = response.headers?.get?.('Retry-After')
  if (raw === null || raw === undefined) return undefined
  const seconds = Number.parseInt(raw, 10)
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : undefined
}

/** A sign-in failure with a sentence that names the problem. */
export class LoginError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'LoginError'
  }
}

/** The detail sentence from an API error body, or the fallback. */
export async function apiDetail(response: Response, fallback: string): Promise<string> {
  try {
    const body: unknown = await response.json()
    if (
      typeof body === 'object' &&
      body !== null &&
      typeof (body as Record<string, unknown>).detail === 'string'
    ) {
      return (body as Record<string, string>).detail
    }
  } catch {
    // Not a JSON body; the fallback stands.
  }
  return fallback
}

function parseSession(body: unknown): Session | null {
  if (typeof body !== 'object' || body === null) return null
  const record = body as Record<string, unknown>
  const user = record.user
  if (typeof user !== 'object' || user === null) return null
  const u = user as Record<string, unknown>
  if (
    typeof u.email !== 'string' ||
    typeof u.role !== 'string' ||
    typeof record.csrf_token !== 'string'
  ) {
    return null
  }
  const institution =
    typeof u.institution === 'object' && u.institution !== null
      ? (u.institution as Record<string, unknown>)
      : null
  return {
    user: {
      id: typeof u.id === 'number' ? u.id : 0,
      email: u.email,
      role: u.role as Role,
      institution_id: typeof u.institution_id === 'number' ? u.institution_id : 0,
      institution:
        institution !== null &&
        typeof institution.slug === 'string' &&
        typeof institution.name === 'string'
          ? { slug: institution.slug, name: institution.name }
          : null,
    },
    csrfToken: record.csrf_token,
  }
}

/**
 * The one fetch wrapper for the API. It attaches the session's CSRF token to
 * every state-changing method and turns any 401 into a SessionEndedError
 * (after firing the session-ended listener, which signs the UI out). When
 * there is no session, or the call is a GET, the init passes through
 * untouched so existing callers and tests keep their exact request shape.
 */
export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  let finalInit = init
  const method = (init?.method ?? 'GET').toUpperCase()
  if (session !== null && init !== undefined && method !== 'GET' && method !== 'HEAD') {
    const headers = new Headers(init.headers)
    headers.set('X-CSRF-Token', session.csrfToken)
    finalInit = { ...init, headers }
  }
  const response =
    finalInit === undefined
      ? await fetch(`/api${path}`)
      : await fetch(`/api${path}`, finalInit)
  if (response.status === 401 && path !== '/auth/login') {
    clearSession()
    sessionEndedListener?.()
    throw new SessionEndedError()
  }
  return response
}

/**
 * POST /api/auth/login. The API's 401 is deliberately generic (wrong email,
 * wrong password, and a disabled account all look the same, so accounts
 * cannot be enumerated), and five failures per fifteen minutes lock the
 * route out with 429; the messages below name each problem the API can
 * honestly report.
 */
export async function login(email: string, password: string): Promise<Session> {
  let response: Response
  try {
    response = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    })
  } catch {
    throw new LoginError(
      "We couldn't reach CampusLens. Check your connection and try again.",
    )
  }
  if (response.status === 401) {
    throw new LoginError(
      'That email and password did not work. Check both and try again. ' +
        'If this account was disabled, an administrator has to enable it.',
    )
  }
  if (response.status === 429) {
    const retryAfter = Number(response.headers.get('Retry-After'))
    const minutes = Number.isFinite(retryAfter) && retryAfter > 0 ? Math.ceil(retryAfter / 60) : 15
    throw new LoginError(
      `Too many sign in attempts. Try again in about ${minutes} minute${minutes === 1 ? '' : 's'}.`,
    )
  }
  if (!response.ok) {
    throw new LoginError(
      response.status >= 500
        ? 'Something went wrong on our side. Try again in a minute.'
        : 'Sign in did not work. Try again in a moment.',
    )
  }
  const parsed = parseSession(await response.json().catch(() => null))
  if (parsed === null) {
    throw new LoginError('The sign in answer was not understood. Try again in a moment.')
  }
  setSession(parsed)
  return parsed
}

/**
 * GET /api/auth/me — the session check on every page load. A 401 means
 * signed out (null); anything else that fails throws so the app can show a
 * retry instead of mistaking a dead API for a signed-out user.
 */
export async function fetchMe(): Promise<Session | null> {
  const response = await fetch('/api/auth/me')
  if (response.status === 401) return null
  if (!response.ok) {
    throw new ApiError(
      response.status,
      'The session check did not work.',
      retryAfterFrom(response),
    )
  }
  const parsed = parseSession(await response.json().catch(() => null))
  if (parsed === null) {
    throw new ApiError(200, 'The session check answer was not understood.')
  }
  setSession(parsed)
  return parsed
}

/** POST /api/auth/logout, then forget the session locally either way. */
export async function logout(): Promise<void> {
  try {
    await apiFetch('/auth/logout', { method: 'POST' })
  } catch {
    // The session may already be gone server-side; signing out locally stands.
  }
  clearSession()
}

// --- Role gates (the API enforces the same table; these only shape the page) ---

/** Ask, approve, refresh, and the governance demo: admin and executive. */
export function canAct(role: Role): boolean {
  return role === 'admin' || role === 'executive'
}

/** The audit log: admin, reviewer, and executive (the president runs the
 * Beat 6 audit walkthrough) — staff may not. Matches AUDIT_ROLES in the API. */
export function canSeeAuditLog(role: Role): boolean {
  return role === 'admin' || role === 'reviewer' || role === 'executive' || role === 'it'
}

/** The briefing's figures (GET /findings and the pages built on them):
 * every role but IT. Matches READ_ROLES in the API. */
export function canReadBriefing(role: Role): boolean {
  return role !== 'it'
}

/** The department overviews: each department account reads its own; the
 * president and the admin read every one. Matches OVERVIEW_ROLES. */
export function overviewDepartments(role: Role): Department[] {
  if (isDepartment(role)) return [role]
  return role === 'executive' || role === 'admin' ? [...DEPARTMENTS] : []
}

/** Accounts and connections: the admin, and IT (department accounts only). */
export function canManageAccounts(role: Role): boolean {
  return role === 'admin' || role === 'it'
}

/** Sign-in activity: IT, the admin, and the president. */
export function canSeeSessions(role: Role): boolean {
  return role === 'admin' || role === 'it' || role === 'executive'
}

/** The Financial Aid review queue: the aid office works it, the admin
 * manages it, and the executive and reviewer may read it. Staff may prepare
 * it from the decision panel but never read the rows. Matches
 * AID_QUEUE_READ_ROLES in the API. */
export function canSeeAidQueue(role: Role): boolean {
  return role === 'aid' || role === 'admin' || role === 'executive' || role === 'reviewer'
}

/** Find a student by name: the executive and the admin, the roles that may
 * open the records behind a figure. Matches ROW_ROLES in the API. */
export function canSearchStudents(role: Role): boolean {
  return role === 'admin' || role === 'executive'
}

/** Status and note on a queue row: the aid role and the admin only. */
export function canEditAidQueue(role: Role): boolean {
  return role === 'aid' || role === 'admin'
}

/** The Institution area: admin only. */
export function canSeeInstitution(role: Role): boolean {
  return role === 'admin'
}

export function roleDisplayName(role: Role): string {
  switch (role) {
    case 'admin':
      return 'Admin'
    case 'executive':
      return 'President'
    case 'it':
      return 'IT'
    case 'finance':
      return 'Finance — Student Accounts'
    case 'registrar':
      return 'Registrar'
    case 'studentlife':
      return 'Student Life'
    case 'staff':
      return 'Staff'
    case 'reviewer':
      return 'Reviewer'
    case 'aid':
      return 'Financial Aid'
  }
}
