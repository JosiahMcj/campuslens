import type { Finding, Findings, OfficeHolds } from '../api'
import { getFinding } from '../api'
import {
  analystSource,
  analystSourceLabel,
  findingDisplay,
  m3ThresholdLabel,
  type AnalystClaim,
  type ModelSection,
} from '../states'
import { FindingLink } from './FindingLink'
import { ChevronIcon } from './icons'

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
}: BriefingProps) {
  const m1 = getFinding(findings, 'M1')
  const m2 = getFinding(findings, 'M2')
  const m3 = getFinding(findings, 'M3')
  const m4 = getFinding(findings, 'M4')
  const m5 = getFinding(findings, 'M5')
  const m7 = getFinding(findings, 'M7')

  return (
    <article className="briefing" aria-label="Executive briefing">
      <ExecutiveSummary
        findings={findings}
        chiefSummary={chiefSummary}
        onOpenEvidence={onOpenEvidence}
      />

      <section aria-labelledby="s-measure">
        <h2 id="s-measure">2. Current measure and historical comparison</h2>
        {enrollment !== null && enrollment.kind === 'available' ? (
          <ModelClaims
            claims={enrollment.claims}
            provenance={enrollment.provenance}
            analyst="the Enrollment Analyst"
            onOpen={onOpenEvidence}
          />
        ) : (
          <p>
            Registered continuing students stand at{' '}
            <Num finding={m1} id="M1" onOpen={onOpenEvidence} /> compared with the
            equivalent date last year
            {typeof m1?.comparison?.prior_year_equivalent_date === 'string' && (
              <> ({m1.comparison.prior_year_equivalent_date})</>
            )}
            , and registered credit hours are{' '}
            <Num finding={m7} id="M7" onOpen={onOpenEvidence} /> over the same
            comparison.
          </p>
        )}
        {enrollment !== null && enrollment.kind === 'unavailable' && (
          <ModelUnavailable reason={enrollment.reason} onRetry={onCheckAgain} />
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
              Of the <Num finding={m2} id="M2" onOpen={onOpenEvidence} /> students not
              yet registered, <Num finding={m3} id="M3" onOpen={onOpenEvidence} />{' '}
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
        <OfficeTable finding={m5} onOpen={onOpenEvidence} />
      </section>

      <EvidenceSources
        findings={findings}
        fictional={fictional}
        onOpenEvidence={onOpenEvidence}
      />

      <StaffActions findings={findings} onOpenEvidence={onOpenEvidence} />
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
}: {
  findings: Findings
  fictional: boolean
  onOpenEvidence: (findingId: string) => void
  headingId?: string | null
  title?: string
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
        traces to one of the findings below, each listed with the exact
        source fields it read. Open any finding to see its formula and the
        rows behind it.
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
                <span className="finding-row-id">{id}</span>
                <span className="finding-row-title">{finding.title}</span>
                <span className="finding-row-display">{display.text}</span>
                <span className="finding-row-fields">
                  {finding.source_fields.join(', ')}
                </span>
              </button>
            </li>
          )
        })}
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
              has no students to review. The{' '}
              <FindingLink findingId="M4" onOpen={onOpenEvidence}>
                M4
              </FindingLink>{' '}
              value is not available.
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
              has no small-balance cases to review. The{' '}
              <FindingLink findingId="M3" onOpen={onOpenEvidence}>
                M3
              </FindingLink>{' '}
              value is not available.
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
            <strong>{office.office}</strong> resolves the {office.count}{' '}
            unresolved hold{office.count === 1 ? '' : 's'} recorded for that
            office (
            <FindingLink findingId="M5" onOpen={onOpenEvidence}>
              M5
            </FindingLink>
            ).
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
          <details className="technical-detail">
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
  if (chiefLimitations !== null && chiefLimitations.kind === 'available') {
    return (
      <section aria-labelledby="s-limitations">
        <h2 id="s-limitations">
          7. Known limitations, missing data, or conflicting definitions
        </h2>
        <ModelClaims
          claims={chiefLimitations.claims}
          provenance={chiefLimitations.provenance}
          analyst="the Chief of Staff"
          onOpen={onOpenEvidence}
        />
      </section>
    )
  }
  return (
    <section aria-labelledby="s-limitations">
      <h2 id="s-limitations">
        7. Known limitations, missing data, or conflicting definitions
      </h2>
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
        <li>M7 (registered credit hours vs. prior year) is an optional measure.</li>
      </ul>
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
}) {
  const source = analystSource(provenance)
  return (
    <>
      <p className={source === 'fake' ? 'analyst-source stub-tag' : 'analyst-source'}>
        {analystSourceLabel(source, analyst, provenance.model_label)}
      </p>
      {claims.map((claim, index) => (
        <p key={index}>
          {claim.text}{' '}
          {claim.finding_ids.map((id) => (
            <FindingLink key={id} findingId={id} onOpen={onOpen}>
              [{id}]
            </FindingLink>
          ))}
        </p>
      ))}
    </>
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
      {label !== null ? `under ${label}` : (finding?.title ?? 'the contracted threshold')}
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
        <button type="button" className="secondary" onClick={onRetry}>
          Check again
        </button>
      )}
      <details className="technical-detail">
        <summary>
          <ChevronIcon />
          Technical detail
        </summary>
        <p>{reason}</p>
      </details>
    </div>
  )
}
