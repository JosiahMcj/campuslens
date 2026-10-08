import { useLayoutEffect, useRef, useState } from 'react'

import { roleDisplayName, type Role, type Session } from '../auth'
import { fieldLabels } from '../fieldLabels'
import { findingLabel } from '../findingLabels'
import type { StaffEmployee } from '../staff'
import { setPrefs, usePrefs, type Motion, type TextSize, type ThemeChoice } from '../theme'

/** What each human role may do, mirroring the API's role table (auth.ts). */
/** The Explore line: every role but Financial Aid may ask it. */
const ASK_ANYTHING = 'Ask any question about students, courses and majors (totals only)'

/** The demonstration directory: a logged name search. */
const FIND_STUDENT = 'Find a student by name in the demonstration directory (each search is logged)'

/** What each human role may do, mirroring the API's role table (auth.ts). */
const ROLE_ABILITIES: Record<Role, string[]> = {
  admin: [
    'Ask the approved questions',
    ASK_ANYTHING,
    'Approve leadership decisions',
    'Read the audit log',
    FIND_STUDENT,
    'Manage datasets and users in Institution settings',
    'Track the staff actions and send them to their offices',
    'Work the Financial Aid review queue',
  ],
  executive: [
    'Ask the approved questions',
    ASK_ANYTHING,
    'Approve leadership decisions',
    'Read the audit log',
    FIND_STUDENT,
    'Follow the staff actions and add notes to them',
    'Read the Financial Aid review queue',
    "Open every department's overview and send alerts to any office",
  ],
  staff: [
    'Read the briefing, the figures and their evidence',
    ASK_ANYTHING,
    'Track the staff actions: status, owner, due date and notes',
    'Send a staff action to its office',
  ],
  reviewer: [
    'Read the briefing, the figures and their evidence',
    ASK_ANYTHING,
    'Read the audit log',
    'Follow the staff actions (read only)',
    'Read the Financial Aid review queue',
  ],
  aid: [
    'Read the briefing, the figures and their evidence',
    'Follow the Financial Aid staff action',
    'Work the Financial Aid review queue, with a status and a note for each student',
    'Open the Financial Aid overview: Pell recipients by class level and college, totals only',
    'Receive and send inbox alerts',
  ],
  finance: [
    "Open the Finance overview: the university's budget, revenue and tuition discount",
    ASK_ANYTHING,
    'Receive and send inbox alerts',
  ],
  studentaccounts: [
    'Open the Student Accounts overview: balances, holds and payments, totals only',
    ASK_ANYTHING,
    'Receive and send inbox alerts',
  ],
  registrar: [
    'Open the Registrar overview: enrollment and academic standing, totals only',
    ASK_ANYTHING,
    'Receive and send inbox alerts',
  ],
  studentlife: [
    'Open the Student Life overview: retention, housing and advising, totals only',
    ASK_ANYTHING,
    'Receive and send inbox alerts',
  ],
  admissions: [
    'Open the Admissions overview: entering classes by admit type, residency and college, totals only',
    ASK_ANYTHING,
    'Receive and send inbox alerts',
  ],
  advising: [
    'Open the Advising and Student Success overview: advising coverage, appointments and who leaves without a degree, totals only',
    ASK_ANYTHING,
    'Receive and send inbox alerts',
  ],
  provost: [
    'Open the Academic Affairs overview: D, F and withdrawal rates, hardest courses, section sizes, totals only',
    ASK_ANYTHING,
    'Receive and send inbox alerts',
  ],
  ir: [
    'Open the Institutional Research overview: enrollment, retention and degrees awarded over the years, totals only',
    ASK_ANYTHING,
    'Receive and send inbox alerts',
  ],
  careers: [
    'Open the Career Services overview: first destinations, starting salaries and graduate school, totals only',
    ASK_ANYTHING,
    'Receive and send inbox alerts',
  ],
  advancement: [
    'Open the Advancement overview: alumni giving participation and gifts, totals only',
    ASK_ANYTHING,
    'Receive and send inbox alerts',
  ],
  international: [
    'Open the International Student Services overview: international students by college and term, totals only',
    ASK_ANYTHING,
    'Receive and send inbox alerts',
  ],
  athletics: [
    'Open the Athletics overview: athletes compared with other students, totals only',
    ASK_ANYTHING,
    'Receive and send inbox alerts',
  ],
  it: [
    'Manage the department and staff accounts',
    'See sign-in activity and the outside connections',
    'Read the audit log',
    'Nothing about students: no briefing, no figures, no questions',
  ],
}

/** Under the People table: who sees instructor names in those answers. */
export const INSTRUCTOR_NAMES_NOTE =
  'Instructor names are shown to the executive and admin only.'

const ROLE_ORDER: Role[] = [
  'executive',
  'admin',
  'it',
  'finance',
  'studentaccounts',
  'aid',
  'registrar',
  'studentlife',
  'admissions',
  'advising',
  'provost',
  'ir',
  'careers',
  'advancement',
  'international',
  'athletics',
  'staff',
  'reviewer',
]

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

      {/* Signing out loses nothing, so it is a plain secondary button. */}
      <button type="button" className="btn-secondary profile-sign-out" onClick={onSignOut}>
        Sign out
      </button>
    </div>
  )
}

/**
 * A choice of a few options, as a radio group: one Tab stop (the chosen
 * option), and the arrow keys move the choice, Home and End jump to the
 * first and last, as a radio group does everywhere else.
 */
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
  const id = `setting-${label.toLowerCase().replace(/\s+/g, '-')}`
  const current = Math.max(
    0,
    options.findIndex((option) => option.value === value),
  )
  const choose = (index: number, group: HTMLElement | null) => {
    const next = (index + options.length) % options.length
    onChange(options[next].value)
    group?.querySelectorAll<HTMLButtonElement>('[role="radio"]')[next]?.focus()
  }
  return (
    <div className="setting-row">
      <span className="setting-label" id={id}>
        {label}
      </span>
      <div
        className="segmented"
        role="radiogroup"
        aria-labelledby={id}
        onKeyDown={(event) => {
          const group = event.currentTarget
          if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
            event.preventDefault()
            choose(current + 1, group)
          } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
            event.preventDefault()
            choose(current - 1, group)
          } else if (event.key === 'Home') {
            event.preventDefault()
            choose(0, group)
          } else if (event.key === 'End') {
            event.preventDefault()
            choose(options.length - 1, group)
          }
        }}
      >
        {options.map((option, index) => (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={index === current}
            tabIndex={index === current ? 0 : -1}
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
      <Segmented<ThemeChoice>
        label="Theme"
        value={prefs.theme}
        options={[
          { value: 'system', label: 'Automatic' },
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

/**
 * One scope list ("May read" / "Never reads"): clamped to a fixed number of
 * lines so a long list cannot stretch its row of cards, with a Show all
 * toggle that appears only when the text really is cut off.
 */
function ScopeBlock({ label, text }: { label: string; text: string }) {
  const ref = useRef<HTMLParagraphElement>(null)
  const [open, setOpen] = useState(false)
  const [cut, setCut] = useState(false)
  useLayoutEffect(() => {
    const el = ref.current
    if (el === null) return
    const measure = () => {
      if (!open) setCut(el.scrollHeight > el.clientHeight + 1)
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [text, open])
  return (
    <div className="grant-scope">
      <p className="grant-scope-label">{label}</p>
      <p ref={ref} className={`grant-scope-text${open ? ' is-open' : ''}`}>
        {text}
      </p>
      {(cut || open) && (
        <button
          type="button"
          className="grant-scope-toggle"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? 'Show less' : 'Show all'}
        </button>
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

/** Each employee's request count today, in words. */
function requestsToday(count: number): string {
  if (count === 0) return 'No requests today'
  return `Handled ${count} ${count === 1 ? 'request' : 'requests'} today`
}

/**
 * The "AI employees and data access" page: one card for each department's
 * AI employee (from GET /staff, the signed-in department's own first): its
 * title, the office it serves, its job, what it may read (always as
 * totals), what it may never read, the data outside its job, and how many
 * requests it handled today. For the briefing's employees, what the latest
 * run gave each (from the audit log or the ask response, never assumed).
 * Then what each human role may do. No AI employee receives student names
 * or identifiers.
 */
export function DataAccessPanel({
  staff,
  staffError = null,
  onRetryStaff,
  grants,
  error = null,
  onRetry,
}: {
  /** Every AI employee, in the order to show them; null while loading. */
  staff: StaffEmployee[] | null
  /** The employees could not be loaded: a plain sentence, with Retry. */
  staffError?: string | null
  onRetryStaff?: () => void
  /** What each briefing employee was given on the latest run; undefined
   * for roles that do not run the briefing (no "ask a question" note). */
  grants?: AccessGrant[] | null
  /** The latest run's grants could not be loaded: a plain sentence, shown
   * with Retry instead of the "ask a question" empty text. */
  error?: string | null
  onRetry?: () => void
}) {
  return (
    <div className="account-panel">
      {/* The page's introduction comes from the page header (SidePanel's
          intro), like every other page's. */}
      <h3 className="panel-subhead">AI employees</h3>
      {staffError !== null && (
        <div className="state-error state-panel error-panel" role="alert">
          <p>Couldn't load the AI employees. {staffError}</p>
          {onRetryStaff !== undefined && (
            <button type="button" className="btn-secondary secondary" onClick={onRetryStaff}>
              Retry
            </button>
          )}
        </div>
      )}
      {staffError === null && staff === null && (
        <p className="panel-text state-empty" role="status">
          Loading the AI employees…
        </p>
      )}
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
      {staff !== null && error === null && grants !== undefined && (grants === null || grants.length === 0) && (
        <p className="panel-text state-empty">
          Ask an approved briefing question to see exactly what each briefing employee was given.
        </p>
      )}
      {staff !== null && (
        <div className="grant-list">
          {staff.map((employee) => {
            const grant = grants?.find((item) => item.role === employee.role)
            return (
              <article
                key={employee.role}
                className={`grant-card${employee.yours ? ' is-yours' : ''}`}
                aria-label={employee.title}
              >
                <p className="grant-name">
                  {employee.title}
                  {employee.yours && <span className="grant-tag grant-tag-yours">Your AI employee</span>}
                  {employee.no_data && <span className="grant-tag">No data connected yet</span>}
                </p>
                <p className="grant-office">Serves {employee.office}</p>
                <p className="grant-job">{employee.job}</p>
                <ScopeBlock
                  label="May read, as totals"
                  text={
                    employee.may_read.length > 0
                      ? employee.may_read.join('; ')
                      : employee.no_data
                        ? 'Nothing yet: no data is connected for this office.'
                        : 'No student data.'
                  }
                />
                <ScopeBlock label="Never reads" text={employee.never_reads.join('; ')} />
                <div className="grant-extras">
                {employee.outside_scope.length > 0 && (
                  <details className="fold technical-detail">
                    <summary>Outside its job ({employee.outside_scope.length})</summary>
                    <ul className="plain-list">
                      {employee.outside_scope.map((label) => (
                        <li key={label}>{label}</li>
                      ))}
                    </ul>
                  </details>
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
                <p className="grant-meta grant-count">{requestsToday(employee.requests_today)}</p>
              </article>
            )
          })}
        </div>
      )}

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
      <p className="panel-text role-table-note">{INSTRUCTOR_NAMES_NOTE}</p>
    </div>
  )
}
