import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'

import { getSession } from '../auth'
import { friendlyError, friendlyLoadError } from '../errors'
import { formatTimestamp, personName } from '../states'
import {
  ACTION_CHANGED_MESSAGE,
  ACTION_NOTE_MAX_CHARS,
  ACTION_STATUSES,
  ActionError,
  addActionNote,
  changeWords,
  dueLabel,
  fetchStaffActions,
  isPastDue,
  ownerLabel,
  saveStaffAction,
  sendActionToOffice,
  statusCounts,
  statusLabel,
  todayIso,
  type ActionSave,
  type ActionStatus,
  type StaffAction,
  type StaffActionList,
} from '../staffActions'
import { FindingLink } from './FindingLink'
import { SentIcon } from './icons'
import './StaffActions.css'

type ListState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; list: StaffActionList }

type StatusFilter = 'all' | ActionStatus

/** The value the owner select uses for "the office itself". */
const OFFICE_OWNER = ''

interface StaffActionsPageProps {
  onOpenEvidence: (findingId: string) => void
  /** Opens Institution settings (admins), where office mailboxes are set. */
  onOpenInstitution: (() => void) | null
  /** Who is looking; their own name reads "you". */
  viewerEmail?: string | null
}

/**
 * Staff actions as a worklist: what staff can do now, each with a
 * responsible office, no leadership approval needed. Each action has a
 * status, an owner, a due date, notes and a history, and a named staff
 * member can send it to the office mailbox. Staff and administrators edit;
 * the executive reads and adds notes; the reviewer reads; the Financial Aid
 * office sees its own action.
 */
export function StaffActionsPage({
  onOpenEvidence,
  onOpenInstitution,
  viewerEmail = getSession()?.user.email ?? null,
}: StaffActionsPageProps) {
  const [state, setState] = useState<ListState>({ kind: 'loading' })
  const [office, setOffice] = useState<string>('all')
  const [status, setStatus] = useState<StatusFilter>('all')

  const load = useCallback(async () => {
    try {
      setState({ kind: 'ready', list: await fetchStaffActions() })
    } catch (caught) {
      setState({ kind: 'error', message: friendlyLoadError(caught) })
    }
  }, [])

  useEffect(() => {
    // The list fetch's setState calls all land after an await.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  const replace = useCallback((item: StaffAction) => {
    setState((previous) =>
      previous.kind === 'ready'
        ? {
            kind: 'ready',
            list: {
              ...previous.list,
              items: previous.list.items.map((entry) => (entry.id === item.id ? item : entry)),
            },
          }
        : previous,
    )
  }, [])

  const list = state.kind === 'ready' ? state.list : null
  const offices = useMemo(
    () => (list === null ? [] : [...new Set(list.items.map((item) => item.office))]),
    [list],
  )
  const intro = list === null
    ? 'Work staff can start now, each with a responsible office.'
    : list.can_edit
      ? 'Work staff can start now, each with a responsible office. Track who has it, when it is due and how far it has got. No leadership approval is needed, and nothing goes to an office until you send it.'
      : list.can_note
        ? 'Work staff can start now, each with a responsible office. You can follow progress and add a note for the staff working on it. No leadership approval is needed.'
        : 'Work staff can start now, each with a responsible office. This view is read only.'

  return (
    <div className="worklist">
      <p className="panel-intro">{intro}</p>

      {state.kind === 'loading' && (
        <div role="status" aria-busy="true" className="worklist-loading">
          <span className="visually-hidden">Loading the staff actions…</span>
          <div className="skeleton skeleton-line" />
          <div className="skeleton skeleton-line" />
          <div className="skeleton skeleton-line short" />
        </div>
      )}

      {state.kind === 'error' && (
        <div className="state-error state-panel error-panel" role="alert">
          <p>Couldn't load the staff actions. {state.message}</p>
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

      {list !== null && list.items.length === 0 && (
        <div className="state-empty">
          <p>
            There are no staff actions for the data in use. Actions appear here
            when the figures show students who may need support.
          </p>
        </div>
      )}

      {list !== null && list.items.length > 0 && (
        <Worklist
          list={list}
          offices={offices}
          office={office}
          onOffice={setOffice}
          status={status}
          onStatus={setStatus}
          onSaved={replace}
          onOpenEvidence={onOpenEvidence}
          onOpenInstitution={onOpenInstitution}
          viewerEmail={viewerEmail}
        />
      )}
    </div>
  )
}

function Worklist({
  list,
  offices,
  office,
  onOffice,
  status,
  onStatus,
  onSaved,
  onOpenEvidence,
  onOpenInstitution,
  viewerEmail,
}: {
  list: StaffActionList
  offices: string[]
  office: string
  onOffice: (office: string) => void
  status: StatusFilter
  onStatus: (status: StatusFilter) => void
  onSaved: (item: StaffAction) => void
  onOpenEvidence: (findingId: string) => void
  onOpenInstitution: (() => void) | null
  viewerEmail: string | null
}) {
  const counts = statusCounts(list.items)
  const shown = list.items.filter(
    (item) => (office === 'all' || item.office === office) && (status === 'all' || item.status === status),
  )
  const today = todayIso()
  // Offices whose action cannot be sent yet: no mailbox in the address book.
  const unsendable = [
    ...new Set(
      list.items
        .filter((item) => item.office_mailbox === null && item.message?.status !== 'sent')
        .map((item) => item.office),
    ),
  ]
  const noMailboxAtAll = list.items.every((item) => item.office_mailbox === null)
  return (
    <>
      {unsendable.length > 0 && (
        <div className="worklist-notice" role="note">
          <p>
            {noMailboxAtAll
              ? 'No office mailboxes are set yet, so nothing can be sent to an office.'
              : `No mailbox is set yet for ${joinNames(unsendable)}, so ${unsendable.length === 1 ? 'that action' : 'those actions'} cannot be sent.`}{' '}
            {onOpenInstitution !== null ? (
              <button type="button" className="link-button" onClick={onOpenInstitution}>
                Add {noMailboxAtAll || unsendable.length > 1 ? 'them' : 'it'} in Institution settings
              </button>
            ) : (
              `An administrator can add ${noMailboxAtAll || unsendable.length > 1 ? 'them' : 'it'} in Institution settings.`
            )}
          </p>
        </div>
      )}

      <div className="worklist-summary" role="group" aria-label="Actions by status">
        {ACTION_STATUSES.map((value) => (
          <button
            key={value}
            type="button"
            className={`summary-tile summary-${value}`}
            aria-pressed={status === value}
            onClick={() => onStatus(status === value ? 'all' : value)}
          >
            <span className="summary-count">{counts[value].toLocaleString('en-US')}</span>{' '}
            <span className="summary-label">{statusLabel(value)}</span>
          </button>
        ))}
      </div>

      <div className="worklist-filters" role="group" aria-label="Filter the actions">
        {offices.length > 1 && (
          <label className="filter-field">
            <span>Office</span>
            <select className="field" value={office} onChange={(event) => onOffice(event.target.value)}>
              <option value="all">All offices ({list.items.length})</option>
              {offices.map((name) => (
                <option key={name} value={name}>
                  {name} ({list.items.filter((item) => item.office === name).length})
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="filter-field">
          <span>Status</span>
          <select
            className="field"
            value={status}
            onChange={(event) => onStatus(event.target.value as StatusFilter)}
          >
            <option value="all">Any status</option>
            {ACTION_STATUSES.map((value) => (
              <option key={value} value={value}>
                {statusLabel(value)} ({counts[value]})
              </option>
            ))}
          </select>
        </label>
        <p className="worklist-shown" aria-live="polite">
          Showing {shown.length} of {list.items.length} action{list.items.length === 1 ? '' : 's'}
          {list.fictional ? ', demonstration data' : ''}.
        </p>
      </div>

      {shown.length === 0 ? (
        <div className="state-empty">
          <p>No actions match these choices.</p>
          <div className="state-actions">
            <button
              type="button"
              className="btn-secondary secondary"
              onClick={() => {
                onOffice('all')
                onStatus('all')
              }}
            >
              Show every action
            </button>
          </div>
        </div>
      ) : (
        <ul className="action-cards">
          {shown.map((item) => (
            <li key={item.id}>
              <ActionCard
                item={item}
                list={list}
                today={today}
                onSaved={onSaved}
                onOpenEvidence={onOpenEvidence}
                viewerEmail={viewerEmail}
              />
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

/** One action: what to do and its count, who has it and by when, its
 * message to the office, notes and history. */
function ActionCard({
  item,
  list,
  today,
  onSaved,
  onOpenEvidence,
  viewerEmail,
}: {
  item: StaffAction
  list: StaffActionList
  today: string
  onSaved: (item: StaffAction) => void
  onOpenEvidence: (findingId: string) => void
  viewerEmail: string | null
}) {
  const headingId = useId()
  const pastDue = isPastDue(item, today)
  return (
    <article className="action-card" aria-labelledby={headingId} data-status={item.status}>
      <header className="action-card-head">
        <p className="action-office">{item.office}</p>
        <span className={`action-status action-status-${item.status}`}>{statusLabel(item.status)}</span>
      </header>
      <h2 id={headingId} className="action-title">
        {item.title}
      </h2>
      <p className="action-count">
        {item.count !== null ? (
          <FindingLink findingId={item.finding_id} onOpen={onOpenEvidence}>
            <span className="num">{item.noun}</span>
          </FindingLink>
        ) : (
          <>The figure is not available in the data in use, so there is nothing to count yet.</>
        )}
      </p>
      <p className="action-what">{item.what}</p>

      <dl className="kv action-facts">
        {/* People who edit see owner and due date in the form below. */}
        {!list.can_edit && (
          <>
            <dt>Owner</dt>
            <dd>{ownerLabel(item.owner, item.office, viewerEmail)}</dd>
            <dt>Due</dt>
            <dd>
              {item.due_date !== null ? dueLabel(item.due_date) : 'No due date'}
              {pastDue && <strong className="past-due"> · past due</strong>}
            </dd>
          </>
        )}
        {list.can_edit && pastDue && item.due_date !== null && (
          <>
            <dt>Due</dt>
            <dd>
              <strong className="past-due">Past due</strong> since {dueLabel(item.due_date)}
            </dd>
          </>
        )}
        {item.updated_by !== null && item.updated_at !== null && (
          <>
            <dt>Last change</dt>
            <dd>
              {sentenceStart(personName(item.updated_by, viewerEmail))}, {formatTimestamp(item.updated_at)}
            </dd>
          </>
        )}
      </dl>

      {list.can_edit && (
        <ActionEditor item={item} list={list} onSaved={onSaved} viewerEmail={viewerEmail} />
      )}

      <OfficeMessage
        item={item}
        canSend={list.can_send}
        onSaved={onSaved}
        viewerEmail={viewerEmail}
      />

      <NotesFold item={item} canNote={list.can_note} onSaved={onSaved} viewerEmail={viewerEmail} />
      <HistoryFold item={item} viewerEmail={viewerEmail} />
    </article>
  )
}

/** A name at the start of a sentence: "You", but an email address keeps
 * its own case. */
function sentenceStart(name: string): string {
  return name === 'you' ? 'You' : name
}

/** "Bursar", "Bursar and Library", "Bursar, Library and Registrar". */
function joinNames(names: string[]): string {
  if (names.length <= 1) return names.join('')
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

/** Status, owner and due date, saved together with the version they were
 * opened at. A refused save (someone else saved first) keeps the person's
 * choices and shows the latest beside them. */
function ActionEditor({
  item,
  list,
  onSaved,
  viewerEmail,
}: {
  item: StaffAction
  list: StaffActionList
  onSaved: (item: StaffAction) => void
  viewerEmail: string | null
}) {
  const ids = useId()
  const [status, setStatus] = useState<ActionStatus>(item.status)
  const [owner, setOwner] = useState<string>(item.owner ?? OFFICE_OWNER)
  const [due, setDue] = useState<string>(item.due_date ?? '')
  // The version this form's choices are based on (moves on after a 409).
  const [base, setBase] = useState<StaffAction>(item)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedNote, setSavedNote] = useState(false)
  const savedRef = useRef<HTMLParagraphElement>(null)

  const changed =
    status !== base.status || owner !== (base.owner ?? OFFICE_OWNER) || due !== (base.due_date ?? '')

  useEffect(() => {
    if (savedNote) savedRef.current?.focus()
  }, [savedNote])

  const save = async () => {
    if (!changed || saving) return
    const change: ActionSave = { expected_updated_at: base.updated_at }
    if (status !== base.status) change.status = status
    if (owner !== (base.owner ?? OFFICE_OWNER)) change.owner = owner === OFFICE_OWNER ? null : owner
    if (due !== (base.due_date ?? '')) change.due_date = due === '' ? null : due
    setSaving(true)
    setError(null)
    try {
      const updated = await saveStaffAction(item.id, change)
      onSaved(updated)
      setBase(updated)
      setSavedNote(true)
    } catch (caught) {
      if (caught instanceof ActionError && caught.status === 409 && caught.item !== null) {
        setError(ACTION_CHANGED_MESSAGE)
        setBase(caught.item)
        onSaved(caught.item)
      } else {
        setError(friendlyError(caught, 'The action'))
      }
    } finally {
      setSaving(false)
    }
  }

  const people = list.assignees
  const ownerKnown = owner === OFFICE_OWNER || people.includes(owner)
  return (
    <form
      className="action-editor"
      onSubmit={(event) => {
        event.preventDefault()
        void save()
      }}
    >
      <div className="action-editor-fields">
        <div className="editor-field">
          <label htmlFor={`${ids}-status`}>Status</label>
          <select
            id={`${ids}-status`}
            className="field"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value as ActionStatus)
              setSavedNote(false)
            }}
          >
            {ACTION_STATUSES.map((value) => (
              <option key={value} value={value}>
                {statusLabel(value)}
              </option>
            ))}
          </select>
        </div>
        <div className="editor-field">
          <label htmlFor={`${ids}-due`}>Due date</label>
          <input
            id={`${ids}-due`}
            className="field"
            type="date"
            value={due}
            onChange={(event) => {
              setDue(event.target.value)
              setSavedNote(false)
            }}
          />
        </div>
        <div className="editor-field editor-field-owner">
          <label htmlFor={`${ids}-owner`}>Owner</label>
          <select
            id={`${ids}-owner`}
            className="field"
            value={owner}
            onChange={(event) => {
              setOwner(event.target.value)
              setSavedNote(false)
            }}
          >
            <option value={OFFICE_OWNER}>{item.office} office</option>
            {!ownerKnown && <option value={owner}>{owner}</option>}
            {people.map((email) => (
              <option key={email} value={email}>
                {ownerLabel(email, item.office, viewerEmail) === 'You' ? `${email} (you)` : email}
              </option>
            ))}
          </select>
        </div>
      </div>
      {error !== null && (
        <p className="error-line" role="alert">
          {error}
        </p>
      )}
      {error === ACTION_CHANGED_MESSAGE && (
        <p className="hint">
          Latest: {statusLabel(base.status)}, {ownerLabel(base.owner, item.office, viewerEmail)},{' '}
          {base.due_date !== null ? `due ${dueLabel(base.due_date)}` : 'no due date'}.
        </p>
      )}
      <div className="action-editor-actions">
        {changed && (
          <>
            <button
              type="submit"
              className="btn-primary primary-button"
              aria-busy={saving ? 'true' : undefined}
              onClick={(event) => {
                if (saving) event.preventDefault()
              }}
            >
              {saving && <span className="spinner" aria-hidden="true" />}
              {saving ? 'Saving…' : 'Save changes'}
            </button>
            <button
              type="button"
              className="btn-secondary secondary"
              disabled={saving}
              onClick={() => {
                setStatus(base.status)
                setOwner(base.owner ?? OFFICE_OWNER)
                setDue(base.due_date ?? '')
                setError(null)
              }}
            >
              Undo
            </button>
          </>
        )}
        {!changed && savedNote && (
          <p className="hint saved-line" role="status" tabIndex={-1} ref={savedRef}>
            Saved.
          </p>
        )}
      </div>
    </form>
  )
}

/** The action's message to the office mailbox: not sent yet (Send), sent
 * (who and when), or failed (Retry). Nothing goes without the click. */
function OfficeMessage({
  item,
  canSend,
  onSaved,
  viewerEmail,
}: {
  item: StaffAction
  canSend: boolean
  onSaved: (item: StaffAction) => void
  viewerEmail: string | null
}) {
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const message = item.message
  const sent = message !== null && message.status === 'sent'
  // A failed try, recorded by the server or just now in this browser (a
  // dropped connection leaves no record): the button then says Retry.
  const failed = (message !== null && message.status === 'failed') || error !== null

  const send = async () => {
    if (sending) return
    setSending(true)
    setError(null)
    try {
      onSaved(await sendActionToOffice(item.id))
    } catch (caught) {
      if (caught instanceof ActionError && caught.item !== null) onSaved(caught.item)
      setError(
        caught instanceof ActionError && caught.status === 503
          ? 'The message did not reach the office mailbox. Nothing was delivered. Try again, or ask your administrator to check the mail settings.'
          : friendlyError(caught, 'The message'),
      )
    } finally {
      setSending(false)
    }
  }

  return (
    <section className="office-message" aria-label={`Message to the ${item.office} office`}>
      {sent && message.sent_at !== null && (
        <p className="message-sent">
          <SentIcon />
          <span>
            Sent to the {item.office} office
            {item.office_mailbox !== null ? ` (${item.office_mailbox})` : ''} by{' '}
            {personName(message.sent_by ?? '', viewerEmail)}, {formatTimestamp(message.sent_at)}.
          </span>
        </p>
      )}
      {/* The page's one notice explains a missing mailbox; each card only
          says it cannot go yet. */}
      {!sent && item.office_mailbox === null && (
        <p className="hint message-blocked">Can't send yet: no mailbox for this office.</p>
      )}
      {!sent && item.office_mailbox === null && error !== null && (
        <p className="error-line" role="alert">
          {error}
        </p>
      )}
      {!sent && item.office_mailbox !== null && canSend && (
        <div className="message-actions">
          <div className="message-send">
            <button
              type="button"
              className="btn-secondary secondary"
              aria-busy={sending ? 'true' : undefined}
              onClick={() => void send()}
            >
              {sending && <span className="spinner" aria-hidden="true" />}
              {sending ? 'Sending…' : failed ? 'Retry sending' : `Send to ${item.office}`}
            </button>
            {(error !== null || failed) && (
              <p className="error-line" role="alert">
                {error ??
                  'The last try to send this to the office did not go through. Nothing was delivered.'}
              </p>
            )}
          </div>
          <p className="hint">
            Goes to {item.office_mailbox} with the count and a sign-in link. No student names
            or records are in the message.
          </p>
        </div>
      )}
      {!sent && item.office_mailbox !== null && !canSend && (
        <p className="hint">
          {failed
            ? 'The last try to send this to the office did not go through. A staff member can try again.'
            : 'Not sent to the office yet. A staff member sends it.'}
        </p>
      )}
      {message !== null && (
        <details className="fold">
          <summary>{sent ? 'Show the message that was sent' : 'Show the message'}</summary>
          <p className="message-subject">{message.subject}</p>
          <pre className="message-body">{message.body}</pre>
        </details>
      )}
    </section>
  )
}

function NotesFold({
  item,
  canNote,
  onSaved,
  viewerEmail,
}: {
  item: StaffAction
  canNote: boolean
  onSaved: (item: StaffAction) => void
  viewerEmail: string | null
}) {
  const noteId = useId()
  const [text, setText] = useState('')
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // "Note added", read out once the note is saved; cleared on the next edit.
  const [added, setAdded] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const length = [...text].length
  const over = length - ACTION_NOTE_MAX_CHARS

  const add = async () => {
    if (adding || text.trim() === '' || over > 0) return
    setAdding(true)
    setError(null)
    setAdded(false)
    try {
      onSaved(await addActionNote(item.id, text))
      setText('')
      setAdded(true)
      // The Add button disables once the box is empty, so focus would fall
      // to the page: it goes back to the box, ready for another note.
      textareaRef.current?.focus()
    } catch (caught) {
      setError(friendlyError(caught, 'Your note'))
    } finally {
      setAdding(false)
    }
  }

  const count = item.notes.length
  return (
    <details className="fold action-fold">
      <summary>
        {count === 0 ? (canNote ? 'Notes: add the first one' : 'Notes: none yet') : `Notes (${count})`}
      </summary>
      {count > 0 && (
        <ol className="note-list">
          {item.notes.map((note) => (
            <li key={note.id}>
              <p className="note-meta">
                {sentenceStart(personName(note.author, viewerEmail))}, {formatTimestamp(note.created_at)}
              </p>
              <p className="note-text">{note.text}</p>
            </li>
          ))}
        </ol>
      )}
      {canNote && (
        <form
          className="note-form"
          onSubmit={(event) => {
            event.preventDefault()
            void add()
          }}
        >
          <label htmlFor={noteId}>Add a note</label>
          <textarea
            id={noteId}
            ref={textareaRef}
            className="field"
            rows={3}
            value={text}
            aria-describedby={`${noteId}-help${over > 0 ? ` ${noteId}-over` : ''}`}
            aria-invalid={over > 0 ? 'true' : undefined}
            placeholder="e.g. Called the office; they start Monday."
            onChange={(event) => {
              setText(event.target.value)
              setError(null)
              setAdded(false)
            }}
          />
          <p className="hint" id={`${noteId}-help`}>
            Everyone who can see this action can read the note. Keep student names out of it.
            {' '}
            {length.toLocaleString('en-US')} of {ACTION_NOTE_MAX_CHARS.toLocaleString('en-US')} characters.
          </p>
          {over > 0 && (
            <p className="field-error" id={`${noteId}-over`}>
              {over.toLocaleString('en-US')} character{over === 1 ? '' : 's'} over the limit.
            </p>
          )}
          <button
            type="submit"
            className="btn-secondary secondary"
            disabled={!adding && (text.trim() === '' || over > 0)}
            aria-busy={adding ? 'true' : undefined}
          >
            {adding && <span className="spinner" aria-hidden="true" />}
            {adding ? 'Adding…' : 'Add note'}
          </button>
          <p className="hint note-added" role="status">
            {added ? 'Note added.' : ''}
          </p>
          {error !== null && (
            <p className="error-line" role="alert">
              {error}
            </p>
          )}
        </form>
      )}
    </details>
  )
}

function HistoryFold({ item, viewerEmail }: { item: StaffAction; viewerEmail: string | null }) {
  if (item.history.length === 0) {
    return <p className="hint action-no-history">No changes yet.</p>
  }
  return (
    <details className="fold action-fold">
      <summary>History ({item.history.length})</summary>
      <ol className="history-list">
        {[...item.history].reverse().map((change) => (
          <li key={change.id}>
            <span className="history-when">{formatTimestamp(change.at)}</span>{' '}
            <span>
              {sentenceStart(personName(change.actor, viewerEmail))}{' '}
              {changeWords(change, item.office, viewerEmail)}.
            </span>
          </li>
        ))}
      </ol>
    </details>
  )
}
