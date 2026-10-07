import { useRef } from 'react'

import { queuedLine, type AidQueueSummary } from '../aid'
import { plainSentence } from '../errors'

/** What the decision panel tracks while a queue is being prepared. */
export interface AidQueueUiState {
  busy: boolean
  error: string | null
}

/**
 * The Financial Aid review queue as one "Next steps" row in the decision
 * card, shown once the decision is approved. A role that may prepare the
 * queue (staff, executive, admin) gets Prepare; once the queue exists every
 * role reads "Queued N students", and a role that may read the rows gets
 * Open. Staff can prepare the queue but never read it, so they get the line
 * without Open. While Prepare works the button stays focusable (aria-busy),
 * and when the queue lands focus moves to the result line.
 */
export function AidQueueNotice({
  summary,
  authorized,
  canPrepare,
  state,
  onPrepare,
  onOpen,
}: {
  summary: AidQueueSummary | undefined
  authorized: boolean
  canPrepare: boolean
  state: AidQueueUiState | undefined
  onPrepare: () => void
  onOpen: (() => void) | null
}) {
  const focusResult = useRef(false)
  if (summary === undefined || !summary.supported || !authorized) return null
  const busy = state?.busy === true
  const error =
    state?.error != null
      ? (plainSentence(state.error) ?? "That didn't work. The review queue was not prepared.")
      : null
  return (
    <li className="next-step aid-queued">
      <p className="next-step-head">
        <strong className="next-step-name">Financial Aid review queue:</strong>{' '}
        {summary.count !== null ? (
          <span
            className="next-step-status done"
            tabIndex={-1}
            ref={(element) => {
              if (element !== null && focusResult.current) {
                focusResult.current = false
                element.focus()
              }
            }}
          >
            {queuedLine(summary.count).replace(' for Financial Aid review', '')}
          </span>
        ) : (
          <span className="next-step-status">Not prepared yet</span>
        )}
      </p>
      {summary.count !== null
        ? onOpen !== null && (
            <button type="button" className="btn-secondary secondary" onClick={onOpen}>
              Open the review queue
            </button>
          )
        : canPrepare && (
            <button
              type="button"
              className="btn-secondary secondary"
              aria-busy={busy}
              onClick={() => {
                if (busy) return
                focusResult.current = true
                onPrepare()
              }}
            >
              {busy && <span className="spinner" aria-hidden="true" />}
              {busy ? 'Preparing the review queue…' : 'Prepare the review queue'}
            </button>
          )}
      {error !== null && (
        <p className="error-line" role="alert">
          {error}
        </p>
      )}
    </li>
  )
}
