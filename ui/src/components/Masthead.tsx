import { useState } from 'react'

import { canSeeInstitution, roleDisplayName, type Session } from '../auth'
import { currentTheme, setTheme, type Theme } from '../theme'
import { AscentMark } from './AscentMark'
import { MoonIcon, SunIcon } from './icons'

interface MastheadProps {
  session: Session
  /** The active dataset's name, once the findings have loaded. */
  datasetName: string | null
  /** The current path, so the nav link points at the other area. */
  route: string
  onNavigate: (path: string) => void
  onSignOut: () => void
}

/**
 * The rail's masthead, in two parts: the brand block at the top (the
 * product name, the institution and the active dataset this briefing is
 * computed from) and the account block pinned to the bottom (who is signed
 * in, the Institution link for an admin, the theme switch, and sign out). Instrument font,
 * small, quiet: these are controls, not the document.
 */
export function Masthead({
  session,
  datasetName,
  route,
  onNavigate,
  onSignOut,
}: MastheadProps) {
  const institutionName = session.user.institution?.name ?? null
  const admin = canSeeInstitution(session.user.role)
  const navTarget = route === '/institution' ? '/' : '/institution'
  const navLabel = route === '/institution' ? 'Briefing' : 'Institution'

  const [theme, setThemeState] = useState<Theme>(currentTheme)
  const nextTheme: Theme = theme === 'dark' ? 'light' : 'dark'
  const initial = session.user.email.trim().charAt(0).toUpperCase()

  return (
    <>
      <div className="rail-masthead masthead masthead-brand">
        <p className="masthead-product">
          <AscentMark className="masthead-mark" />
          Golden Eagle AI Cabinet
        </p>
        {institutionName !== null && <p className="masthead-institution">{institutionName}</p>}
        {datasetName !== null && (
          <p className="masthead-dataset">
            Dataset: {datasetName}
          </p>
        )}
      </div>
      <div className="masthead masthead-account">
        <p className="masthead-user">
          <span className="masthead-avatar" aria-hidden="true">
            {initial}
          </span>
          <span className="masthead-user-text">
            {session.user.email} · {roleDisplayName(session.user.role)}
          </span>
        </p>
        <div className="masthead-actions">
          {admin && (
            <a
              href={navTarget}
              onClick={(event) => {
                event.preventDefault()
                onNavigate(navTarget)
              }}
            >
              {navLabel}
            </a>
          )}
          <button
            type="button"
            className="secondary theme-toggle"
            aria-label={`Switch to ${nextTheme} theme`}
            title={`Switch to ${nextTheme} theme`}
            onClick={() => {
              setTheme(nextTheme)
              setThemeState(nextTheme)
            }}
          >
            {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
          </button>
          <button type="button" className="secondary" onClick={onSignOut}>
            Sign out
          </button>
        </div>
      </div>
    </>
  )
}
