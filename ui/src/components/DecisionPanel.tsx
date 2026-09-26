import type { ReactNode } from 'react'

import type { AuditEvent, Decision, DispatchInfo, SimulatedTask } from '../api'
import type { Role } from '../auth'
import { formatTimestamp, taskFromEvents, type TaskRecord } from '../states'
import { FindingLink } from './FindingLink'

/** What the panel knows about one decision's dispatch: the API's dispatch
 * state, plus whether a Prepare or Send is in flight and its error. */
export interface DispatchUiState {
  info: DispatchInfo | null
  busy: 'compose' | 'send' | null
  error: string | null
}

interface DecisionPanelProps {
  decisions: Decision[] | null
  events: AuditEvent[]
  /** False for staff and reviewer: the API refuses their approval (403), so
   * the panel says who can approve instead of showing a button. */
  canApprove: boolean
  /** The signed-in user's role and address: composing is open to staff,
   * executive, and admin; sending is staff and admin only. */
  role: Role
  userEmail: string
  approving: boolean
  approveError: string | null
  approvedTasks: Record<string, { task: SimulatedTask; created: boolean }>
  dispatches: Record<string, DispatchUiState>
  onApprove: (decisionId: string) => void
  onPrepareDispatch: (decisionId: string) => void
  onSendDispatch: (decisionId: string) => void
  onOpenEvidence: (findingId: string) => void
}

const COMPOSE_ROLES: readonly Role[] = ['staff', 'executive', 'admin']
const SEND_ROLES: readonly Role[] = ['staff', 'admin']

/** The finding ids in the message body render as evidence links, exactly
 * like numbers in the briefing: the reader can check every figure before
 * anyone sends it. */
function linkifiedBody(
  body: string,
  onOpenEvidence: (findingId: string) => void,
): ReactNode[] {
  return body.split('\n').map((line, lineIndex) => {
    const parts = line.split(/\b(M[1-7])\b/)
    return (
      <p key={lineIndex}>
        {parts.map((part, partIndex) =>
          /^M[1-7]$/.test(part) ? (
            <FindingLink
              key={partIndex}
              findingId={part}
              onOpen={onOpenEvidence}
            >
              {part}
            </FindingLink>
          ) : (
            part
          ),
        )}
      </p>
    )
  })
}

/**
 * The leadership decisions (Beat 5, PROPOSAL.md section 6), visually separate
 * from the operational actions. Approve calls POST /decisions/approve; the
 * follow-up task is recorded, and a second click shows the same task, not a
 * new one. A role the API would refuse (staff, reviewer) reads the decision
 * with "Only an executive can approve this" in place of the button; the
 * decision itself is never hidden.
 *
 * After approval, the dispatch block is the governed execution step: Prepare
 * composes the message to the responsible office in code (shown read-only,
 * finding links intact), and Send — visible only to staff and admin — sends
 * it through the configured provider. The executive sees the draft and the
 * note that a staff member sends it. Once sent, the block says who sent it,
 * when, and through which provider, and Send is gone: a sent message is never
 * resent.
 */
export function DecisionPanel({
  decisions,
  events,
  canApprove,
  role,
  userEmail,
  approving,
  approveError,
  approvedTasks,
  dispatches,
  onApprove,
  onPrepareDispatch,
  onSendDispatch,
  onOpenEvidence,
}: DecisionPanelProps) {
  const canCompose = COMPOSE_ROLES.includes(role)
  const canSend = SEND_ROLES.includes(role)
  return (
    <section aria-labelledby="s-decision" className="decision-panel">
      <h2 id="s-decision">6. Leadership decisions</h2>
      <p className="panel-note">
        This decision belongs to leadership. The cabinet advises, a person
        decides. Approving records a <code>decision.approved</code> event and
        creates one follow-up task. After approval, a named staff member can
        send the office its message. Nothing is ever sent on its own.
      </p>
      {decisions === null && <p className="status-line">Loading the decision…</p>}
      {decisions?.map((decision) => {
        const approved = approvedTasks[decision.id]
        const recovered: TaskRecord | undefined = taskFromEvents(events, decision.id)
        const task = approved?.task ?? recovered
        const alreadyApproved = decision.approved || task !== undefined
        const dispatchState = dispatches[decision.id]
        const dispatch = dispatchState?.info?.dispatch ?? null
        const officeContact = dispatchState?.info?.office_contact ?? null
        return (
          <div key={decision.id} className="decision-card">
            <h3>{decision.title}</h3>
            <p>{decision.text}</p>
            <p className="follow-up">
              Follow-up on approval: {decision.follow_up.office}.{' '}
              {decision.follow_up.description}
            </p>
            {canApprove ? (
              <button
                type="button"
                className="approve-button"
                disabled={approving}
                onClick={() => onApprove(decision.id)}
              >
                {approving
                  ? 'Approving the review…'
                  : alreadyApproved
                    ? 'Approve again (shows the same task)'
                    : 'Approve the review'}
              </button>
            ) : (
              <p className="hint">Only an executive can approve this.</p>
            )}
            {approveError !== null && (
              <p className="error-line" role="alert">
                Approval failed: {approveError}
              </p>
            )}
            {task !== undefined && (
              <div className="simulated-task" role="status">
                <h4>Follow-up task</h4>
                <dl>
                  <dt>Task</dt>
                  <dd>
                    <code>{task.id}</code>
                  </dd>
                  <dt>Office</dt>
                  <dd>{task.office}</dd>
                  <dt>Description</dt>
                  <dd>{task.description}</dd>
                  <dt>Status</dt>
                  <dd>
                    <strong>{task.status}</strong>
                  </dd>
                </dl>
                <p className="hint">
                  {approved?.created === true
                    ? 'Task created and recorded in the audit log.'
                    : 'This decision was already approved. The same follow-up task is shown, and no duplicate was created.'}
                </p>
              </div>
            )}
            {alreadyApproved && dispatch === null && canCompose && (
              <button
                type="button"
                className="dispatch-prepare"
                disabled={dispatchState?.busy === 'compose'}
                onClick={() => onPrepareDispatch(decision.id)}
              >
                {dispatchState?.busy === 'compose'
                  ? 'Preparing the message…'
                  : `Prepare the message to ${decision.follow_up.office}`}
              </button>
            )}
            {dispatch !== null && (
              <div className="dispatch-draft">
                <h4>
                  {dispatch.status === 'sent'
                    ? 'Message sent'
                    : 'Message to the office, not yet sent'}
                </h4>
                <dl>
                  <dt>To</dt>
                  <dd>
                    {dispatch.to_office}
                    {officeContact !== null
                      ? ` <${officeContact}>`
                      : ' (no mailbox configured)'}
                  </dd>
                  <dt>Subject</dt>
                  <dd>{dispatch.subject}</dd>
                </dl>
                <div className="dispatch-body">
                  {linkifiedBody(dispatch.body, onOpenEvidence)}
                </div>
                {dispatch.status === 'sent' ? (
                  <p className="dispatch-sent" role="status">
                    Sent by {dispatch.sent_by} at{' '}
                    {dispatch.sent_at !== null
                      ? formatTimestamp(dispatch.sent_at)
                      : 'an unknown time'}{' '}
                    through {dispatch.provider}.
                  </p>
                ) : (
                  <>
                    {dispatch.status === 'failed' && (
                      <p className="error-line" role="alert">
                        The last send did not go through: {dispatch.error}
                      </p>
                    )}
                    {officeContact === null && (
                      <p className="hint">
                        No mailbox is configured for {dispatch.to_office}. An
                        administrator must add one first (office contacts, in the runbook)
                        before anything can be sent.
                      </p>
                    )}
                    {canSend ? (
                      <button
                        type="button"
                        className="dispatch-send"
                        disabled={dispatchState?.busy === 'send'}
                        onClick={() => onSendDispatch(decision.id)}
                      >
                        {dispatchState?.busy === 'send'
                          ? 'Sending…'
                          : `Send as ${userEmail}`}
                      </button>
                    ) : role === 'executive' ? (
                      <p className="hint">
                        A staff member sends this message. The executive
                        approves. Sending is not the executive's step.
                      </p>
                    ) : (
                      <p className="hint">
                        Only staff and administrators can send this message.
                      </p>
                    )}
                  </>
                )}
                {dispatchState?.error != null && (
                  <p className="error-line" role="alert">
                    {dispatchState.error}
                  </p>
                )}
              </div>
            )}
          </div>
        )
      })}
    </section>
  )
}
