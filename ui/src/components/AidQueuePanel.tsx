import { useCallback, useEffect, useState } from 'react'

import {
  AID_NOTE_MAX_CHARS,
  AID_STATUSES,
  aidStatusLabel,
  fetchAidQueue,
  formatAmount,
  patchAidReview,
  plainValue,
  type AidQueue,
  type AidReviewRow,
  type AidStatus,
} from '../aid'
import { formatTimestamp, errorMessage } from '../states'

type QueueState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; queue: AidQueue }

/** The facts one row shows, read from the record and nothing else. */
function RowFacts({ row }: { row: AidReviewRow }) {
  const { facts } = row
  const last = facts.advising_last_appointment_date
  return (
    <dl className="aid-facts">
      {facts.holds.map((hold, index) => (
        <div key={index} className="aid-fact">
          <dt>{facts.holds.length > 1 ? `Hold ${index + 1}` : 'Financial hold'}</dt>
          <dd>
            {formatAmount(hold.amount)}, placed {hold.hold_date}, held by{' '}
            {hold.responsible_office}
          </dd>
        </div>
      ))}
      <div className="aid-fact">
        <dt>Registration</dt>
        <dd>{plainValue(facts.registration_status)}</dd>
      </div>
      <div className="aid-fact">
        <dt>Advising</dt>
        <dd>
          {plainValue(facts.advising_appointment_status)}
          {last !== null ? `, last appointment ${last}` : ', no appointment on record'}
        </dd>
      </div>
    </dl>
  )
}

/** One row the aid office can edit: a status and a note, saved together. */
function EditableRow({
  row,
  onSaved,
}: {
  row: AidReviewRow
  onSaved: (row: AidReviewRow) => void
}) {
  const [status, setStatus] = useState<AidStatus>(row.status)
  const [note, setNote] = useState(row.note)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const changed = status !== row.status || note !== row.note
  const noteId = `aid-note-${row.id}`
  const statusId = `aid-status-${row.id}`

  const save = async () => {
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      const updated = await patchAidReview(row.id, { status, note })
      onSaved(updated)
      setSaved(true)
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setSaving(false)
    }
  }

  return (
    <form
      className="aid-edit"
      onSubmit={(event) => {
        event.preventDefault()
        void save()
      }}
    >
      <label htmlFor={statusId}>Status</label>
      <select
        id={statusId}
        value={status}
        onChange={(event) => {
          setStatus(event.target.value as AidStatus)
          setSaved(false)
        }}
      >
        {AID_STATUSES.map((value) => (
          <option key={value} value={value}>
            {aidStatusLabel(value)}
          </option>
        ))}
      </select>
      <label htmlFor={noteId}>Note</label>
      <textarea
        id={noteId}
        value={note}
        maxLength={AID_NOTE_MAX_CHARS}
        rows={3}
        onChange={(event) => {
          setNote(event.target.value)
          setSaved(false)
        }}
      />
      <p className="aid-count">
        {note.length.toLocaleString('en-US')} of{' '}
        {AID_NOTE_MAX_CHARS.toLocaleString('en-US')} characters
      </p>
      <div className="aid-edit-actions">
        <button type="submit" className="dispatch-prepare" disabled={!changed || saving}>
          {saving ? 'Saving…' : 'Save'}
        </button>
        {saved && !changed && (
          <span className="hint" role="status">
            Saved
          </span>
        )}
      </div>
      {error !== null && (
        <p className="error-line" role="alert">
          {error}
        </p>
      )}
    </form>
  )
}

/**
 * The Financial Aid review panel: the students the emergency-aid review
 * concerns, with the facts the aid office needs to start its own review. The
 * cabinet decides nothing about any student here. A person in the aid role
 * (or an admin) sets each row's status and keeps a note; the executive and
 * the reviewer read the same rows without controls. The rows come in student
 * id order, the evidence drawer's order, and the panel adds no other order.
 */
export function AidQueuePanel({ canEdit }: { canEdit: boolean }) {
  const [state, setState] = useState<QueueState>({ kind: 'loading' })

  const load = useCallback(async () => {
    try {
      setState({ kind: 'ready', queue: await fetchAidQueue() })
    } catch (caught) {
      setState({ kind: 'error', message: errorMessage(caught) })
    }
  }, [])

  useEffect(() => {
    // The queue fetch's setState calls all land after an await.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  const replaceRow = (updated: AidReviewRow) =>
    setState((previous) =>
      previous.kind === 'ready'
        ? {
            kind: 'ready',
            queue: {
              ...previous.queue,
              rows: previous.queue.rows.map((row) => (row.id === updated.id ? updated : row)),
            },
          }
        : previous,
    )

  return (
    <div className="aid-queue">
      <p className="panel-text">
        Facts for the Financial Aid office to start its own review. The cabinet
        makes no determination about any student. Each status and note is set by
        a person in the Financial Aid office.
        {canEdit ? '' : ' This view is read only.'}
      </p>

      {state.kind === 'loading' && (
        <p className="status-line" role="status">
          Loading the review queue…
        </p>
      )}

      {state.kind === 'error' && (
        <div className="state-panel error-panel" role="alert">
          <h3>The review queue could not be loaded</h3>
          <p>{state.message}</p>
          <button
            type="button"
            onClick={() => {
              setState({ kind: 'loading' })
              void load()
            }}
          >
            Retry
          </button>
        </div>
      )}

      {state.kind === 'ready' && state.queue.rows.length === 0 && (
        <p className="hint">
          No students are queued yet. The queue is prepared from the decision
          panel once leadership authorizes the emergency-aid review.
        </p>
      )}

      {state.kind === 'ready' && state.queue.rows.length > 0 && (
        <>
          <p className="aid-summary">
            {state.queue.rows.length} students
            {state.queue.fictional ? ', demonstration data' : ''}.{' '}
            {AID_STATUSES.map((status) => {
              const count = state.queue.rows.filter((row) => row.status === status).length
              return `${aidStatusLabel(status)} ${count}`
            }).join(', ')}
            .
          </p>
          <ul className="aid-rows">
            {state.queue.rows.map((row) => (
              <li key={row.id} className="aid-row" aria-label={`Student ${row.student_id}`}>
                <div className="aid-row-head">
                  <span className="id-badge">{row.student_id}</span>
                  <span className="aid-status">{aidStatusLabel(row.status)}</span>
                </div>
                <RowFacts row={row} />
                {canEdit ? (
                  <EditableRow row={row} onSaved={replaceRow} />
                ) : (
                  <p className="aid-note-read">
                    {row.note !== '' ? row.note : 'No note yet.'}
                  </p>
                )}
                {row.updated_by !== null && row.updated_at !== null && (
                  <p className="hint">
                    Last updated by {row.updated_by} at {formatTimestamp(row.updated_at)}.
                  </p>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
