import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '../auth'
import {
  AID_NOTE_MAX_CHARS,
  AID_ROW_CHANGED_MESSAGE,
  AID_STATUSES,
  aidStatusLabel,
  fetchAidQueue,
  formatAmount,
  noteLength,
  patchAidReview,
  plainValue,
  type AidQueue,
  type AidReviewChange,
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

/**
 * One row the aid office can edit: a status and a note. A save sends only
 * the fields that changed, with the row's updated_at as it was opened. When
 * someone else saved the row in the meantime the API refuses (409): the row
 * reloads and shows the saved version under the box, while the person's own
 * edits stay in place to compare and save again. A field they did not touch
 * takes the saved value, so a second save never undoes someone else's change.
 */
function EditableRow({
  row,
  onSaved,
  onStale,
}: {
  row: AidReviewRow
  onSaved: (row: AidReviewRow) => void
  /** Reload this row from the API. Resolves to the fresh row, or null when
   * it could not be read. */
  onStale: (id: number) => Promise<AidReviewRow | null>
}) {
  const [status, setStatus] = useState<AidStatus>(row.status)
  const [note, setNote] = useState(row.note)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  // The row as saved by someone else, after a refused (409) save.
  const [savedVersion, setSavedVersion] = useState<AidReviewRow | null>(null)
  const changed = status !== row.status || note !== row.note
  const length = noteLength(note)
  const over = length - AID_NOTE_MAX_CHARS
  const noteId = `aid-note-${row.id}`
  const countId = `aid-note-count-${row.id}`
  const overId = `aid-note-over-${row.id}`
  const statusId = `aid-status-${row.id}`
  const savedVersionId = `aid-saved-version-${row.id}`
  const describedBy = [
    countId,
    ...(over > 0 ? [overId] : []),
    ...(savedVersion !== null ? [savedVersionId] : []),
  ].join(' ')

  const save = async () => {
    if (!changed || over > 0) return
    const change: AidReviewChange = { expected_updated_at: row.updated_at }
    if (status !== row.status) change.status = status
    if (note !== row.note) change.note = note
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      const updated = await patchAidReview(row.id, change)
      onSaved(updated)
      setSavedVersion(null)
      setSaved(true)
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        setError(AID_ROW_CHANGED_MESSAGE)
        // `row` here is the version this save was based on: a field equal to
        // it is one the person did not edit, so it follows the saved value.
        const fresh = await onStale(row.id)
        if (fresh !== null) {
          setSavedVersion(fresh)
          if (status === row.status) setStatus(fresh.status)
          if (note === row.note) setNote(fresh.note)
        }
      } else {
        setError(errorMessage(caught))
      }
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
      {/* No maxLength: the browser counts UTF-16 units and would cut an
          emoji in half. The count below is the API's own (characters). */}
      <textarea
        id={noteId}
        value={note}
        rows={3}
        aria-describedby={describedBy}
        aria-invalid={over > 0}
        onChange={(event) => {
          setNote(event.target.value)
          setSaved(false)
        }}
      />
      <p className="aid-count" id={countId}>
        {length.toLocaleString('en-US')} of{' '}
        {AID_NOTE_MAX_CHARS.toLocaleString('en-US')} characters
      </p>
      {over > 0 && (
        <p className="field-error" id={overId}>
          {over.toLocaleString('en-US')} character{over === 1 ? '' : 's'} over the
          limit. Shorten the note to save it.
        </p>
      )}
      {savedVersion !== null && (
        <p className="hint aid-saved-version" id={savedVersionId}>
          Saved version: {aidStatusLabel(savedVersion.status)}.{' '}
          {savedVersion.note !== '' ? `Note: ${savedVersion.note}` : 'No note.'}
        </p>
      )}
      <div className="aid-edit-actions">
        <button
          type="submit"
          className="dispatch-prepare"
          disabled={!changed || saving || over > 0}
        >
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

  /** Read the queue again and replace one row with the API's copy. */
  const reloadRow = async (id: number): Promise<AidReviewRow | null> => {
    try {
      const fresh = (await fetchAidQueue()).rows.find((row) => row.id === id) ?? null
      if (fresh !== null) replaceRow(fresh)
      return fresh
    } catch {
      return null
    }
  }

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
                  <EditableRow row={row} onSaved={replaceRow} onStale={reloadRow} />
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
