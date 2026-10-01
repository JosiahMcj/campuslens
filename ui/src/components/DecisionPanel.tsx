import type { AuditEvent, Decision, SimulatedTask } from '../api'
import { taskFromEvents, type TaskRecord } from '../states'

interface DecisionPanelProps {
  decisions: Decision[] | null
  events: AuditEvent[]
  /** False for staff and reviewer: the API refuses their approval (403), so
   * the panel says who can approve instead of showing a button. */
  canApprove: boolean
  approving: boolean
  approveError: string | null
  approvedTasks: Record<string, { task: SimulatedTask; created: boolean }>
  onApprove: (decisionId: string) => void
  /** The heading; the chat reply calls it "Your decision". */
  title?: string
  /** The heading's id (s-decision); null where another copy already has it. */
  headingId?: string | null
}

/**
 * The leadership decisions (Beat 5, PROPOSAL.md section 6), visually separate
 * from the operational actions. Approve calls POST /decisions/approve; the
 * follow-up task is simulated — nothing is sent — and a second click shows
 * the same task, not a new one. A role the API would refuse (staff, reviewer)
 * reads the decision with "Only an executive can approve this" in place of
 * the button; the decision itself is never hidden.
 */
export function DecisionPanel({
  decisions,
  events,
  canApprove,
  approving,
  approveError,
  approvedTasks,
  onApprove,
  title = '6. Leadership decisions',
  headingId = 's-decision',
}: DecisionPanelProps) {
  return (
    <section
      aria-labelledby={headingId ?? undefined}
      aria-label={headingId === null ? title : undefined}
      className="decision-panel"
    >
      <h2 id={headingId ?? undefined}>{title}</h2>
      <p className="panel-note">
        This decision belongs to leadership. The cabinet advises, a person
        decides. Approving records a <code>decision.approved</code> event and
        creates one simulated follow-up task. Nothing is sent anywhere.
      </p>
      {decisions === null && <p className="status-line">Loading the decision…</p>}
      {decisions?.map((decision) => {
        const approved = approvedTasks[decision.id]
        const recovered: TaskRecord | undefined = taskFromEvents(events, decision.id)
        const task = approved?.task ?? recovered
        const alreadyApproved = decision.approved || task !== undefined
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
                    : 'This decision was already approved. The same simulated task is shown, and no duplicate was created.'}
                </p>
              </div>
            )}
          </div>
        )
      })}
    </section>
  )
}
