import { useCallback, useEffect, useId, useState } from 'react'

import { friendlyError, friendlyLoadError } from '../errors'
import {
  LIST_STATUS_WORDS,
  ROW_STATUS_WORDS,
  decideOutreach,
  fetchInterventions,
  fetchOutreachRows,
  formatDifference,
  formatValue,
  prepareOutreach,
  setOutreachRow,
  type Comparison,
  type InterventionsPage as PageData,
  type OutcomeImpact,
  type OutreachRows,
  type Program,
  type RowStatus,
} from '../interventions'
import './Interventions.css'

type PageState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; data: PageData }

const ROWS_PER_PAGE = 50

/**
 * Support programs: who each one is for (a rule a person can read), what it
 * offers, how many students it reaches, and an honest look at whether it
 * works. Figures are aggregates. A list of the students a rule names is
 * prepared only by the roles allowed per-student rows, waits for a person's
 * approval, and is recorded in the audit log; nothing is sent to anyone.
 */
export function InterventionsPage() {
  const [state, setState] = useState<PageState>({ kind: 'loading' })
  const [run, setRun] = useState(0)

  useEffect(() => {
    let live = true
    fetchInterventions()
      .then((data) => live && setState({ kind: 'ready', data }))
      .catch((error: unknown) => live && setState({ kind: 'error', message: friendlyLoadError(error) }))
    return () => {
      live = false
    }
  }, [run])

  const reload = useCallback(() => setRun((n) => n + 1), [])

  if (state.kind === 'loading') {
    return (
      <p className="status-line" role="status">
        Loading the support programs…
      </p>
    )
  }
  if (state.kind === 'error') {
    return (
      <div className="state-error" role="alert">
        <p>{state.message}</p>
        <button type="button" className="secondary-button" onClick={reload}>
          Try again
        </button>
      </div>
    )
  }
  const { data } = state
  return (
    <div className="iv-page">
      <p className="iv-principle">
        Support is offered, never required. Each program names who it is for in a rule anyone
        can read, and the figures below are totals for groups of at least 10 students.
      </p>
      <nav className="iv-jump" aria-label="Programs on this page">
        {data.programs.map((p) => (
          <a key={p.id} href={`#program-${p.id}`}>
            {p.name}
          </a>
        ))}
      </nav>
      {data.programs.map((p) => (
        <ProgramCard key={p.id} program={p} termName={data.current_term_name} onChanged={reload} />
      ))}
    </div>
  )
}

function ProgramCard({
  program: p,
  termName,
  onChanged,
}: {
  program: Program
  termName: string
  onChanged: () => void
}) {
  const headingId = useId()
  const total = p.reach.total
  return (
    <section className="iv-program" id={`program-${p.id}`} aria-labelledby={headingId}>
      <header className="iv-head">
        <h2 id={headingId}>{p.name}</h2>
        <p className="iv-meta">
          Run by {p.owner_office} · offered since {p.start_term_name}
        </p>
      </header>
      <dl className="iv-terms">
        <div>
          <dt>Who it is for</dt>
          <dd>{p.rule}</dd>
        </div>
        <div>
          <dt>What is offered</dt>
          <dd>{p.offer}</dd>
        </div>
      </dl>
      <div className="iv-stats">
        <div className="iv-stat">
          <span className="iv-stat-figure">{countWords(p.reach.eligible_now)}</span>
          <span className="iv-stat-label">eligible in {termName}</span>
        </div>
        <div className="iv-stat">
          <span className="iv-stat-figure">{countWords(total.accepted)}</span>
          <span className="iv-stat-label">took part since {p.start_term_name}</span>
        </div>
        <div className="iv-stat">
          <span className="iv-stat-figure">
            {total.take_up_pct === null ? 'withheld' : `${total.take_up_pct.toFixed(1)}%`}
          </span>
          <span className="iv-stat-label">of those offered said yes</span>
        </div>
      </div>
      <ReachBars program={p} />
      <h3 className="iv-subhead">Is it working?</h3>
      {p.impact.outcomes.map((o) => (
        <OutcomePanel key={o.key} outcome={o} />
      ))}
      <div className="iv-caveat">
        <p>
          <strong>{p.impact.caveat}</strong> {p.impact.source}
        </p>
        {p.impact.planted && <p className="iv-planted">{p.impact.planted}</p>}
      </div>
      <Outreach program={p} termName={termName} onChanged={onChanged} />
    </section>
  )
}

function countWords(value: number | null): string {
  return value === null ? 'withheld' : value.toLocaleString('en-US')
}

/** Eligible and took part, each term since the program started. */
function ReachBars({ program: p }: { program: Program }) {
  const terms = p.reach.terms
  const max = Math.max(1, ...terms.map((t) => t.eligible ?? 0))
  return (
    <figure className="iv-reach">
      <figcaption>Eligible and took part, by term</figcaption>
      <ul className="iv-bars">
        {terms.map((t) => {
          const eligible = t.eligible ?? 0
          const took = t.accepted ?? 0
          return (
            <li key={t.term} className="iv-bar-row">
              <span className="iv-bar-term">{t.term_name}</span>
              <span
                className="iv-bar-track"
                role="img"
                aria-label={`${t.term_name}: ${countWords(t.eligible)} eligible, ${countWords(
                  t.accepted,
                )} took part`}
              >
                <span className="iv-bar-eligible" style={{ inlineSize: `${(100 * eligible) / max}%` }}>
                  <span
                    className="iv-bar-took"
                    style={{ inlineSize: eligible ? `${(100 * took) / eligible}%` : '0%' }}
                  />
                </span>
              </span>
              <span className="iv-bar-figure">
                {countWords(t.accepted)} of {countWords(t.eligible)}
              </span>
            </li>
          )
        })}
      </ul>
      <p className="iv-legend">
        <span className="iv-key iv-key-took" aria-hidden="true" /> took part
        <span className="iv-key iv-key-eligible" aria-hidden="true" /> eligible, did not
      </p>
    </figure>
  )
}

const METHOD_SHORT: Record<Comparison['method'], string> = {
  before_after: 'Before and after it started',
  naive: 'Took part or not (naive)',
  matched: 'Took part or not, similar GPA (fairer)',
}

function OutcomePanel({ outcome: o }: { outcome: OutcomeImpact }) {
  const shown = o.comparisons.filter((c) => !c.withheld && c.low !== null && c.high !== null)
  const reach = Math.max(
    o.kind === 'pct' ? 2 : 0.05,
    ...shown.flatMap((c) => [Math.abs(c.low ?? 0), Math.abs(c.high ?? 0)]),
  )
  const scale = (v: number) => 50 + (45 * v) / reach
  const verdictClass = o.verdict.startsWith('Likely')
    ? 'iv-verdict iv-verdict-good'
    : o.verdict.startsWith('Worth')
      ? 'iv-verdict iv-verdict-bad'
      : 'iv-verdict'
  return (
    <div className="iv-outcome">
      <h4>
        {o.label} <span className="iv-among">({o.among})</span>
      </h4>
      <p className={verdictClass}>{o.verdict}</p>
      <ul className="iv-comparisons">
        {o.comparisons.map((c) => (
          <li key={c.method} className={c.method === 'matched' ? 'iv-cmp iv-cmp-fair' : 'iv-cmp'}>
            <div className="iv-cmp-text">
              <span className="iv-cmp-label">{METHOD_SHORT[c.method]}</span>
              {c.withheld ? (
                <span className="iv-cmp-values">Withheld: a group is under 10 students.</span>
              ) : (
                <span className="iv-cmp-values">
                  {c.a.label} {formatValue(c.a.value, o.kind)} vs {lowerFirst(c.b.label)}{' '}
                  {formatValue(c.b.value, o.kind)}:{' '}
                  <strong>{formatDifference(c.difference, o.kind)}</strong>
                  <span className="iv-range">
                    {' '}
                    (likely {formatDifference(c.low, o.kind)} to {formatDifference(c.high, o.kind)})
                  </span>
                </span>
              )}
              <span className="iv-cmp-method">{c.sentence}</span>
            </div>
            {!c.withheld && c.low !== null && c.high !== null && c.difference !== null && (
              <svg
                className="iv-interval"
                viewBox="0 0 100 12"
                preserveAspectRatio="none"
                role="img"
                aria-label={`Difference ${formatDifference(c.difference, o.kind)}, likely between ${formatDifference(
                  c.low,
                  o.kind,
                )} and ${formatDifference(c.high, o.kind)}`}
              >
                <line className="iv-zero" x1="50" x2="50" y1="0" y2="12" />
                <line className="iv-whisker" x1={scale(c.low)} x2={scale(c.high)} y1="6" y2="6" />
                <rect className="iv-point" x={scale(c.difference) - 1.2} y="2.5" width="2.4" height="7" rx="1" />
              </svg>
            )}
          </li>
        ))}
      </ul>
      <p className="iv-axis-note">
        Each bar is the difference with its likely range; the middle line is no difference.
      </p>
    </div>
  )
}

/** The governed list of students the rule names this term. */
function Outreach({
  program: p,
  termName,
  onChanged,
}: {
  program: Program
  termName: string
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [rows, setRows] = useState<OutreachRows | null>(null)
  const list = p.outreach

  if (!p.can_prepare && !p.can_decide) {
    return (
      <p className="iv-outreach-note">
        Lists of the students a rule names are prepared by the {p.owner_office} office or
        leadership, approved by a person, and recorded in the audit log.
      </p>
    )
  }

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      onChanged()
    } catch (e) {
      setError(friendlyError(e, 'The outreach list'))
    } finally {
      setBusy(false)
    }
  }

  const openRows = async () => {
    if (list === null) return
    setBusy(true)
    setError(null)
    try {
      setRows(await fetchOutreachRows(list.id))
    } catch (e) {
      setError(friendlyError(e, 'The outreach list'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="iv-outreach">
      <h3 className="iv-subhead">Outreach for {termName}</h3>
      {list === null ? (
        p.can_prepare ? (
          <>
            <p>
              Prepare the list of students this rule names in {termName}. It waits for approval
              by the president or an administrator, and nothing is sent to any student.
            </p>
            <button
              type="button"
              className="primary-button btn-primary"
              disabled={busy}
              onClick={() => void act(() => prepareOutreach(p.id))}
            >
              Prepare outreach list
            </button>
          </>
        ) : (
          <p>No outreach list has been prepared for {termName} yet.</p>
        )
      ) : (
        <>
          <p>
            <span className={`iv-badge iv-badge-${list.status}`}>{LIST_STATUS_WORDS[list.status]}</span>{' '}
            {list.count.toLocaleString('en-US')} students, prepared by {list.prepared_by}
            {list.decided_by ? `; ${list.status} by ${list.decided_by}` : ''}.
          </p>
          <div className="iv-actions">
            {list.status === 'pending_approval' && p.can_decide && (
              <>
                <button
                  type="button"
                  className="primary-button btn-primary"
                  disabled={busy}
                  onClick={() => void act(() => decideOutreach(list.id, 'approve'))}
                >
                  Approve list
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={busy}
                  onClick={() => void act(() => decideOutreach(list.id, 'decline'))}
                >
                  Decline
                </button>
              </>
            )}
            {p.can_prepare && rows === null && (
              <button type="button" className="secondary-button" disabled={busy} onClick={() => void openRows()}>
                Open the list
              </button>
            )}
          </div>
          {list.status === 'approved' && (
            <p className="iv-outreach-note">
              Approved: staff reach out in person and record how it went. CampusLens sends nothing.
            </p>
          )}
        </>
      )}
      {error && (
        <p className="iv-error" role="alert">
          {error}
        </p>
      )}
      {rows && <OutreachTable data={rows} editable={rows.list.status === 'approved'} />}
    </div>
  )
}

function OutreachTable({ data, editable }: { data: OutreachRows; editable: boolean }) {
  const [shown, setShown] = useState(ROWS_PER_PAGE)
  const [statuses, setStatuses] = useState<Record<number, RowStatus>>({})
  const [error, setError] = useState<string | null>(null)
  const save = async (rowId: number, status: RowStatus) => {
    setError(null)
    try {
      await setOutreachRow(data.list.id, rowId, status)
      setStatuses((s) => ({ ...s, [rowId]: status }))
    } catch (e) {
      setError(friendlyError(e, 'The outreach status'))
    }
  }
  return (
    <div className="iv-table-wrap">
      <p className="iv-outreach-note">
        Opening this list was recorded in the audit log. Students are shown by their
        pseudonymous id with only the facts the rule used.
      </p>
      {error && (
        <p className="iv-error" role="alert">
          {error}
        </p>
      )}
      <table className="iv-table">
        <thead>
          <tr>
            <th scope="col">Student</th>
            {data.fact_labels.map((f) => (
              <th scope="col" key={f.key}>
                {f.label}
              </th>
            ))}
            <th scope="col">Outreach</th>
          </tr>
        </thead>
        <tbody>
          {data.rows.slice(0, shown).map((r) => {
            const status = statuses[r.id] ?? r.status
            return (
              <tr key={r.id}>
                <td className="iv-mono">{r.student_id}</td>
                {data.fact_labels.map((f) => (
                  <td key={f.key}>{factText(r.facts[f.key])}</td>
                ))}
                <td>
                  {editable ? (
                    <select
                      aria-label={`Outreach for ${r.student_id}`}
                      value={status}
                      onChange={(e) => void save(r.id, e.target.value as RowStatus)}
                    >
                      {data.statuses.map((s) => (
                        <option key={s} value={s}>
                          {ROW_STATUS_WORDS[s]}
                        </option>
                      ))}
                    </select>
                  ) : (
                    ROW_STATUS_WORDS[status]
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="iv-count">
        Showing {Math.min(shown, data.rows.length).toLocaleString('en-US')} of{' '}
        {data.rows.length.toLocaleString('en-US')}.
        {shown < data.rows.length && (
          <button type="button" className="link-button" onClick={() => setShown((n) => n + ROWS_PER_PAGE)}>
            Show {ROWS_PER_PAGE} more
          </button>
        )}
      </p>
    </div>
  )
}

function factText(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number') return Number.isInteger(value) ? value.toLocaleString('en-US') : value.toFixed(2)
  return value
}

function lowerFirst(text: string): string {
  return text.charAt(0).toLowerCase() + text.slice(1)
}
