import { useState, type FormEvent } from 'react'

import { LoginError, login, type Session } from '../auth'
import { errorMessage } from '../states'

interface LoginScreenProps {
  /** A line above the form, e.g. "Your session ended. Sign in again." */
  notice: string | null
  onSignedIn: (session: Session) => void
}

/**
 * The /login screen: email, password, "Sign in". Errors name the problem the
 * API can honestly report: wrong email or password (which deliberately also
 * covers a disabled account), too many attempts, or an unreachable service.
 * Instrument font throughout: this is a control, not the document.
 */
export function LoginScreen({ notice, onSignedIn }: LoginScreenProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (submitting) return
    const trimmedEmail = email.trim()
    if (trimmedEmail.length === 0 || password.length === 0) {
      setError('Enter your email and your password.')
      return
    }
    setSubmitting(true)
    setError(null)
    void (async () => {
      try {
        onSignedIn(await login(trimmedEmail, password))
      } catch (cause) {
        setError(
          cause instanceof LoginError
            ? cause.message
            : `Sign in did not work. ${errorMessage(cause)}`,
        )
        setSubmitting(false)
      }
    })()
  }

  return (
    <main className="login-page">
      <div className="login-panel">
        <h1>Golden Eagle AI Cabinet</h1>
        <p className="login-lede">
          The weekly student success briefing, under your institution's
          governance. Sign in to continue.
        </p>
        {notice !== null && (
          <p className="login-notice" role="status">
            {notice}
          </p>
        )}
        <form onSubmit={submit}>
          <label htmlFor="login-email">Email</label>
          <input
            id="login-email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            disabled={submitting}
          />
          <label htmlFor="login-password">Password</label>
          <input
            id="login-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={submitting}
          />
          <button type="submit" className="primary-button" disabled={submitting}>
            {submitting ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
        {error !== null && (
          <p className="error-line" role="alert">
            {error}
          </p>
        )}
      </div>
    </main>
  )
}
