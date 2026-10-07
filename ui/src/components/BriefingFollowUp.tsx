import { useId } from 'react'

import type { Decision } from '../api'
import type { FollowUpAnswer, FollowUpBlock, FollowUpDenied, FollowUpLabel } from '../followup'
import { findingLabel } from '../findingLabels'
import { ApprovedIcon, SearchIcon } from './icons'
import './DecisionPanel.css'
import './FollowUp.css'

/** The words on each block's label: what kind of statement it is, so a
 * computed fact is never mistaken for a reading of it. */
function labelWords(label: FollowUpLabel, author: string | undefined): string {
  switch (label) {
    case 'fact':
      return 'Fact · computed from the records'
    case 'interpretation':
      return `Interpretation · ${author ?? 'CampusLens'}`
    case 'recommendation':
      return author !== undefined ? `Recommendation · ${author}` : 'Proposed · not executed'
    default:
      return 'Note'
  }
}

function Label({ label, author }: { label: FollowUpLabel; author?: string }) {
  return (
    <span className="followup-label" data-label={label}>
      {labelWords(label, author)}
    </span>
  )
}

interface ApprovalProps {
  block: Extract<FollowUpBlock, { type: 'approval' }>
  decision: Decision | null
  canApprove: boolean
  approving: boolean
  approveError: string | null
  onApprove: (decisionId: string) => void
}

/** The leadership decision, with Approve wired to the same approval the
 * decision panel uses (POST /decisions/approve, recorded in the audit log). */
function ApprovalCard({ block, decision, canApprove, approving, approveError, onApprove }: ApprovalProps) {
  const approved = decision?.approved ?? block.approved
  const by = decision?.approved_by ?? block.approved_by
  const at = decision?.approved_at ?? block.approved_at
  const when = at !== null && at !== undefined ? new Date(at) : null
  return (
    <div className="followup-approval" data-approved={approved}>
      <div className="followup-approval-head">
        {approved ? (
          <span className="followup-label" data-label="fact">
            Decision recorded
          </span>
        ) : (
          <Label label="recommendation" />
        )}
        <span className="followup-approval-status">
          {approved ? 'Approved' : 'Awaiting your approval'}
        </span>
      </div>
      <h4>{block.title}</h4>
      <p>{block.text}</p>
      <p className="followup-approval-office">
        Responsible office: <strong>{block.office}</strong>
      </p>
      {approved ? (
        <p className="followup-approval-done approved-line" role="status">
          <ApprovedIcon />
          <span>
            Approved{by ? ` by ${by}` : ''}
            {when !== null && !Number.isNaN(when.getTime())
              ? ` on ${when.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}`
              : ''}
            . The approval and a task for {block.office} are in the audit log. Nothing was sent.
          </span>
        </p>
      ) : canApprove ? (
        <div className="followup-approval-actions">
          <button
            type="button"
            className="btn-approve approve-button"
            aria-busy={approving}
            onClick={() => {
              if (!approving) onApprove(block.decision_id)
            }}
          >
            {approving ? 'Approving…' : 'Approve'}
          </button>
          <span className="followup-approval-hint">
            Approving records your decision and creates a task for {block.office}. It sends no
            message and changes no student record.
          </span>
        </div>
      ) : (
        <p className="followup-approval-hint">Only the executive or an admin can approve this.</p>
      )}
      {approveError !== null && !approved && (
        <p className="error-line" role="alert">
          {approveError}
        </p>
      )}
    </div>
  )
}

function BlockView({
  block,
  onOpenEvidence,
  approval,
}: {
  block: FollowUpBlock
  onOpenEvidence: (findingId: string) => void
  approval: Omit<ApprovalProps, 'block' | 'decision'> & { decisions: Decision[] | null }
}) {
  if (block.type === 'approval') {
    const decision = approval.decisions?.find((d) => d.id === block.decision_id) ?? null
    return <ApprovalCard block={block} decision={decision} {...approval} />
  }
  const evidence = (ids: readonly string[] | undefined) =>
    ids !== undefined && ids.length > 0 ? (
      <button
        type="button"
        className="link-button followup-evidence-link"
        aria-label={`See the evidence: ${findingLabel(ids[0])}`}
        onClick={() => onOpenEvidence(ids[0])}
      >
        See evidence
      </button>
    ) : null
  if (block.type === 'text') {
    return (
      <div className="followup-block" data-label={block.label}>
        <Label label={block.label} author={block.author} />
        <p>{block.text}</p>
      </div>
    )
  }
  if (block.type === 'list') {
    return (
      <div className="followup-block" data-label={block.label}>
        <Label label={block.label} author={block.author} />
        <h4>{block.title}</h4>
        <ul>
          {block.items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </div>
    )
  }
  const withEvidence = block.row_findings !== undefined
  return (
    <div className="followup-block" data-label={block.label}>
      <Label label={block.label} />
      <h4>{block.title}</h4>
      <div className="followup-table-wrap">
        <table className="followup-table">
          <thead>
            <tr>
              {block.columns.map((column) => (
                <th key={column} scope="col">
                  {column}
                </th>
              ))}
              {withEvidence && <th scope="col">Evidence</th>}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, index) => (
              <tr key={`${index}-${row[0]}`}>
                {row.map((cell, column) =>
                  column === 0 ? (
                    <th key={column} scope="row">
                      {cell}
                    </th>
                  ) : (
                    <td key={column} data-column={block.columns[column]}>
                      {cell}
                    </td>
                  ),
                )}
                {withEvidence && (
                  <td className="followup-evidence-cell">
                    {evidence(block.row_findings?.[index])}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

interface BriefingFollowUpProps {
  response: FollowUpAnswer
  onOpenEvidence: (findingId: string) => void
  onAsk: (question: string) => void
  busy: boolean
  decisions: Decision[] | null
  canApprove: boolean
  approving: boolean
  approveError: string | null
  onApprove: (decisionId: string) => void
  onSeeAuditLog: (() => void) | null
}

/** A follow-up to the registration briefing, answered in code: labelled
 * blocks (fact, interpretation, proposal, note), real tables, the decision
 * with its Approve button, and what to ask next. */
export function BriefingFollowUp({
  response,
  onOpenEvidence,
  onAsk,
  busy,
  decisions,
  canApprove,
  approving,
  approveError,
  onApprove,
  onSeeAuditLog,
}: BriefingFollowUpProps) {
  const titleId = useId()
  const nextId = useId()
  return (
    <section className="followup" aria-labelledby={titleId}>
      <h3 id={titleId} className="followup-title">
        {response.title}
      </h3>
      {response.blocks.map((block, index) => (
        <BlockView
          key={index}
          block={block}
          onOpenEvidence={onOpenEvidence}
          approval={{ decisions, canApprove, approving, approveError, onApprove }}
        />
      ))}
      <p className="followup-source">
        {response.source} Answered in code from the briefing's figures; no AI model wrote the
        numbers.
        {onSeeAuditLog !== null && (
          <>
            {' '}
            <button type="button" className="link-button" onClick={onSeeAuditLog}>
              Recorded in the audit log
            </button>
          </>
        )}
      </p>
      {response.suggestions.length > 0 && (
        <div className="try-card explore-suggestions">
          <p className="explore-try-title try-card-title" id={nextId}>
            Ask next
          </p>
          <ul className="try-list" aria-labelledby={nextId}>
            {response.suggestions.slice(0, 3).map((question) => (
              <li key={question}>
                <button
                  type="button"
                  className="try-row"
                  disabled={busy}
                  onClick={() => onAsk(question)}
                >
                  <SearchIcon />
                  <span>{question}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

/** A counseling or chaplain request to a named AI employee: refused by the
 * permission gate before anything was read, and recorded. */
export function FollowUpDeniedCard({
  response,
  onSeeAuditLog,
}: {
  response: FollowUpDenied
  onSeeAuditLog: (() => void) | null
}) {
  return (
    <div className="refusal-card followup-denied" role="alert">
      <h3>
        Access denied · {response.employee_title}
      </h3>
      <p>{response.message}</p>
      {onSeeAuditLog !== null && (
        <button type="button" className="link-button" onClick={onSeeAuditLog}>
          See the refusal in the audit log
        </button>
      )}
    </div>
  )
}
