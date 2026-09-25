// Typed client for the institution-admin user routes (GET/POST
// /api/admin/users, disable, enable, role change), plus the pure mappers
// the Institution area's Users section renders. The one-time password of a
// newly created user exists only in the creation response: it is held in
// component state for the single callout and never written to storage.

import { ApiError, apiDetail, apiFetch, type Role } from './auth'

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
  one_time_password: string
}

export type AddUserResult = { ok: true; user: CreatedUser } | { ok: false; errors: string[] }

export type UserActionResult = { ok: true } | { ok: false; message: string }

const ROLES: readonly string[] = ['admin', 'executive', 'staff', 'reviewer']

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

/** Map a creation response onto the two render branches; exported for tests. */
export function addUserResultFrom(status: number, body: unknown): AddUserResult {
  const record = typeof body === 'object' && body !== null ? (body as Record<string, unknown>) : {}
  if (status === 201) {
    if (
      typeof record.id === 'number' &&
      typeof record.email === 'string' &&
      typeof record.role === 'string' &&
      ROLES.includes(record.role) &&
      typeof record.one_time_password === 'string'
    ) {
      return {
        ok: true,
        user: {
          id: record.id,
          email: record.email,
          role: record.role as Role,
          one_time_password: record.one_time_password,
        },
      }
    }
    return { ok: false, errors: ['The user was created but the answer was not understood.'] }
  }
  const detail = typeof record.detail === 'string' ? record.detail : null
  return {
    ok: false,
    errors: [detail ?? `The user could not be added (HTTP ${status}). Try again.`],
  }
}

export async function fetchUsers(): Promise<UserRow[]> {
  const response = await apiFetch('/admin/users')
  if (!response.ok) {
    throw new ApiError(
      response.status,
      await apiDetail(response, `The user list failed to load (HTTP ${response.status}).`),
    )
  }
  const body: unknown = await response.json().catch(() => null)
  if (!Array.isArray(body)) return []
  return body.map(userFrom).filter((user): user is UserRow => user !== null)
}

/**
 * POST /api/admin/users — creates the user and answers 201 with the
 * one-time password (shown once, in the callout) or an error detail
 * (duplicate email, validation) rendered plainly.
 */
export async function addUser(email: string, role: Role): Promise<AddUserResult> {
  const response = await apiFetch('/admin/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, role }),
  })
  const body: unknown = await response.json().catch(() => null)
  return addUserResultFrom(response.status, body)
}

/** POST /api/admin/users/<id>/disable or /enable. */
export async function setUserDisabled(id: number, disabled: boolean): Promise<UserActionResult> {
  const response = await apiFetch(`/admin/users/${id}/${disabled ? 'disable' : 'enable'}`, {
    method: 'POST',
  })
  if (!response.ok) {
    return {
      ok: false,
      message: await apiDetail(
        response,
        `The user could not be ${disabled ? 'disabled' : 'enabled'} (HTTP ${response.status}).`,
      ),
    }
  }
  return { ok: true }
}

/** PATCH /api/admin/users/<id> — a role change. */
export async function setUserRole(id: number, role: Role): Promise<UserActionResult> {
  const response = await apiFetch(`/admin/users/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role }),
  })
  if (!response.ok) {
    return {
      ok: false,
      message: await apiDetail(response, `The role could not be changed (HTTP ${response.status}).`),
    }
  }
  return { ok: true }
}

/** The status cell of a user row: "Active" or "Disabled". */
export function userStatusLabel(user: UserRow): string {
  return user.disabled ? 'Disabled' : 'Active'
}
