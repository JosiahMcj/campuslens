import { useCallback, useEffect, useId, useState } from 'react'

import { ALL_ROLES, IT_MANAGED_ROLES, roleDisplayName, type Role } from '../auth'
import { friendlyError, friendlyLoadError } from '../errors'
import { fetchSessions, type SessionRow } from '../inbox'
import { friendlyTime } from '../states'
import {
  addUser,
  fetchUsers,
  newUserEmailError,
  setUserDisabled,
  userStatusLabel,
  type CreatedUser,
  type UserRow,
} from '../users'

import './Roles.css'

function LoadFailed({ what, message, onRetry }: { what: string; message: string; onRetry: () => void }) {
  return (
    <div className="state-panel error-panel state-error" role="alert">
      <p>
        We couldn't load {what}. {message}
      </p>
      <button type="button" className="secondary btn-secondary" onClick={onRetry}>
        Retry
      </button>
    </div>
  )
}

function Loading({ what }: { what: string }) {
  return (
    <div role="status" aria-busy="true" className="panel-skeleton">
      <span className="visually-hidden">Loading {what}…</span>
      <div className="skeleton skeleton-line skeleton-heading" />
      <div className="skeleton skeleton-line" />
    </div>
  )
}

/**
 * Accounts: everyone who can sign in, with Disable/Enable and Add an account.
 * IT manages the department and staff accounts; admin, executive and IT
 * accounts are shown but only an administrator changes them (the API
 * refuses IT there too).
 */
export function AccountsPage({ role, currentUserEmail }: { role: Role; currentUserEmail: string }) {
  const [users, setUsers] = useState<UserRow[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const assignable = role === 'it' ? IT_MANAGED_ROLES : ALL_ROLES
  const [email, setEmail] = useState('')
  const [newRole, setNewRole] = useState<Role>(assignable[0])
  const [emailError, setEmailError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [created, setCreated] = useState<CreatedUser | null>(null)
  const [rowBusy, setRowBusy] = useState<number | null>(null)
  const emailId = useId()
  const roleId = useId()

  const load = useCallback(async () => {
    try {
      setUsers(await fetchUsers())
      setLoadError(null)
    } catch (failure) {
      setLoadError(friendlyLoadError(failure))
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the fetch's setState lands after an await
    void load()
  }, [load])

  const add = async () => {
    const problem = newUserEmailError(email)
    setEmailError(problem)
    setError(null)
    if (problem !== null) return
    setBusy(true)
    try {
      setCreated(await addUser(email.trim(), newRole))
      setEmail('')
      await load()
    } catch (failure) {
      setError(friendlyError(failure, 'The new account'))
    } finally {
      setBusy(false)
    }
  }

  const toggle = async (user: UserRow) => {
    setRowBusy(user.id)
    setError(null)
    try {
      await setUserDisabled(user.id, !user.disabled)
      await load()
    } catch (failure) {
      setError(friendlyError(failure, user.disabled ? 'Enabling the account' : 'Disabling the account'))
    } finally {
      setRowBusy(null)
    }
  }

  return (
    <div className="it-page">
      <form
        className="inst-form"
        noValidate
        onSubmit={(event) => {
          event.preventDefault()
          void add()
        }}
      >
        <h3>Add an account</h3>
        <div className="inst-fields">
          <div className="inst-field">
            <label htmlFor={emailId}>Email</label>
            <input
              id={emailId}
              type="email"
              inputMode="email"
              autoComplete="off"
              placeholder="e.g. name@example.edu"
              value={email}
              disabled={busy}
              aria-invalid={emailError !== null}
              onChange={(event) => {
                setEmail(event.target.value)
                setEmailError(null)
              }}
            />
            {emailError !== null && <p className="field-error">{emailError}</p>}
          </div>
          <div className="inst-field inst-field-narrow">
            <label htmlFor={roleId}>Role</label>
            <select
              id={roleId}
              value={newRole}
              disabled={busy}
              onChange={(event) => setNewRole(event.target.value as Role)}
            >
              {assignable.map((option) => (
                <option key={option} value={option}>
                  {roleDisplayName(option)}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="inst-actions">
          <button type="submit" className="btn-primary" disabled={busy} aria-busy={busy}>
            {busy ? 'Adding…' : 'Add the account'}
          </button>
        </div>
      </form>
      {created !== null && (
        <div className="password-callout" role="status">
          {created.one_time_password === null ? (
            <p>
              {created.email} was added as {roleDisplayName(created.role)}. An administrator
              issues their first password.
            </p>
          ) : (
            <>
              <p>
                {created.email} was added as {roleDisplayName(created.role)}. Their one-time
                password is shown only now:
              </p>
              <p className="password-value">{created.one_time_password}</p>
            </>
          )}
        </div>
      )}
      {error !== null && (
        <p className="error-line" role="alert">
          {error}
        </p>
      )}
      {users === null ? (
        loadError !== null ? (
          <LoadFailed what="the accounts" message={loadError} onRetry={() => void load()} />
        ) : (
          <Loading what="the accounts" />
        )
      ) : (
        <div className="overview-table-scroll">
          <table className="it-table">
            <thead>
              <tr>
                <th scope="col">Account</th>
                <th scope="col">Role</th>
                <th scope="col">Status</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => {
                const self = user.email === currentUserEmail
                const managed = role === 'admin' || IT_MANAGED_ROLES.includes(user.role)
                return (
                  <tr key={user.id}>
                    <th scope="row">
                      {user.email}
                      {self && <span className="hint"> (you)</span>}
                    </th>
                    <td>{roleDisplayName(user.role)}</td>
                    <td>{userStatusLabel(user)}</td>
                    <td className="it-actions">
                      {managed && !self ? (
                        <button
                          type="button"
                          className="secondary btn-secondary"
                          disabled={rowBusy === user.id}
                          aria-label={`${user.disabled ? 'Enable' : 'Disable'} ${user.email}`}
                          onClick={() => void toggle(user)}
                        >
                          {user.disabled ? 'Enable' : 'Disable'}
                        </button>
                      ) : (
                        <span className="hint">{self ? '' : 'Administrator only'}</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

/** Sign-in activity: per account, live sessions and when it was last seen. */
export function SessionsPage() {
  const [rows, setRows] = useState<SessionRow[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setRows(await fetchSessions())
      setLoadError(null)
    } catch (failure) {
      setLoadError(friendlyLoadError(failure))
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the fetch's setState lands after an await
    void load()
  }, [load])

  if (rows === null) {
    return loadError !== null ? (
      <LoadFailed what="sign-in activity" message={loadError} onRetry={() => void load()} />
    ) : (
      <Loading what="sign-in activity" />
    )
  }
  const live = rows.filter((row) => row.live_sessions > 0).length
  return (
    <div className="it-page">
      <p className="hint">
        {live} of {rows.length} accounts are signed in right now.{' '}
        <button type="button" className="link-button" onClick={() => void load()}>
          Refresh
        </button>
      </p>
      <div className="overview-table-scroll">
        <table className="it-table">
          <thead>
            <tr>
              <th scope="col">Account</th>
              <th scope="col">Role</th>
              <th scope="col">Signed in now</th>
              <th scope="col">Last seen</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <th scope="row">
                  {row.email}
                  {row.disabled && <span className="hint"> (disabled)</span>}
                </th>
                <td>{roleDisplayName(row.role)}</td>
                <td>{row.live_sessions > 0 ? `Yes (${row.live_sessions})` : 'No'}</td>
                <td>{row.last_seen !== null ? (friendlyTime(row.last_seen) ?? '—') : 'Never'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
