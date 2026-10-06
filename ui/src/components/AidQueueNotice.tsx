import { queuedLine, type AidQueueSummary } from '../aid'

/** What the decision panel tracks while a queue is being prepared. */
export interface AidQueueUiState {
  busy: boolean
  error: string | null
}

/**
 * The Financial Aid review queue's line in the decision card. Before the
 * decision is signed off nothing shows. Once it is, a role that may prepare
 * the queue (staff, executive, admin) gets the button; once the queue
 * exists every role reads "Queued N students for Financial Aid review", and
 * a role that may read the rows gets a button that opens them. Staff can
 * prepare the queue but never read it, so they get the line without the
 * button.
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
  if (summary === undefined || !summary.supported) return null
  if (summary.count !== null) {
    return (
      <div className="aid-queued" role="status">
        <p>{queuedLine(summary.count)}</p>
        {onOpen !== null && (
          <button type="button" className="dispatch-prepare" onClick={onOpen}>
            Open the review queue
          </button>
        )}
      </div>
    )
  }
  if (!authorized || !canPrepare) return null
  return (
    <>
      <button
        type="button"
        className="dispatch-prepare"
        disabled={state?.busy === true}
        onClick={onPrepare}
      >
        {state?.busy === true
          ? 'Preparing the review queue…'
          : 'Prepare the Financial Aid review queue'}
      </button>
      {state?.error != null && (
        <p className="error-line" role="alert">
          {state.error}
        </p>
      )}
    </>
  )
}
