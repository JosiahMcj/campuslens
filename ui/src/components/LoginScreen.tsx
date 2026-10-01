import { useState, type FormEvent, type MouseEvent, type ReactNode } from 'react'

import { LoginError, login, type Session } from '../auth'
import { errorMessage } from '../states'
import { AscentMark } from './AscentMark'
import { EyeIcon, EyeOffIcon } from './icons'

interface LoginScreenProps {
  /** A line above the form, e.g. "Your session ended. Sign in again." */
  notice: string | null
  onSignedIn: (session: Session) => void
}

/**
 * One line of the form, revealed as in the animated sign-in reference: a
 * box sweeps across while the content rises into place. Pure CSS (the
 * `order` picks the stagger step), so no inline styles are needed.
 */
function Reveal({
  order,
  wide = false,
  children,
}: {
  order: number
  wide?: boolean
  children: ReactNode
}) {
  return <div className={`reveal reveal-${order}${wide ? ' reveal-wide' : ''}`}>{children}</div>
}

/**
 * An input inside a frame whose border glows under the pointer (the
 * reference's radial hover). The glow follows the pointer through CSS custom
 * properties set on the element's style object (CSSOM), never an inline
 * style attribute, so the page's CSP holds.
 */
function GlowField({ children }: { children: ReactNode }) {
  const track = (event: MouseEvent<HTMLDivElement>) => {
    const frame = event.currentTarget
    const rect = frame.getBoundingClientRect()
    frame.style.setProperty('--glow-x', `${event.clientX - rect.left}px`)
    frame.style.setProperty('--glow-y', `${event.clientY - rect.top}px`)
  }
  return (
    <div className="glow-field" onMouseMove={track}>
      {children}
    </div>
  )
}

/**
 * The landing page: one static screen with no scroll. The left side is the
 * hero from the referenced SaaS landing template (hatched ground, gradient
 * glow, announcement pill, headline, lede); the right side is the sign-in
 * card, after the animated sign-in reference. Errors name the problem the
 * API can honestly report: wrong email or password (which deliberately also
 * covers a disabled account), too many attempts, or an unreachable service.
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
    <main className="landing">
      <div className="landing-glow" aria-hidden="true" />

      <header className="landing-brand">
        <AscentMark className="landing-mark" />
        <span>Golden Eagle Cabinet</span>
      </header>

      <div className="landing-grid">
        <section className="landing-hero" aria-labelledby="landing-title">
          <p className="landing-pill">
            <span className="landing-pill-dot" aria-hidden="true" />
            Built for the Gloo AI Hackathon, Boulder 2026
          </p>
          <h1 id="landing-title">
            Student success,
            <br />
            briefed and ready.
          </h1>
          <p className="landing-lede">
            Golden Eagle Cabinet reads your student records, explains what has
            changed, and leaves the decision to you. Every number traces back
            to its source.
          </p>
        </section>

        <section className="landing-card" aria-labelledby="login-title">
          <Reveal order={1}>
            <h2 id="login-title">Welcome back</h2>
          </Reveal>
          <Reveal order={2}>
            <p className="landing-card-sub">Sign in to your cabinet.</p>
          </Reveal>

          {notice !== null && (
            <Reveal order={3} wide>
              <p className="landing-notice" role="status">
                {notice}
              </p>
            </Reveal>
          )}

          <form onSubmit={submit} noValidate>
            <div className="landing-field">
              <Reveal order={3}>
                <label htmlFor="login-email">
                  Email <span className="landing-required">*</span>
                </label>
              </Reveal>
              <Reveal order={4} wide>
                <GlowField>
                  <input
                    id="login-email"
                    type="email"
                    autoComplete="username"
                    placeholder="you@university.edu"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    disabled={submitting}
                    aria-invalid={fieldErrors.email !== undefined}
                    aria-describedby="login-email-error"
                  />
                </GlowField>
                <p className="landing-field-error" id="login-email-error">
                  {fieldErrors.email ?? ''}
                </p>
              </Reveal>
            </div>

            <div className="landing-field">
              <Reveal order={5}>
                <label htmlFor="login-password">
                  Password <span className="landing-required">*</span>
                </label>
              </Reveal>
              <Reveal order={6} wide>
                <GlowField>
                  <input
                    id="login-password"
                    type={showPassword ? 'text' : 'password'}
                    autoComplete="current-password"
                    placeholder="Your password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    disabled={submitting}
                    aria-invalid={fieldErrors.password !== undefined}
                    aria-describedby="login-password-error"
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
                </GlowField>
                <p className="landing-field-error" id="login-password-error">
                  {fieldErrors.password ?? ''}
                </p>
              </Reveal>
            </div>

            {error !== null && (
              <p className="landing-error" role="alert">
                {error}
              </p>
            )}

            <Reveal order={7} wide>
              <button type="submit" className="landing-submit" disabled={submitting}>
                {submitting ? 'Signing in…' : 'Sign in →'}
                <span className="landing-submit-line" aria-hidden="true" />
                <span className="landing-submit-blur" aria-hidden="true" />
              </button>
            </Reveal>
          </form>

          <Reveal order={8}>
            <p className="landing-help">Need access? Ask your cabinet administrator.</p>
          </Reveal>
        </section>
      </div>
    </main>
  )
}
