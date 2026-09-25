import { canSeeInstitution, roleDisplayName, type Session } from '../auth'

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
 * The rail's masthead (rail top): the product name, the institution and the
 * active dataset this briefing is computed from, who is signed in (email and
 * role), the Institution link for an admin, and sign out. Instrument font,
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

  return (
    <div className="rail-masthead masthead">
      <p className="masthead-product">Golden Eagle AI Cabinet</p>
      {institutionName !== null && <p className="masthead-institution">{institutionName}</p>}
      {datasetName !== null && (
        <p className="masthead-dataset">
          Dataset: {datasetName}
        </p>
      )}
      <p className="masthead-user">
        {session.user.email} · {roleDisplayName(session.user.role)}
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
        <button type="button" className="secondary" onClick={onSignOut}>
          Sign out
        </button>
      </div>
    </div>
  )
}
