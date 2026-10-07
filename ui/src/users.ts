// Typed client for the institution-admin user routes (GET/POST
// /api/admin/users, disable, enable, role change), plus the pure mappers
// the Institution area's Users section renders. The one-time password of a
// newly created user exists only in the creation response: it is held in
// component state for the single callout and never written to storage.
// Every failure throws an ApiError whose message is a plain sentence or
// empty (ui/src/adminErrors.ts); the screen words it with friendlyError.

import { failureFrom, apiFailure, type KnownDetails } from './adminErrors'
import { ALL_ROLES, apiFetch, type Role } from './auth'

export interface UserRow {
  id: number
  email: string
  role: Role
  disabled: boolean
  created_at: string
}

export interface CreatedUser {
  id: number
  email: string
  role: Role
  /** Null when an administrator issues the password: IT creates accounts
   * but never sees their passwords. */
  one_time_password: string | null
}

const ROLES: readonly string[] = ALL_ROLES

function userFrom(value: unknown): UserRow | null {
  if (typeof value !== 'object' || value === null) return null
  const record = value as Record<string, unknown>
  if (typeof record.id !== 'number' || typeof record.email !== 'string') return null
  if (typeof record.role !== 'string' || !ROLES.includes(record.role)) return null
  return {
    id: record.id,
    email: record.email,
    role: record.role as Role,
    disabled: record.disabled === true,
    created_at: typeof record.created_at === 'string' ? record.created_at : '',
  }
}

/** The API's refusals that the screen can explain in its own words. */
export const USER_REFUSALS: KnownDetails = [
  ['already exists', 'Someone with that email can already sign in.'],
  ['cannot disable their own account', "You can't disable your own account."],
  [
    'IT manages department',
    'IT manages department and staff accounts. An administrator changes admin, executive and IT accounts.',
  ],
  [
    'last enabled administrator',
    'This is the last active administrator. Make someone else an administrator first.',
  ],
]

/** Map a creation response to the new user, or throw; exported for tests. */
export function createdUserFrom(status: number, body: unknown): CreatedUser {
  const record = typeof body === 'object' && body !== null ? (body as Record<string, unknown>) : {}
  if (
    status === 201 &&
    typeof record.id === 'number' &&
    typeof record.email === 'string' &&
    typeof record.role === 'string' &&
    ROLES.includes(record.role) &&
    (typeof record.one_time_password === 'string' || record.one_time_password === null)
  ) {
    return {
      id: record.id,
      email: record.email,
      role: record.role as Role,
      one_time_password: record.one_time_password,
    }
  }
  throw failureFrom(status === 201 ? 500 : status, body, USER_REFUSALS)
}

export async function fetchUsers(): Promise<UserRow[]> {
  const response = await apiFetch('/admin/users')
  if (!response.ok) throw await apiFailure(response)
  const body: unknown = await response.json().catch(() => null)
  if (!Array.isArray(body)) return []
  return body.map(userFrom).filter((user): user is UserRow => user !== null)
}

/**
 * POST /api/admin/users — creates the user and answers 201 with the
 * one-time password (shown once, in the callout). A refusal (an email that
 * already has an account) throws with a plain sentence.
 */
export async function addUser(email: string, role: Role): Promise<CreatedUser> {
  const response = await apiFetch('/admin/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, role }),
  })
  const body: unknown = await response.json().catch(() => null)
  return createdUserFrom(response.status, body)
}

/** POST /api/admin/users/<id>/disable or /enable. */
export async function setUserDisabled(id: number, disabled: boolean): Promise<void> {
  const response = await apiFetch(`/admin/users/${id}/${disabled ? 'disable' : 'enable'}`, {
    method: 'POST',
  })
  if (!response.ok) throw await apiFailure(response, USER_REFUSALS)
}

/** PATCH /api/admin/users/<id> — a role change. */
export async function setUserRole(id: number, role: Role): Promise<void> {
  const response = await apiFetch(`/admin/users/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role }),
  })
  if (!response.ok) throw await apiFailure(response, USER_REFUSALS)
}

// The API's own shape check for an email: local@domain.tld.
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

/** The Add-a-user email field's error, or null when it can be sent. */
export function newUserEmailError(email: string): string | null {
  const value = email.trim()
  if (value === '') return 'Enter the email address of the person to add.'
  if (!EMAIL_RE.test(value)) return 'Enter an email address like name@example.edu.'
  return null
}

/** The status cell of a user row: "Active" or "Disabled". */
export function userStatusLabel(user: UserRow): string {
  return user.disabled ? 'Disabled' : 'Active'
}
