import { useRef, useState, type ReactNode } from 'react'

import type { AuditEvent, Decision, DispatchInfo, SimulatedTask } from '../api'
import type { Role } from '../auth'
import { plainSentence } from '../errors'
import { formatTimestamp, personName } from '../states'
import { AidQueueNotice, type AidQueueUiState } from './AidQueueNotice'
import { ApprovedIcon, SentIcon } from './icons'
import './DecisionPanel.css'

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
   * the panel says leadership approves instead of showing a button. */
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
  /** The heading; the chat reply calls it "Your decision". */
  title?: string
  /** The heading's id (s-decision); null where another copy already has it. */
  headingId?: string | null
  onPrepareDispatch: (decisionId: string) => void
  onSendDispatch: (decisionId: string) => void
  /** The Financial Aid review queue (optional: omitted, the row is
   * hidden). onOpenAidQueue is null for a role that may not read the rows. */
  aidQueues?: Record<string, AidQueueUiState>
  onPrepareAidQueue?: (decisionId: string) => void
  onOpenAidQueue?: (() => void) | null
  /** The decisions could not be loaded: a plain sentence, shown with Retry
   * instead of "Loading" forever. */
  loadError?: string | null
  onRetry?: () => void
  /** The Decision page: a step line (approve, prepare, send, queue) above
   * each decision, so where it stands reads at a glance. */
  progress?: boolean
}

const COMPOSE_ROLES: readonly Role[] = ['staff', 'executive', 'admin']
const SEND_ROLES: readonly Role[] = ['staff', 'admin']

/** Institution settings, scrolled to its Offices section. */
const OFFICES_HREF = '/institution#inst-offices'

/** Open Institution settings in place: the app follows the address on
 * popstate, so a pushState plus that event switches screens without a
 * reload (the href still works on its own, e.g. in a new tab). */
function openOfficesSettings(): void {
  window.history.pushState(null, '', OFFICES_HREF)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

/** The message as it will be sent, one paragraph per line. */
function messageParagraphs(body: string): ReactNode[] {
  return body.split('\n').map((line, index) => <p key={index}>{line}</p>)
}

/** The time of the latest approval of this decision in the audit log. */
function approvalTime(events: AuditEvent[], decisionId: string): string | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if (event.type === 'decision.approved' && event.payload.decision_id === decisionId) {
      return event.ts
    }
  }
  return null
}

/** Sign-in roles as a person's title. */
const ROLE_TITLES: Record<string, string> = {
  executive: 'Executive',
  admin: 'Administrator',
  staff: 'Staff',
}

/** The approver's role, when the log has recorded it for that address
 * (each question carries the asker's role). */
function recordedRole(events: AuditEvent[], email: string): string | null {
  const wanted = email.trim().toLowerCase()
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if (event.actor.toLowerCase() === wanted && typeof event.payload.role === 'string') {
      return ROLE_TITLES[event.payload.role] ?? null
    }
  }
  return null
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null
}

/** A busy button keeps focus (aria-busy, not disabled) and shows a spinner. */
function BusyLabel({ busy, working, idle }: { busy: boolean; working: string; idle: string }) {
  return (
    <>
      {busy && <span className="spinner" aria-hidden="true" />}
      {busy ? working : idle}
    </>
  )
}

/** Where a decision stands, as four short steps: done (a check), the next
 * one (highlighted), and the ones after it. Text carries the state too. */
function DecisionSteps({
  approved,
  office,
  prepared,
  sent,
  mailbox,
  queue,
}: {
  approved: boolean
  office: string
  prepared: boolean
  sent: boolean
  /** False when the office has no mailbox yet, so the send step waits. */
  mailbox: boolean
  /** null when this decision opens no review queue. */
  queue: boolean | null
}) {
  // Short labels, so the four steps keep to one line on a desktop.
  const steps: { label: string; done: boolean; waiting?: boolean }[] = [
    { label: 'Approved', done: approved },
    { label: 'Message ready', done: prepared },
    !sent && !mailbox
      ? { label: `Waiting: ${office} needs a mailbox`, done: false, waiting: true }
      : { label: `Sent to ${office}`, done: sent },
    ...(queue !== null ? [{ label: 'Review queue ready', done: queue }] : []),
  ]
  const next = steps.findIndex((step) => !step.done)
  return (
    <ol className="decision-steps" aria-label="Where this decision stands">
      {steps.map((step, index) => (
        <li
          key={index}
          className={
            step.done
              ? 'step-done'
              : step.waiting === true
                ? 'step-waiting'
                : index === next
                  ? 'step-next'
                  : 'step-later'
          }
        >
          <span className="step-mark" aria-hidden="true">
            {step.done ? '✓' : index + 1}
          </span>
          <span>
            {step.label}
            <span className="visually-hidden">
              {step.done
                ? ': done'
                : step.waiting === true
                  ? ''
                  : index === next
                    ? ': next'
                    : ': not yet'}
            </span>
          </span>
        </li>
      ))}
    </ol>
  )
}

/**
 * The leadership decision (Beat 5, PROPOSAL.md section 6). One line says who
 * decides, the decision appears once, and Approve (gold) records the
 * approval. Once approved, a gold "Approved by …" line replaces the button,
 * and a short "Next steps" list follows: the message to the responsible
 * office (Prepare, then To and Subject with the message behind "Show
 * message", then Send behind an inline confirmation, staff and admin only)
 * and the Financial Aid review queue (Prepare, then Open). Status words come
 * from the dispatch state. A role that may not approve reads "Waiting for
 * leadership approval", then "Approved by leadership". Focus never falls to
 * the page while a button works: busy buttons stay focusable (aria-busy) and
 * focus moves to the result line when it appears.
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
  title = '6. Leadership decisions',
  headingId = 's-decision',
  onPrepareDispatch,
  onSendDispatch,
  aidQueues = {},
  onPrepareAidQueue,
  onOpenAidQueue = null,
  loadError = null,
  onRetry,
  progress = false,
}: DecisionPanelProps) {
  const canCompose = COMPOSE_ROLES.includes(role)
  const canSend = SEND_ROLES.includes(role)
  const [confirmSend, setConfirmSend] = useState<string | null>(null)
  // The result line that takes focus when it appears, e.g. "D-1:approved".
  const pendingFocus = useRef<string | null>(null)
  const focusWhen = (key: string, ready = true) => (element: HTMLElement | null) => {
    if (element !== null && ready && pendingFocus.current === key) {
      pendingFocus.current = null
      element.focus()
    }
  }
  const cancelConfirm = (decisionId: string) => {
    pendingFocus.current = `${decisionId}:send`
    setConfirmSend(null)
  }

  return (
    <section
      aria-labelledby={headingId ?? undefined}
      aria-label={headingId === null ? title : undefined}
      className="decision-panel"
    >
      <h2 id={headingId ?? undefined}>{title}</h2>
      <p className="panel-intro">
        {role === 'executive'
          ? 'CampusLens advises. You decide. Nothing is sent on its own.'
          : 'Leadership decides. Nothing is sent on its own.'}
      </p>
      {decisions === null &&
        (loadError !== null ? (
          <div className="state-error state-panel error-panel" role="alert">
            <p>Couldn't load the decision. {loadError}</p>
            {onRetry !== undefined && (
              <button type="button" className="btn-secondary secondary" onClick={onRetry}>
                Retry
              </button>
            )}
          </div>
        ) : (
          <div role="status" aria-busy="true" className="decision-loading">
            <span className="visually-hidden">Loading the decision…</span>
            <div className="skeleton skeleton-line" />
            <div className="skeleton skeleton-line" />
            <div className="skeleton skeleton-line short" />
          </div>
        ))}
      {decisions !== null && decisions.length === 0 && (
        <p className="state-empty hint">
          No leadership decision is waiting. Ask an approved question to get one.
        </p>
      )}
      {decisions?.map((decision) => {
        // Approval for the active dataset only (GET /decisions), the state the
        // API acts on.
        const approved = decision.approved
        const dispatchState = dispatches[decision.id]
        const approvedHere = approvedTasks[decision.id]?.created === true
        // Who approved and when, from the API (the decision list, else the
        // dispatch state); "you" when the viewer is the approver, including
        // before the list reloads.
        const approverEmail =
          text(decision.approved_by) ?? text(dispatchState?.info?.approved_by)
        // "you", or the address with the role the log recorded for it.
        const approverRole =
          approverEmail !== null && personName(approverEmail, userEmail) !== 'you'
            ? recordedRole(events, approverEmail)
            : null
        const approverName =
          approverEmail !== null
            ? personName(approverEmail, userEmail) +
              (approverRole !== null ? ` (${approverRole})` : '')
            : approvedHere && canApprove
              ? 'you'
              : null
        const approvedAt =
          text(decision.approved_at) ??
          text(dispatchState?.info?.approved_at) ??
          approvalTime(events, decision.id)
        const dispatch = dispatchState?.info?.dispatch ?? null
        const officeContact = dispatchState?.info?.office_contact ?? null
        const office = decision.follow_up.office
        const composeBusy = dispatchState?.busy === 'compose'
        const sendBusy = dispatchState?.busy === 'send'
        const dispatchError =
          dispatchState?.error != null
            ? (plainSentence(dispatchState.error) ??
              "That didn't work. The message was not saved.")
            : null
        const approveFailure =
          approveError !== null
            ? (plainSentence(approveError) ?? "That didn't work. The approval was not saved.")
            : null
        return (
          <div key={decision.id} className="decision-card">
            {progress && (
              <DecisionSteps
                approved={approved}
                office={office}
                prepared={dispatch !== null}
                sent={dispatch?.status === 'sent'}
                mailbox={dispatchState?.info == null || officeContact !== null}
                queue={
                  dispatchState?.info?.aid_queue?.supported === true
                    ? dispatchState.info.aid_queue.count !== null
                    : null
                }
              />
            )}
            <h3>{decision.title}</h3>
            <p>{decision.text}</p>

            {!approved ? (
              canApprove ? (
                <button
                  type="button"
                  className="btn-approve approve-button"
                  aria-busy={approving}
                  onClick={() => {
                    if (approving) return
                    pendingFocus.current = `${decision.id}:approved`
                    onApprove(decision.id)
                  }}
                >
                  <BusyLabel busy={approving} working="Approving…" idle="Approve" />
                </button>
              ) : (
                <p className="hint">Waiting for leadership approval.</p>
              )
            ) : (
              <p
                className="approved-line"
                role="status"
                tabIndex={-1}
                ref={focusWhen(`${decision.id}:approved`)}
              >
                <ApprovedIcon />
                <span>
                  {`Approved by ${approverName ?? 'leadership'}`}
                  {approvedAt !== null && ` at ${formatTimestamp(approvedAt)}`}.
                </span>
              </p>
            )}
            {!approved && approveFailure !== null && (
              <p className="error-line" role="alert">
                {approveFailure}
              </p>
            )}

            {approved && (
              <div className="next-steps">
                <h4>Next steps</h4>
                <ol className="next-step-list">
                  <li className="next-step">
                    <p className="next-step-head">
                      <strong className="next-step-name">Message to {office}:</strong>{' '}
                      <span
                        className={
                          dispatch?.status === 'sent' ? 'next-step-status done' : 'next-step-status'
                        }
                        tabIndex={-1}
                        ref={focusWhen(`${decision.id}:prepared`, dispatch !== null)}
                      >
                        {dispatch === null
                          ? canCompose
                            ? 'Not prepared yet'
                            : 'Not prepared yet. Staff or leadership prepares it.'
                          : dispatch.status === 'sent'
                            ? 'Sent'
                            : dispatch.status === 'failed'
                              ? 'Not sent yet'
                              : 'Prepared, not sent'}
                      </span>
                    </p>

                    {dispatch === null && canCompose && (
                      <button
                        type="button"
                        className={
                          progress ? 'btn-primary primary-button' : 'btn-secondary secondary'
                        }
                        aria-busy={composeBusy}
                        onClick={() => {
                          if (composeBusy) return
                          pendingFocus.current = `${decision.id}:prepared`
                          onPrepareDispatch(decision.id)
                        }}
                      >
                        <BusyLabel
                          busy={composeBusy}
                          working="Preparing the message…"
                          idle="Prepare the message"
                        />
                      </button>
                    )}

                    {dispatch !== null && (
                      <div className="dispatch-draft">
                        <dl className="kv">
                            <dt>To</dt>
                            <dd>
                              {dispatch.to_office}
                              {officeContact !== null
                                ? `, ${officeContact}`
                                : ' (no mailbox set up yet)'}
                            </dd>
                            <dt>Subject</dt>
                            <dd>{dispatch.subject}</dd>
                        </dl>
                        <details className="fold technical-detail">
                          <summary>
                            Show message
                          </summary>
                          <div className="dispatch-body">
                            {messageParagraphs(dispatch.body)}
                          </div>
                        </details>

                        {dispatch.status === 'sent' ? (
                          <p
                            className="dispatch-sent"
                            role="status"
                            tabIndex={-1}
                            ref={focusWhen(`${decision.id}:sent`)}
                          >
                            <SentIcon /> Sent by{' '}
                            {dispatch.sent_by !== null
                              ? personName(dispatch.sent_by, userEmail)
                              : 'a staff member'}
                            {dispatch.sent_at !== null &&
                              ` at ${formatTimestamp(dispatch.sent_at)}`}
                            .
                          </p>
                        ) : (
                          <>
                            {dispatch.status === 'failed' && (
                              <div className="error-line" role="alert">
                                <p>The last send did not go through. Nothing was delivered.</p>
                                {dispatch.error !== null && (
                                  <details className="fold technical-detail">
                                    <summary>
                                      Technical detail
                                    </summary>
                                    <p>{dispatch.error}</p>
                                  </details>
                                )}
                              </div>
                            )}
                            {officeContact === null &&
                              (role === 'admin' ? (
                                <p className="hint">
                                  {dispatch.to_office} has no mailbox yet. Add one in{' '}
                                  <a
                                    href={OFFICES_HREF}
                                    onClick={(event) => {
                                      event.preventDefault()
                                      openOfficesSettings()
                                    }}
                                  >
                                    Institution settings, Offices
                                  </a>{' '}
                                  before it can be sent.
                                </p>
                              ) : (
                                <p className="hint">
                                  {dispatch.to_office} has no mailbox yet. An administrator
                                  adds one in Institution settings before it can be sent.
                                </p>
                              ))}
                            {canSend && officeContact !== null ? (
                              confirmSend === decision.id ? (
                                <div
                                  className="confirm-inline"
                                  role="alertdialog"
                                  aria-label="Confirm sending"
                                  tabIndex={-1}
                                  ref={focusWhen(`${decision.id}:confirm`)}
                                  onKeyDown={(event) => {
                                    // Escape cancels the confirmation, not the
                                    // panel around it; focus goes back to Send….
                                    if (event.key === 'Escape' && !sendBusy) {
                                      event.stopPropagation()
                                      event.preventDefault()
                                      cancelConfirm(decision.id)
                                    }
                                  }}
                                >
                                  <p>
                                    Send to {dispatch.to_office} at {officeContact}? It goes
                                    from {userEmail}.
                                  </p>
                                  <div className="confirm-actions">
                                    <button
                                      type="button"
                                      className="btn-primary primary-button"
                                      aria-busy={sendBusy}
                                      onClick={() => {
                                        if (sendBusy) return
                                        pendingFocus.current = `${decision.id}:sent`
                                        onSendDispatch(decision.id)
                                      }}
                                    >
                                      <BusyLabel busy={sendBusy} working="Sending…" idle="Send" />
                                    </button>
                                    <button
                                      type="button"
                                      className="btn-secondary secondary"
                                      disabled={sendBusy}
                                      onClick={() => cancelConfirm(decision.id)}
                                    >
                                      Cancel
                                    </button>
                                  </div>
                                </div>
                              ) : (
                                <button
                                  type="button"
                                  className="btn-primary primary-button dispatch-send"
                                  ref={focusWhen(`${decision.id}:send`)}
                                  onClick={() => {
                                    pendingFocus.current = `${decision.id}:confirm`
                                    setConfirmSend(decision.id)
                                  }}
                                >
                                  Send…
                                </button>
                              )
                            ) : (
                              !canSend && (
                                <p className="hint">A staff member sends this message.</p>
                              )
                            )}
                          </>
                        )}
                      </div>
                    )}
                    {dispatchError !== null && (
                      <p className="error-line" role="alert">
                        {dispatchError}
                      </p>
                    )}
                  </li>
                  {onPrepareAidQueue !== undefined && (
                    <AidQueueNotice
                      summary={dispatchState?.info?.aid_queue}
                      authorized={approved}
                      canPrepare={canCompose}
                      state={aidQueues[decision.id]}
                      onPrepare={() => onPrepareAidQueue(decision.id)}
                      onOpen={onOpenAidQueue}
                    />
                  )}
                </ol>
              </div>
            )}
          </div>
        )
      })}
    </section>
  )
}
