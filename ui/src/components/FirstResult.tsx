import type { Decision, DispatchInfo, Findings, OfficeHolds } from '../api'
import { getFinding } from '../api'
import type { Role } from '../auth'
import { formatIsoDate, tidyNumbers } from '../displayFormat'
import { findingDisplay } from '../states'
import { FindingLink } from './FindingLink'
import { StatRow } from './StatRow'
import './FirstResult.css'

/** The student-support counts. The registration change is the sentence
 * above them, so it is not repeated as a card. */
const SUPPORT_IDS = ['M2', 'M3', 'M4', 'M8'] as const
/** The unresolved-holds question's own findings: small-balance holds and the
 * days left to register (the total and the by-office rows come from M5). */
const HOLDS_IDS = ['M3', 'M6'] as const
const HOLDS_QUESTION_ID = 'unresolved-holds'
const REGISTRATION_SCOPE = 'Spring registration window · continuing students not yet registered'
const HOLDS_SCOPE = 'Unresolved holds affecting continued enrollment'

/**
 * The first thing an answer shows, before the long summary: the registration
 * finding with the period it is compared against, the student-support
 * counts (each opens its evidence), and the next step a person owns. Two
 * room-scale actions close it: "View evidence" and "Review next steps".
 *
 * Nothing here is computed in the UI. Every number is a finding's `display`
 * string, the dates come from the findings, and the proposed deadline comes
 * from the API (fictional dataset only).
 */
export function FirstResult({
  findings,
  fictional,
  role,
  decision,
  dispatch,
  questionId = 'spring-registration',
  onOpenEvidence,
  onReviewNextSteps,
}: {
  findings: Findings
  fictional: boolean
  role: Role
  /** The leadership decision for this answer; null while it loads. */
  decision: Decision | null
  dispatch: DispatchInfo | null
  /** The registry id of the question this answer belongs to. */
  questionId?: string
  onOpenEvidence: (findingId: string) => void
  onReviewNextSteps: () => void
}) {
  const m1 = getFinding(findings, 'M1')
  const display = m1 !== undefined ? findingDisplay(m1) : null
  const asOf = findings.meta.as_of
  const priorDate =
    m1?.comparison?.prior_year_equivalent_date ??
    findings.meta.terms.prior_year_equivalent_date
  const holds = questionId === HOLDS_QUESTION_ID
  const m5 = getFinding(findings, 'M5')
  const m5Display = m5 !== undefined ? findingDisplay(m5) : null
  const offices = Array.isArray(m5?.value) ? (m5.value as OfficeHolds[]) : []
  const proposedDue = dispatch?.proposed_due ?? null
  return (
    <section className="first-result" aria-labelledby="first-result-title">
      <div className="first-result-head">
        <h2 id="first-result-title">Main findings</h2>
        {fictional && <span className="data-tag">Fictional data</span>}
      </div>

      <p className="first-result-scope">{holds ? HOLDS_SCOPE : REGISTRATION_SCOPE}</p>

      {holds ? (
        <>
          {m5Display !== null && (
            <p className="first-result-finding">
              <FindingLink findingId="M5" onOpen={onOpenEvidence}>
                {tidyNumbers(m5Display.text)}
              </FindingLink>
              {offices.length > 0 && (
                <>
                  {' '}
                  across {offices.length} {offices.length === 1 ? 'office' : 'offices'}:{' '}
                  {offices.map((o) => `${o.office} ${o.count}`).join(', ')}.
                </>
              )}
            </p>
          )}
          <StatRow
            findings={findings}
            onOpenEvidence={onOpenEvidence}
            ids={HOLDS_IDS}
            label="Unresolved-holds counts"
          />
        </>
      ) : (
        <>
          {display !== null && (
            <p className="first-result-finding">
              Spring registration is{' '}
              <FindingLink findingId="M1" onOpen={onOpenEvidence}>
                {tidyNumbers(display.text)}
              </FindingLink>{' '}
              against the same point last year.
            </p>
          )}
          {asOf != null && typeof priorDate === 'string' && (
            <p className="first-result-period">
              Comparison period: registrations as of {formatIsoDate(asOf)}, against{' '}
              {formatIsoDate(priorDate)} last year.
              {fictional && ` Fictional snapshot dated ${formatIsoDate(asOf)}.`}
            </p>
          )}
          <StatRow
            findings={findings}
            onOpenEvidence={onOpenEvidence}
            ids={SUPPORT_IDS}
            label="Student-support counts"
          />
        </>
      )}

      {decision !== null && (
        <div className="first-result-next">
          <p className="first-result-kicker">
            Next step · {role === 'executive' ? 'you decide' : 'leadership decides'}
          </p>
          <p className="first-result-next-title">{decision.title}</p>
          <dl className="first-result-facts">
            <div>
              <dt>Status</dt>
              <dd>
                {decision.approved
                  ? 'Approved by leadership'
                  : role === 'executive'
                    ? 'Waiting for your approval'
                    : 'Waiting for leadership approval'}
              </dd>
            </div>
            <div>
              <dt>Responsible office</dt>
              <dd>{decision.follow_up.office}</dd>
            </div>
            {proposedDue !== null && (
              <div>
                <dt>Proposed deadline</dt>
                <dd>{formatIsoDate(proposedDue)} (demo)</dd>
              </div>
            )}
          </dl>
        </div>
      )}

      <div className="first-result-actions">
        {(holds ? m5 : m1) !== undefined && (
          <button
            type="button"
            className="btn-secondary btn-lg"
            onClick={() => onOpenEvidence(holds ? 'M5' : 'M1')}
          >
            View evidence
          </button>
        )}
        <button type="button" className="btn-primary btn-lg" onClick={onReviewNextSteps}>
          Review next steps
        </button>
      </div>
    </section>
  )
}
