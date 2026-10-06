import { roleDisplayName, type Role, type Session } from '../auth'
import { fieldLabels } from '../fieldLabels'
import { findingLabel } from '../findingLabels'
import { setPrefs, usePrefs, type Motion, type TextSize } from '../theme'

/** What each human role may do, mirroring the API's role table (auth.ts). */
const ROLE_ABILITIES: Record<Role, string[]> = {
  admin: [
    'Ask the approved questions',
    'Approve leadership decisions',
    'Read the audit log',
    'Manage datasets and users in Institution settings',
    'Work the Financial Aid review queue',
  ],
  executive: [
    'Ask the approved questions',
    'Approve leadership decisions',
    'Read the audit log',
    'Read the Financial Aid review queue',
  ],
  staff: ['Read the briefing, the figures and their evidence'],
  reviewer: [
    'Read the briefing, the figures and their evidence',
    'Read the audit log',
    'Read the Financial Aid review queue',
  ],
  aid: [
    'Read the briefing, the figures and their evidence',
    'Work the Financial Aid review queue, with a status and a note for each student',
  ],
}

const ROLE_ORDER: Role[] = ['admin', 'executive', 'staff', 'reviewer', 'aid']

/**
 * The Profile panel: who is signed in, where, what their role allows, and
 * sign out. Password changes go through an administrator (the API has no
 * self-service reset), and the panel says so rather than offering a button
 * that cannot work.
 */
export function ProfilePanel({
  session,
  datasetName,
  onSignOut,
}: {
  session: Session
  datasetName: string | null
  onSignOut: () => void
}) {
  const { email, role } = session.user
  const initial = email.trim().charAt(0).toUpperCase()
  return (
    <div className="account-panel">
      <div className="profile-head">
        <span className="profile-avatar" aria-hidden="true">
          {initial}
        </span>
        <div>
          <p className="profile-email">{email}</p>
          <p className="profile-role">{roleDisplayName(role)}</p>
        </div>
      </div>

      <dl className="profile-facts">
        <div>
          <dt>Institution</dt>
          <dd>{session.user.institution?.name ?? 'Not set'}</dd>
        </div>
        <div>
          <dt>Briefing data</dt>
          <dd>{datasetName ?? 'Loading…'}</dd>
        </div>
      </dl>

      <h3 className="panel-subhead">What you can do here</h3>
      <ul className="ability-list">
        {ROLE_ABILITIES[role].map((ability) => (
          <li key={ability}>{ability}</li>
        ))}
      </ul>

      <h3 className="panel-subhead">Password and session</h3>
      <p className="panel-text">Need access? Ask your administrator.</p>
      <p className="panel-text">You are signed out after 12 hours.</p>

      <button type="button" className="panel-danger" onClick={onSignOut}>
        Sign out
      </button>
    </div>
  )
}

function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T
  options: { value: T; label: string }[]
  onChange: (value: T) => void
}) {
  return (
    <div className="setting-row">
      <span className="setting-label" id={`setting-${label}`}>
        {label}
      </span>
      <div className="segmented" role="radiogroup" aria-labelledby={`setting-${label}`}>
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={value === option.value}
            className="segment"
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  )
}

/**
 * The Settings panel: appearance, text size and motion (per browser), and,
 * for an admin, the way into Institution settings (datasets and users).
 */
export function SettingsPanel({
  isAdmin,
  onOpenInstitution,
}: {
  isAdmin: boolean
  onOpenInstitution: () => void
}) {
  const prefs = usePrefs()
  return (
    <div className="account-panel">
      <h3 className="panel-subhead">Display</h3>
      <p className="panel-text">Saved in this browser only.</p>
      <Segmented<'light' | 'dark'>
        label="Theme"
        value={prefs.theme === 'dark' ? 'dark' : 'light'}
        options={[
          { value: 'light', label: 'Light' },
          { value: 'dark', label: 'Dark' },
        ]}
        onChange={(theme) => setPrefs({ theme })}
      />
      <Segmented<TextSize>
        label="Text size"
        value={prefs.textSize}
        options={[
          { value: 'standard', label: 'Standard' },
          { value: 'large', label: 'Large' },
        ]}
        onChange={(textSize) => setPrefs({ textSize })}
      />
      <Segmented<Motion>
        label="Motion"
        value={prefs.motion}
        options={[
          { value: 'full', label: 'Full' },
          { value: 'reduced', label: 'Reduced' },
        ]}
        onChange={(motion) => setPrefs({ motion })}
      />

      {isAdmin && (
        <>
          <h3 className="panel-subhead">Institution</h3>
          <p className="panel-text">
            Upload and activate the datasets the briefing is computed from, and
            add, disable or change the role of the people who can sign in.
          </p>
          <button type="button" className="panel-action" onClick={onOpenInstitution}>
            Open Institution settings
          </button>
        </>
      )}
    </div>
  )
}

export interface AccessGrant {
  role: string
  fields: string[]
  findings: string[]
  aggregate: boolean
}

const EMPLOYEE_NAMES: Record<string, string> = {
  enrollment_analyst: 'Enrollment Analyst',
  student_success_analyst: 'Student Success Analyst',
  chief_of_staff: 'Chief of Staff',
}

/** Each AI employee's job, in one plain line, in the order they work. */
const EMPLOYEE_JOBS: Record<string, string> = {
  enrollment_analyst:
    'Explains the registration figures: who has registered and how that compares with last year.',
  student_success_analyst:
    'Explains what stands in students’ way: holds, missing advising appointments and support indicators.',
  chief_of_staff:
    'Assigns the analysts, then writes the summary and its limits from their checked work, using totals only.',
}

/** The three AI employees in working order, then any other role a grant
 * names (never dropped silently). */
function employeeRoles(grants: AccessGrant[] | null): string[] {
  const roles = Object.keys(EMPLOYEE_NAMES)
  for (const grant of grants ?? []) {
    if (!roles.includes(grant.role)) roles.push(grant.role)
  }
  return roles
}

/**
 * The Data access panel: the cabinet's data boundary in one place. For each
 * AI employee, its job in one line, then the findings and the fields its
 * latest task was granted (from the audit log or the ask response, never
 * assumed); then what each human role may do. Nobody, human or AI, receives student names or identifiers.
 */
export function DataAccessPanel({
  grants,
  error = null,
  onRetry,
}: {
  grants: AccessGrant[] | null
  /** The latest run's grants could not be loaded: a plain sentence, shown
   * with Retry instead of the "ask a question" empty text. */
  error?: string | null
  onRetry?: () => void
}) {
  return (
    <div className="account-panel">
      <p className="panel-intro">
        Each AI employee sees only the fields its task needs, and never a
        student's name or identifiers. A request outside those fields is
        refused before any model runs, and the refusal is logged.
      </p>

      <h3 className="panel-subhead">AI employees</h3>
      {error !== null && (
        <div className="state-error state-panel error-panel" role="alert">
          <p>Couldn't load what each employee was given. {error}</p>
          {onRetry !== undefined && (
            <button type="button" className="btn-secondary secondary" onClick={onRetry}>
              Retry
            </button>
          )}
        </div>
      )}
      {error === null && (grants === null || grants.length === 0) && (
        <p className="panel-text state-empty">
          Ask a question to see exactly what each employee was given.
        </p>
      )}
      <div className="grant-list">
        {employeeRoles(grants).map((role) => {
          const grant = grants?.find((item) => item.role === role)
          return (
            <div key={role} className="grant-card">
              <p className="grant-name">
                {EMPLOYEE_NAMES[role] ?? role}
                {grant?.aggregate === true && <span className="grant-tag">Totals only</span>}
              </p>
              {EMPLOYEE_JOBS[role] !== undefined && (
                <p className="grant-job">{EMPLOYEE_JOBS[role]}</p>
              )}
              {grant !== undefined && grant.findings.length > 0 && (
                <p className="grant-meta">
                  Latest run explained: {grant.findings.map((id) => findingLabel(id)).join('; ')}
                </p>
              )}
              {grant !== undefined && (
                <details className="fold technical-detail">
                  <summary>
                    {grant.aggregate ? 'Totals it was given' : 'Data it was given'} (
                    {grant.fields.length})
                  </summary>
                  <ul className="plain-list">
                    {fieldLabels(grant.fields).map((label) => (
                      <li key={label}>{label}</li>
                    ))}
                  </ul>
                  <details className="fold technical-detail">
                    <summary>Technical detail</summary>
                    <ul className="field-list">
                      {grant.fields.map((field) => (
                        <li key={field}>
                          <code>{field}</code>
                        </li>
                      ))}
                    </ul>
                  </details>
                </details>
              )}
            </div>
          )
        })}
      </div>

      <h3 className="panel-subhead">People</h3>
      <table className="role-table">
        <thead>
          <tr>
            <th scope="col">Role</th>
            <th scope="col">Can</th>
          </tr>
        </thead>
        <tbody>
          {ROLE_ORDER.map((role) => (
            <tr key={role}>
              <th scope="row">{roleDisplayName(role)}</th>
              <td>{ROLE_ABILITIES[role].join('; ')}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
