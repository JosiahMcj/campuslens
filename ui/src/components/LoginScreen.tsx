import { useState, type FormEvent } from 'react'

import { LoginError, login, type Session } from '../auth'
import { AscentMark } from './AscentMark'
import { EyeIcon, EyeOffIcon } from './icons'

interface LoginScreenProps {
  /** A line above the form, e.g. "Your session ended. Sign in again." */
  notice: string | null
  onSignedIn: (session: Session) => void
}

/** Shown when sign in fails in a way the sign-in client did not name. */
export const SIGN_IN_FALLBACK_ERROR = 'Something went wrong on our side. Try again in a minute.'

/**
 * The sign-in screen, drawn from the app's own tokens so it follows the
 * chosen theme (light by default, dark when the person picked dark): the
 * product's one-line description on the left, the sign-in card on the
 * right, stacked on phones. The page scrolls whenever it is taller than the
 * screen, so enlarged text never hides the form. Errors name the problem
 * the sign-in client can honestly report: wrong email or password (which
 * deliberately also covers a disabled account), too many attempts, or an
 * unreachable service.
 */
export function LoginScreen({ notice, onSignedIn }: LoginScreenProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<{ email?: string; password?: string }>({})

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (submitting) return
    const trimmedEmail = email.trim()
    const problems: { email?: string; password?: string } = {}
    if (trimmedEmail.length === 0) problems.email = 'Enter your email.'
    else if (!/\S+@\S+\.\S+/.test(trimmedEmail)) problems.email = 'That email looks incomplete.'
    if (password.length === 0) problems.password = 'Enter your password.'
    setFieldErrors(problems)
    if (problems.email !== undefined || problems.password !== undefined) {
      setError(null)
      return
    }
    setSubmitting(true)
    setError(null)
    void (async () => {
      try {
        onSignedIn(await login(trimmedEmail, password))
      } catch (cause) {
        setError(cause instanceof LoginError ? cause.message : SIGN_IN_FALLBACK_ERROR)
      } finally {
        setSubmitting(false)
      }
    })()
  }

  const emailError = fieldErrors.email
  const passwordError = fieldErrors.password

  return (
    <main className="landing">
      <header className="landing-brand">
        <AscentMark className="landing-mark" />
        <span>Golden Eagle AI Cabinet</span>
      </header>

      <div className="landing-grid">
        <section className="landing-hero" aria-labelledby="landing-title">
          <h1 id="landing-title">Student success, briefed and ready.</h1>
          <p className="landing-lede">
            Golden Eagle AI Cabinet reads your student records, explains what has changed, and
            leaves the decision to you. Every number traces back to its source.
          </p>
        </section>

        <section className="landing-card" aria-labelledby="login-title">
          <h2 id="login-title">Sign in</h2>
          <p className="landing-card-sub">Use the email your administrator set up.</p>

          {notice !== null && (
            <p className="landing-notice" role="status">
              {notice}
            </p>
          )}

          <form onSubmit={submit} noValidate>
            <div className="landing-field">
              <label htmlFor="login-email">Email</label>
              <input
                id="login-email"
                type="email"
                autoComplete="username"
                inputMode="email"
                spellCheck={false}
                placeholder="e.g. you@university.edu"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                disabled={submitting}
                aria-invalid={emailError !== undefined}
                aria-describedby={emailError !== undefined ? 'login-email-error' : undefined}
              />
              {emailError !== undefined && (
                <p className="landing-field-error" id="login-email-error">
                  {emailError}
                </p>
              )}
            </div>

            <div className="landing-field">
              <label htmlFor="login-password">Password</label>
              <div className="landing-password">
                <input
                  id="login-password"
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  disabled={submitting}
                  aria-invalid={passwordError !== undefined}
                  aria-describedby={
                    passwordError !== undefined ? 'login-password-error' : undefined
                  }
                />
                <button
                  type="button"
                  className="landing-eye"
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                  aria-pressed={showPassword}
                  onClick={() => setShowPassword((value) => !value)}
                >
                  {showPassword ? <EyeIcon /> : <EyeOffIcon />}
                </button>
              </div>
              {passwordError !== undefined && (
                <p className="landing-field-error" id="login-password-error">
                  {passwordError}
                </p>
              )}
            </div>

            <button
              type="submit"
              className="btn-primary landing-submit"
              disabled={submitting}
              aria-describedby={error !== null ? 'login-error' : undefined}
            >
              {submitting && <span className="save-spinner" aria-hidden="true" />}
              {submitting ? 'Signing in…' : 'Sign in'}
            </button>

            {error !== null && (
              <p className="landing-error" id="login-error" role="alert">
                {error}
              </p>
            )}
          </form>

          <p className="landing-help">Need access? Ask your administrator.</p>
        </section>
      </div>
    </main>
  )
}
