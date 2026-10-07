import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import { ApiError, getSession } from '../auth'
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
import { fetchDecisions } from '../api'
import { friendlyError } from '../errors'
import { formatTimestamp, parseFlags, personName } from '../states'
import { ChevronIcon } from './icons'

type QueueState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  /** approved: whether leadership has approved a decision yet, read only
   * when the queue is empty (null when that could not be read). */
  | { kind: 'ready'; queue: AidQueue; approved?: boolean | null }

type StatusFilter = 'all' | AidStatus

/** The row's holds in one short phrase: "Bursar hold $160.73". */
function holdSummary(row: AidReviewRow): string {
  const holds = row.facts.holds
  if (holds.length === 0) return 'No hold on record'
  if (holds.length === 1) {
    return `${holds[0].responsible_office} hold ${formatAmount(holds[0].amount)}`
  }
  const offices = [...new Set(holds.map((hold) => hold.responsible_office))].join(', ')
  return `${holds.length} holds, ${offices}`
}

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
  const savedRef = useRef<HTMLSpanElement>(null)
  // Set by a successful save: the "Saved" line takes focus once it is on
  // screen, so focus never falls to the page when Save goes quiet.
  const focusSaved = useRef(false)
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

  // After the re-render that shows "Saved" (the parent's new row and this
  // form's state land together), before paint.
  useLayoutEffect(() => {
    if (saved && focusSaved.current && savedRef.current !== null) {
      focusSaved.current = false
      savedRef.current.focus()
    }
  })

  const save = async () => {
    if (saving || !changed || over > 0) return
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
      focusSaved.current = true
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
        setError(friendlyError(caught, 'The review'))
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
        className="field"
        value={status}
        onChange={(event) => {
          setStatus(event.target.value as AidStatus)
          setSaved(false)
          setError(null)
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
        className="field"
        value={note}
        rows={3}
        aria-describedby={describedBy}
        aria-invalid={over > 0}
        onChange={(event) => {
          setNote(event.target.value)
          setSaved(false)
          setError(null)
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
          className="btn-primary primary-button"
          disabled={!saving && (!changed || over > 0)}
          aria-busy={saving}
          onClick={(event) => {
            if (saving) event.preventDefault()
          }}
        >
          {saving && <span className="spinner" aria-hidden="true" />}
          {saving ? 'Saving…' : 'Save'}
        </button>
        {saved && !changed && (
          <span className="hint" role="status" tabIndex={-1} ref={savedRef}>
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
export function AidQueuePanel({
  canEdit,
  onOpenDecision,
  viewerEmail = getSession()?.user.email ?? null,
}: {
  canEdit: boolean
  /** Opens the Decision page, where the queue is prepared after approval. */
  onOpenDecision?: () => void
  /** Who is looking: their own updates read "you". */
  viewerEmail?: string | null
}) {
  const [state, setState] = useState<QueueState>({ kind: 'loading' })
  const [filter, setFilter] = useState<StatusFilter>('all')
  const [openRow, setOpenRow] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      const queue = await fetchAidQueue()
      // An empty queue reads differently before and after approval.
      let approved: boolean | null = null
      if (queue.rows.length === 0) {
        try {
          approved = (await fetchDecisions(parseFlags(''))).some((decision) => decision.approved)
        } catch {
          approved = null
        }
      }
      setState({ kind: 'ready', queue, approved })
    } catch (caught) {
      setState({ kind: 'error', message: friendlyError(caught, 'The review queue') })
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
      {state.kind === 'loading' && (
        <p className="status-line" role="status">
          Loading the review queue…
        </p>
      )}

      {state.kind === 'error' && (
        <div className="state-error state-panel error-panel" role="alert">
          <p>Couldn't load the review queue. {state.message}</p>
          <button
            type="button"
            className="btn-secondary secondary"
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
        <div className="state-empty">
          <p>
            {state.approved === true
              ? 'Approved. Waiting for staff to prepare the review queue from the Decision page.'
              : state.approved === false
                ? 'No students are queued yet. Once leadership approves the decision, staff prepare the queue from the Decision page.'
                : "No students are queued yet. The queue is prepared from the Decision page's next steps once leadership approves."}
          </p>
          {onOpenDecision !== undefined && (
            <div className="state-actions">
              <button type="button" className="btn-primary primary-button" onClick={onOpenDecision}>
                Go to the decision
              </button>
            </div>
          )}
        </div>
      )}

      {state.kind === 'ready' && state.queue.rows.length > 0 && (
        <QueueRows
          queue={state.queue}
          filter={filter}
          onFilter={setFilter}
          openRow={openRow}
          onToggle={(id) => setOpenRow((current) => (current === id ? null : id))}
          canEdit={canEdit}
          onSaved={replaceRow}
          onStale={reloadRow}
          viewerEmail={viewerEmail}
        />
      )}
    </div>
  )
}

/**
 * The queue as one compact row per student (id, hold, status) that opens to
 * the facts and the form when chosen, with a status filter above.
 */
function QueueRows({
  queue,
  filter,
  onFilter,
  openRow,
  onToggle,
  canEdit,
  onSaved,
  onStale,
  viewerEmail,
}: {
  queue: AidQueue
  filter: StatusFilter
  onFilter: (filter: StatusFilter) => void
  openRow: number | null
  onToggle: (id: number) => void
  canEdit: boolean
  onSaved: (row: AidReviewRow) => void
  onStale: (id: number) => Promise<AidReviewRow | null>
  viewerEmail: string | null
}) {
  const count = (status: AidStatus) => queue.rows.filter((row) => row.status === status).length
  const rows = filter === 'all' ? queue.rows : queue.rows.filter((row) => row.status === filter)
  return (
    <>
      <p className="aid-summary">
        {queue.rows.length} student{queue.rows.length === 1 ? '' : 's'}
        {queue.fictional ? ', demonstration data' : ''}:{' '}
        {AID_STATUSES.map((status) => `${count(status)} ${aidStatusLabel(status).toLowerCase()}`).join(
          ', ',
        )}
        .
      </p>
      <label className="aid-filter">
        <span>Show</span>{' '}
        <select value={filter} onChange={(event) => onFilter(event.target.value as StatusFilter)}>
          <option value="all">All ({queue.rows.length})</option>
          {AID_STATUSES.map((status) => (
            <option key={status} value={status}>
              {aidStatusLabel(status)} ({count(status)})
            </option>
          ))}
        </select>
      </label>
      {rows.length === 0 ? (
        <p className="state-empty hint">
          No students with this status. Choose All to see every student.
        </p>
      ) : (
        <ul className="aid-rows">
          {rows.map((row) => {
            const open = openRow === row.id
            const panelId = `aid-row-${row.id}`
            return (
              <li
                key={row.id}
                className={open ? 'aid-row open' : 'aid-row'}
                aria-label={`Student ${row.student_id}`}
              >
                <button
                  type="button"
                  className="aid-row-head finding-row"
                  aria-expanded={open}
                  aria-controls={panelId}
                  onClick={() => onToggle(row.id)}
                >
                  <span className="aid-row-id">{row.student_id}</span>
                  <span className="aid-row-hold">{holdSummary(row)}</span>
                  <span className={`aid-status aid-status-${row.status}`}>
                    {aidStatusLabel(row.status)}
                  </span>
                  <ChevronIcon />
                </button>
                {open && (
                  <div className="aid-row-body" id={panelId}>
                    <RowFacts row={row} />
                    {canEdit ? (
                      <EditableRow row={row} onSaved={onSaved} onStale={onStale} />
                    ) : (
                      <p className="aid-note-read">
                        {row.note !== '' ? row.note : 'No note yet.'}
                      </p>
                    )}
                    {row.updated_by !== null && row.updated_at !== null && (
                      <p className="hint">
                        Last updated by {personName(row.updated_by, viewerEmail)} at{' '}
                        {formatTimestamp(row.updated_at)}.
                      </p>
                    )}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </>
  )
}
