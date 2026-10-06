import type { Decision, Finding, Findings, OfficeHolds } from '../api'
import { getFinding } from '../api'
import {
  analystSource,
  findingDisplay,
  m3ThresholdLabel,
  type AnalystClaim,
  type AnalystSource,
  type ModelSection,
} from '../states'
import { authorizedSourceLabel, suppressionNote } from '../counseling'
import { fieldLabels } from '../fieldLabels'
import { findingLabel, linkClaimNumbers } from '../findingLabels'
import { FindingLink } from './FindingLink'
import { ChevronIcon } from './icons'

/** A figure's display text by id, for linking the numbers in model text. */
type DisplayOf = (findingId: string) => string

function displayLookup(findings: Findings, counselingFigure: Finding | null = null): DisplayOf {
  return (id) => {
    const finding = id === 'M9' ? (counselingFigure ?? undefined) : getFinding(findings, id)
    return findingDisplay(finding ?? {}).text
  }
}

/**
 * Who wrote a model-written section, in one short line: "Written by the
 * Chief of Staff". The test stub never passes as a model. (Group B's
 * states.ts carries the same wording as analystSourceLabel after the merge.)
 */
function sourceLine(source: AnalystSource, analyst: string): string {
  return source === 'fake' ? 'Test stub, not a live model' : `Written by ${analyst}`
}

/** The replay or live fact, for the small "About this answer" detail. */
function sourceDetail(source: AnalystSource, modelLabel: string | null): string {
  switch (source) {
    case 'fake':
      return 'This text comes from a test stub, not a live model.'
    case 'recorded':
      return 'This text was written during a recorded live run of the Cabinet and is replayed here. Every number in it was checked against the data before it was shown.'
    case 'live':
      return `This text was written just now (${
        modelLabel !== null && modelLabel !== '' ? modelLabel : 'live model'
      }). Every number in it was checked against the data before it was shown.`
  }
}

interface BriefingProps {
  findings: Findings
  /** True while the active dataset is the fictional demonstration set: the
   * copy keeps saying "fictional"; a real upload drops the word. */
  fictional: boolean
  /**
   * Section 2 from the produced briefing (POST /ask or GET /briefing); null
   * before the first Ask, when the computed summary renders with no analyst
   * source label — the page never runs the analysts on load.
   */
  enrollment: ModelSection | null
  /** Section 3 from the produced briefing; same rule as `enrollment`. */
  studentSuccess: ModelSection | null
  /**
   * Section 1 from the produced briefing (POST /ask or GET /briefing); null
   * on first load or under ?model=down, when the computed headline renders.
   */
  chiefSummary: ModelSection | null
  /** "Check again" re-runs the question (POST /ask), the only place the
   * analysts and the Chief of Staff ever run. Null for roles that may not
   * ask (staff, reviewer): the button is not offered. */
  onCheckAgain: (() => void) | null
  onOpenEvidence: (findingId: string) => void
  /**
   * M9, the authorized counseling count, from the produced briefing's own
   * `aggregates` (never the current findings). Null when the briefing did
   * not carry it: before the first Ask, under another question, or on a
   * briefing produced while the authorization was off.
   */
  counselingFigure?: Finding | null
  /** Opens the Staff actions panel: section 5 links there instead of
   * repeating it. Omitted, the section names the sidebar entry instead. */
  onOpenStaffActions?: () => void
}

/**
 * The seven-section executive briefing (Beat 3), in PROPOSAL.md's "Executive
 * Briefing Format" order and wording: 1 executive summary, 2 current measure
 * and historical comparison, 3 student groups most affected, 4 evidence and
 * source fields, 5 operational actions — then the leadership decisions,
 * rendered separately by the parent (DecisionPanel), and 7 known limitations
 * (`Limitations`, rendered after the decision panel). Every number is a
 * FindingLink that opens the evidence drawer. Sections 1, 2, 3 render the
 * Chief of Staff's and the analysts' validated claims when a produced
 * briefing exists; before the first Ask each falls back to the computed
 * summary of its findings (never invented text, no analyst source label),
 * and an unavailable section adds the "model unavailable" note above that
 * fallback.
 */
export function BriefingSections({
  findings,
  fictional,
  enrollment,
  studentSuccess,
  chiefSummary,
  onCheckAgain,
  onOpenEvidence,
  counselingFigure = null,
  onOpenStaffActions,
}: BriefingProps) {
  const m2 = getFinding(findings, 'M2')
  const m3 = getFinding(findings, 'M3')
  const m4 = getFinding(findings, 'M4')
  const m5 = getFinding(findings, 'M5')
  const displayOf = displayLookup(findings, counselingFigure)

  return (
    <article className="briefing" aria-label="Executive briefing">
      <ExecutiveSummary
        findings={findings}
        chiefSummary={chiefSummary}
        onOpenEvidence={onOpenEvidence}
      />

      <section aria-labelledby="s-measure">
        <h2 id="s-measure">2. Current measure and historical comparison</h2>
        <ComparisonTable findings={findings} onOpen={onOpenEvidence} />
        {enrollment !== null && enrollment.kind === 'available' && (
          <details className="fold technical-detail">
            <summary>
              <ChevronIcon />
              Show the Enrollment Analyst's explanation
            </summary>
            <ModelClaims
              claims={enrollment.claims}
              provenance={enrollment.provenance}
              analyst="the Enrollment Analyst"
              onOpen={onOpenEvidence}
              displayOf={displayOf}
            />
          </details>
        )}
      </section>

      <section aria-labelledby="s-groups">
        <h2 id="s-groups">3. Student groups most affected</h2>
        {studentSuccess !== null && studentSuccess.kind === 'available' ? (
          <ModelClaims
            claims={studentSuccess.claims}
            provenance={studentSuccess.provenance}
            analyst="the Student Success Analyst"
            onOpen={onOpenEvidence}
            displayOf={displayOf}
          />
        ) : (
          <>
            <p>
              Most of the gap is among the{' '}
              <Num finding={m2} id="M2" onOpen={onOpenEvidence} /> continuing students
              who have not yet registered for spring. Each one is a person who may
              need support: a conversation, a payment plan, a cleared hold. Each
              appears, pseudonymously, in the evidence behind that number.
            </p>
            <p>
              Of those students, <Num finding={m3} id="M3" onOpen={onOpenEvidence} />{' '}
              carry an unresolved financial hold (
              <M3Threshold finding={m3} onOpen={onOpenEvidence} />), the kind a
              payment plan or a focused aid review often clears, and{' '}
              <Num finding={m4} id="M4" onOpen={onOpenEvidence} /> have not met with an
              advisor this term.
            </p>
          </>
        )}
        {studentSuccess !== null && studentSuccess.kind === 'unavailable' && (
          <ModelUnavailable reason={studentSuccess.reason} onRetry={onCheckAgain} />
        )}
        <CounselingAggregate
          finding={counselingFigure ?? undefined}
          m2={m2}
          onOpen={onOpenEvidence}
        />
        <OfficeTable finding={m5} onOpen={onOpenEvidence} />
      </section>

      <EvidenceSources
        findings={findings}
        fictional={fictional}
        onOpenEvidence={onOpenEvidence}
        counselingFigure={counselingFigure}
      />

      <section aria-labelledby="s-actions">
        <h2 id="s-actions">5. Operational actions</h2>
        <p>
          Staff can act on these figures now, each action with a responsible office
          and no leadership approval needed.{' '}
          {onOpenStaffActions !== undefined ? (
            <button type="button" className="link-button" onClick={onOpenStaffActions}>
              Open Staff actions
            </button>
          ) : (
            <>They are listed under Staff actions in the sidebar.</>
          )}
        </p>
      </section>
    </article>
  )
}

/**
 * Section 4, evidence and source fields: every finding with the exact
 * source fields it read, each opening the evidence drawer. The briefing
 * renders it as section 4; the sidebar's Evidence panel renders it alone,
 * with `headingId` null so the page never carries a duplicate id.
 */
export function EvidenceSources({
  findings,
  fictional,
  onOpenEvidence,
  headingId = 's-evidence',
  title = '4. Evidence and source fields',
  counselingFigure = null,
}: {
  findings: Findings
  fictional: boolean
  onOpenEvidence: (findingId: string) => void
  headingId?: string | null
  title?: string
  /** M9 from the produced briefing, listed last when present. */
  counselingFigure?: Finding | null
}) {
  return (
    <section
      aria-labelledby={headingId ?? undefined}
      aria-label={headingId === null ? title : undefined}
    >
      <h2 id={headingId ?? undefined}>{title}</h2>
      <p>
        Every number in this briefing is computed from{' '}
        {fictional ? 'fictional source data' : "your institution's source data"} and
        traces to one of the figures below, each listed with the fields it
        reads. Open any figure to see how it is computed and the records
        behind it.
      </p>
      <ul className="finding-list">
        {(['M1', 'M2', 'M3', 'M4', 'M5', 'M6', 'M7', 'M8'] as const).map((id) => {
          const finding = getFinding(findings, id)
          if (!finding) return null
          const display = findingDisplay(finding)
          return (
            <li key={id}>
              <button
                type="button"
                className="finding-row"
                onClick={() => onOpenEvidence(id)}
              >
                <span className="finding-row-title">{findingLabel(id, finding.title)}</span>
                <span className="finding-row-display">{display.text}</span>
                <span className="finding-row-fields">
                  Reads: {fieldLabels(finding.source_fields).join(', ')}
                </span>
              </button>
            </li>
          )
        })}
        {counselingFigure !== null && (
          <li key="M9">
            <button
              type="button"
              className="finding-row"
              onClick={() => onOpenEvidence('M9')}
            >
              <span className="finding-row-title">{findingLabel('M9')}</span>
              <span className="finding-row-display">
                {findingDisplay(counselingFigure).text}
              </span>
              <span className="finding-row-fields">
                {capitalize(authorizedSourceLabel(counselingFigure))}. No list of
                students is shown for this figure.
              </span>
            </button>
          </li>
        )}
      </ul>
    </section>
  )
}

/**
 * Section 5, operational actions: what staff can do now, each with its
 * responsible office, no approval needed. Rendered by the briefing and, on
 * its own, by the sidebar's Staff actions panel.
 */
export function StaffActions({
  findings,
  onOpenEvidence,
  headingId = 's-actions',
  title = '5. Operational actions',
}: {
  findings: Findings
  onOpenEvidence: (findingId: string) => void
  headingId?: string | null
  title?: string
}) {
  const m3 = getFinding(findings, 'M3')
  const m4 = getFinding(findings, 'M4')
  const m5 = getFinding(findings, 'M5')
  return (
    <section
      className="staff-plan"
      aria-labelledby={headingId ?? undefined}
      aria-label={headingId === null ? title : undefined}
    >
      <h2 id={headingId ?? undefined}>{title}</h2>
      <p>
        Staff can take these actions now, each with a responsible office.
        They need no leadership approval, and nothing here is sent
        automatically.
      </p>
      <ul className="action-list">
        <li>
          <strong>Student Success</strong>{' '}
          {findingDisplay(m4 ?? {}).missing ? (
            <>
              has no students to review: the figure for students with no
              advising appointment is not available.
            </>
          ) : (
            <>
              reviews the <Num finding={m4} id="M4" onOpen={onOpenEvidence} />{' '}
              students with no advising contact this term.
            </>
          )}
        </li>
        <li>
          <strong>Financial Aid</strong>{' '}
          {findingDisplay(m3 ?? {}).missing ? (
            <>
              has no small-balance cases to review: the figure for students with
              a small hold is not available.
            </>
          ) : (
            <>
              reviews the <Num finding={m3} id="M3" onOpen={onOpenEvidence} />{' '}
              small-balance cases (
              <M3Threshold finding={m3} onOpen={onOpenEvidence} />
              ).
            </>
          )}
        </li>
        {officeRows(m5).map((office) => (
          <li key={office.office}>
            <strong>{office.office}</strong> resolves the{' '}
            <FindingLink findingId="M5" onOpen={onOpenEvidence}>
              <span className="num">{office.count}</span>
            </FindingLink>{' '}
            unresolved hold{office.count === 1 ? '' : 's'} recorded for that
            office.
          </li>
        ))}
      </ul>
    </section>
  )
}

/**
 * Section 1, the executive summary: the Chief of Staff's validated claims
 * when a produced briefing exists, else the computed headline. The briefing
 * renders it as section 1 (heading id s-summary); the chat reply renders the
 * same content under its own heading, with `headingId` null so the page never
 * carries a duplicate id.
 */
export function ExecutiveSummary({
  findings,
  chiefSummary,
  onOpenEvidence,
  headingId = 's-summary',
  title = '1. Executive summary',
}: {
  findings: Findings
  chiefSummary: ModelSection | null
  onOpenEvidence: (findingId: string) => void
  headingId?: string | null
  title?: string
}) {
  const m1 = getFinding(findings, 'M1')
  const m2 = getFinding(findings, 'M2')
  const m6 = getFinding(findings, 'M6')
  return (
    <section
      aria-labelledby={headingId ?? undefined}
      aria-label={headingId === null ? title : undefined}
    >
      <h2 id={headingId ?? undefined}>{title}</h2>
      {chiefSummary !== null && chiefSummary.kind === 'available' ? (
        <ModelClaims
          claims={chiefSummary.claims}
          provenance={chiefSummary.provenance}
          analyst="the Chief of Staff"
          onOpen={onOpenEvidence}
          displayOf={displayLookup(findings)}
        />
      ) : (
        <p className="headline-text">
          Spring registration is <Num finding={m1} id="M1" onOpen={onOpenEvidence} />{' '}
          versus the same date last year, and{' '}
          <Num finding={m2} id="M2" onOpen={onOpenEvidence} /> continuing students have
          not yet registered, people who may need support.{' '}
          {m6?.closed === true ? (
            <>Registration for the spring term has closed.</>
          ) : (
            <>
              Registration closes in{' '}
              <Num finding={m6} id="M6" onOpen={onOpenEvidence} suffix=" days" />.
            </>
          )}
        </p>
      )}
      {chiefSummary !== null && chiefSummary.kind === 'unavailable' && (
        <div className="model-unavailable" role="status">
          <h3>Model unavailable</h3>
          <p>
            The Chief of Staff's written summary is unavailable right now.
            The headline above is computed from the data and remains fully
            evidenced.
          </p>
          <details className="fold technical-detail">
            <summary>
              <ChevronIcon />
              Technical detail
            </summary>
            <p>{chiefSummary.reason}</p>
          </details>
        </div>
      )}
    </section>
  )
}

/**
 * Section 7, rendered after the leadership decision panel so the on-screen
 * order follows PROPOSAL.md. When a produced briefing exists it renders the
 * Chief of Staff's validated limitations claims (with finding links and the
 * same honest source labels as the analysts); otherwise the static fallback
 * below states the real limits of the prototype. The as-of date comes from
 * the findings' meta block, never from "today".
 */
export function Limitations({
  findings,
  fictional,
  chiefLimitations,
  onOpenEvidence,
}: {
  findings: Findings
  /** True while the active dataset is the fictional demonstration set. */
  fictional: boolean
  /** Section 7 from the produced briefing; null before the first Ask. */
  chiefLimitations: ModelSection | null
  onOpenEvidence: (findingId: string) => void
}) {
  const heading = (
    <h2 id="s-limitations">7. Known limitations, missing data, or conflicting definitions</h2>
  )
  if (chiefLimitations !== null && chiefLimitations.kind === 'available') {
    return (
      <section aria-labelledby="s-limitations">
        {heading}
        <details className="fold technical-detail">
          <summary>
            <ChevronIcon />
            Show the known limitations ({chiefLimitations.claims.length})
          </summary>
          <ModelClaims
            claims={chiefLimitations.claims}
            provenance={chiefLimitations.provenance}
            analyst="the Chief of Staff"
            onOpen={onOpenEvidence}
            displayOf={displayLookup(findings)}
          />
        </details>
      </section>
    )
  }
  return (
    <section aria-labelledby="s-limitations">
      {heading}
      <details className="fold technical-detail">
        <summary>
          <ChevronIcon />
          Show the known limitations (5)
        </summary>
      <ul className="limitations-list">
        <li>
          {fictional
            ? 'All records are fictional demonstration data. No real student data is involved.'
            : "Records come from the institution's uploaded export and are pseudonymous."}
        </li>
        <li>
          The as-of date ({findings.meta.as_of ?? 'unknown'}) is taken from the data,
          not from today's date.
        </li>
        <li>
          The analysts' written explanations in sections 2 and 3 are
          model-generated and checked against the findings. Any number that is
          not in the findings fails validation and is never shown.
        </li>
        <li>
          After the approved question is asked, the Chief of Staff's written
          summary (section 1) and its stated limitations replace this list;
          both are validated against the findings the same way.
        </li>
        <li>{findingLabel('M7')} is an optional measure.</li>
      </ul>
      </details>
    </section>
  )
}

/**
 * A section's validated claims, each with its FindingLink evidence links.
 * The response's raw `text` is never rendered: it duplicates the claims and
 * carries the bracketed finding IDs meant for the validator, not the reader.
 * The source label comes from the response's provider/model_label/recorded
 * fields so a recorded run or the test stub can never pass as a live model,
 * and no model id is ever rendered. Used for the analysts' sections (2 and
 * 3) and the Chief of Staff's (1 and 7).
 */
function ModelClaims({
  claims,
  provenance,
  analyst,
  onOpen,
  displayOf,
}: {
  claims: AnalystClaim[]
  provenance: {
    provider: string | null
    model_label: string | null
    recorded: boolean
  }
  /** Who the source label names, e.g. "the Student Success Analyst". */
  analyst: string
  onOpen: (findingId: string) => void
  displayOf: DisplayOf
}) {
  const source = analystSource(provenance)
  return (
    <>
      <p className={source === 'fake' ? 'analyst-source stub-tag' : 'analyst-source'}>
        {sourceLine(source, analyst)}
      </p>
      {claims.map((claim, index) => (
        <ClaimText key={index} claim={claim} onOpen={onOpen} displayOf={displayOf} />
      ))}
      <details className="fold technical-detail about-answer">
        <summary>
          <ChevronIcon />
          About this answer
        </summary>
        <p>{sourceDetail(source, provenance.model_label)}</p>
      </details>
    </>
  )
}

/**
 * One validated claim: the cited figures' numbers inside the sentence are
 * their evidence links. A cited figure whose number is not in the sentence
 * gets one small "evidence" link after it (never a bracketed code).
 */
function ClaimText({
  claim,
  onOpen,
  displayOf,
}: {
  claim: AnalystClaim
  onOpen: (findingId: string) => void
  displayOf: DisplayOf
}) {
  const { parts, unmatched } = linkClaimNumbers(
    claim.text,
    claim.finding_ids.map((id) => ({ id, display: displayOf(id) })),
  )
  return (
    <p>
      {parts.map((part, index) =>
        part.kind === 'text' ? (
          part.text
        ) : (
          <FindingLink key={index} findingId={part.findingId} onOpen={onOpen}>
            <span className="num">{part.text}</span>
          </FindingLink>
        ),
      )}
      {/[.!?]$/.test(claim.text.trim()) ? '' : '.'}
      {unmatched.map((id) => (
        <span key={id}>
          {' '}
          <FindingLink findingId={id} onOpen={onOpen} className="evidence-tag">
            Evidence
          </FindingLink>
        </span>
      ))}
    </p>
  )
}

/** M5's per-office rows, or none when the finding is missing/malformed. */
function officeRows(finding: Finding | undefined): OfficeHolds[] {
  return Array.isArray(finding?.value) ? (finding.value as OfficeHolds[]) : []
}

/**
 * The M3 balance threshold, read from the finding's
 * `comparison.threshold_usd` and rendered as "under $X". When the field is
 * absent, the finding's title text is shown instead of a number — the UI
 * never hardcodes a threshold.
 */
function M3Threshold({
  finding,
  onOpen,
}: {
  finding: Finding | undefined
  onOpen: (findingId: string) => void
}) {
  const label = m3ThresholdLabel(finding ?? {})
  return (
    <FindingLink findingId="M3" onOpen={onOpen}>
      {label !== null ? `under ${label}` : 'under the threshold'}
    </FindingLink>
  )
}

/** A finding's display string, rendered verbatim, linked to its evidence. */
function Num({
  finding,
  id,
  onOpen,
  suffix = '',
}: {
  finding: Finding | undefined
  id: string
  onOpen: (findingId: string) => void
  suffix?: string
}) {
  const display = findingDisplay(finding ?? {})
  return (
    <FindingLink findingId={id} onOpen={onOpen}>
      <span className={display.missing ? 'num missing' : 'num'}>
        {display.text}
        {suffix}
      </span>
    </FindingLink>
  )
}

/**
 * M9, the authorized counseling aggregate, in section 3. Rendered only when
 * the findings carry it (the institution recorded the counseling director's
 * written authorization). It is computed in code, never written by a model,
 * so its source label names the authorization instead of an analyst. A small
 * count is withheld, and the sentence says so plainly.
 */
function CounselingAggregate({
  finding,
  m2,
  onOpen,
}: {
  finding: Finding | undefined
  m2: Finding | undefined
  onOpen: (findingId: string) => void
}) {
  if (finding === undefined) return null
  const note = suppressionNote(finding)
  return (
    <div className="aggregate-figure">
      <p className="analyst-source">{capitalize(authorizedSourceLabel(finding))}</p>
      <p>
        Of the <Num finding={m2} id="M2" onOpen={onOpen} /> students not yet
        registered, <Num finding={finding} id="M9" onOpen={onOpen} /> have had
        any counseling contact this term.{note !== null && <> {note}</>}
      </p>
    </div>
  )
}

function OfficeTable({
  finding,
  onOpen,
}: {
  finding: Finding | undefined
  onOpen: (findingId: string) => void
}) {
  const offices = officeRows(finding)
  if (offices.length === 0) {
    return <p className="hint">No unresolved holds.</p>
  }
  return (
    <table className="office-table">
      <caption>
        Unresolved holds by office: <Num finding={finding} id="M5" onOpen={onOpen} />
      </caption>
      <thead>
        <tr>
          <th scope="col">Office</th>
          <th scope="col">Unresolved holds</th>
        </tr>
      </thead>
      <tbody>
        {offices.map((office) => (
          <tr key={office.office}>
            <td>{office.office}</td>
            <td>{office.count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ModelUnavailable({
  reason,
  onRetry,
}: {
  reason: string
  /** Null for roles that may not re-ask the question (staff, reviewer). */
  onRetry: (() => void) | null
}) {
  return (
    <div className="model-unavailable" role="status">
      <h3>Model unavailable</h3>
      <p>
        The analyst's written explanation is unavailable right now. Every number
        on this page is computed from the data and remains fully evidenced.
      </p>
      {onRetry !== null && (
        <button type="button" className="btn-secondary secondary" onClick={onRetry}>
          Check again
        </button>
      )}
      <details className="fold technical-detail">
        <summary>
          <ChevronIcon />
          Technical detail
        </summary>
        <p>{reason}</p>
      </details>
    </div>
  )
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/**
 * Section 2's comparison: each measure, its change, and what it is compared
 * with (the prior-year value and its date), read from the finding's own
 * comparison block. Section 1 says the headline; this table is what it adds.
 */
function ComparisonTable({
  findings,
  onOpen,
}: {
  findings: Findings
  onOpen: (findingId: string) => void
}) {
  const rows: { id: 'M1' | 'M7'; measure: string; baseKey: string; unit: string }[] = [
    {
      id: 'M1',
      measure: 'Continuing students registered',
      baseKey: 'prior_year_registered_continuing',
      unit: 'students',
    },
    {
      id: 'M7',
      measure: 'Registered credit hours',
      baseKey: 'prior_year_registered_credit_hours',
      unit: 'credit hours',
    },
  ]
  const present = rows.filter((row) => getFinding(findings, row.id) !== undefined)
  if (present.length === 0) return null
  return (
    <table className="office-table comparison-table">
      <caption>Now compared with the same date last year</caption>
      <thead>
        <tr>
          <th scope="col">Measure</th>
          <th scope="col">Change</th>
          <th scope="col">Same date last year</th>
        </tr>
      </thead>
      <tbody>
        {present.map((row) => {
          const finding = getFinding(findings, row.id)
          const comparison = finding?.comparison ?? null
          const base = comparison?.[row.baseKey]
          const date = comparison?.prior_year_equivalent_date
          return (
            <tr key={row.id}>
              <th scope="row">{row.measure}</th>
              <td>
                <Num finding={finding} id={row.id} onOpen={onOpen} />
              </td>
              <td>
                {typeof base === 'number'
                  ? `${base.toLocaleString('en-US')} ${row.unit}`
                  : 'Not recorded'}
                {typeof date === 'string' && <> ({date})</>}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

/**
 * Section 6 in the full briefing: the decision's title and where it stands,
 * with one way to the decision itself (the Decision panel), instead of
 * repeating it.
 */
export function DecisionSection({
  decisions,
  onOpenDecision,
}: {
  decisions: Decision[] | null
  onOpenDecision: () => void
}) {
  return (
    <section aria-labelledby="s-decision-note">
      <h2 id="s-decision-note">6. Leadership decisions</h2>
      {decisions === null ? (
        <p className="status-line">Loading the decision…</p>
      ) : decisions.length === 0 ? (
        <p>No leadership decision is waiting for this question.</p>
      ) : (
        <ul className="plain-list decision-summary">
          {decisions.map((decision) => (
            <li key={decision.id}>
              <strong>{decision.title}</strong>
              <br />
              {decision.approved ? 'Approved.' : 'Waiting for leadership approval.'}
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="btn-secondary secondary" onClick={onOpenDecision}>
        Open the decision
      </button>
    </section>
  )
}
