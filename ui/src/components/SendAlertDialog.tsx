import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'

import { friendlyError, friendlyLoadError } from '../errors'
import {
  fetchRecipients,
  INBOX_NOTE_MAX_CHARS,
  recipientLabel,
  sendAlert,
  type AlertSource,
  type InboxPerson,
} from '../inbox'
import { trapTab } from '../states'
import { CrossSmallIcon } from './icons'

import './Roles.css'

function sourceLine(source: AlertSource): string | null {
  switch (source.kind) {
    case 'note':
      return null
    case 'finding':
    case 'overview':
      return `Attached: ${source.label}`
    case 'explore':
      return `Attached: the answer to “${source.question}”`
  }
}

/**
 * Send an alert to another account's inbox: who (any enabled account of the
 * institution but yourself), a short note, and an optional review-by date.
 * What it points at is fixed by where it was opened from; the server builds
 * the attachment from the records itself, never from this screen.
 */
export function SendAlertDialog({
  source,
  onClose,
  onSent,
}: {
  source: AlertSource
  onClose: () => void
  onSent?: () => void
}) {
  const [people, setPeople] = useState<InboxPerson[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [recipient, setRecipient] = useState('')
  const [note, setNote] = useState('')
  const [reviewBy, setReviewBy] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sentTo, setSentTo] = useState<string | null>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  const opener = useRef<Element | null>(document.activeElement)
  const titleId = useId()
  const noteId = useId()
  const whoId = useId()
  const dateId = useId()

  // The attachment is fixed for the dialog's life: the people it may go to
  // are loaded once, for it.
  const [fixedSource] = useState(source)
  useEffect(() => {
    let live = true
    fetchRecipients(fixedSource)
      .then((list) => {
        if (!live) return
        setPeople(list)
        // Only one possible recipient: chosen for you. Otherwise nobody is preselected.
        setRecipient(list.length === 1 ? String(list[0].id) : '')
      })
      .catch((failure: unknown) => {
        if (live) setLoadError(friendlyLoadError(failure))
      })
    return () => {
      live = false
    }
  }, [fixedSource])

  // Focus goes back to whatever opened the dialog when it closes.
  useEffect(() => {
    const back = opener.current
    return () => {
      if (back instanceof HTMLElement) back.focus()
    }
  }, [])
  // The first field takes focus once the list of people has loaded.
  const loaded = people !== null || loadError !== null
  useEffect(() => {
    dialogRef.current?.querySelector<HTMLElement>('select, textarea')?.focus()
  }, [loaded])

  const close = () => {
    if (!busy) onClose()
  }

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      close()
      return
    }
    if (dialogRef.current !== null) trapTab(event, dialogRef.current)
  }

  const submit = async () => {
    setError(null)
    if (recipient === '') {
      setError('Choose who should see this.')
      return
    }
    if (note.trim() === '') {
      setError('Write a short note so they know what to look at.')
      return
    }
    setBusy(true)
    try {
      const message = await sendAlert({
        recipientId: Number(recipient),
        note,
        reviewBy,
        source,
      })
      setSentTo(message.to !== null ? recipientLabel(message.to) : 'them')
      onSent?.()
    } catch (failure) {
      setError(friendlyError(failure, 'Your alert'))
    } finally {
      setBusy(false)
    }
  }

  const attached = sourceLine(source)
  const remaining = INBOX_NOTE_MAX_CHARS - [...note].length

  return createPortal(
    <div className="alert-scrim" onClick={close}>
      <div
        ref={dialogRef}
        className="alert-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(event) => event.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <div className="alert-dialog-head">
          <h2 id={titleId}>Send an alert</h2>
          <button
            type="button"
            className="head-button"
            aria-label="Close"
            onClick={close}
            disabled={busy}
          >
            <CrossSmallIcon size={18} />
          </button>
        </div>
        {sentTo !== null ? (
          <div className="alert-sent" role="status">
            <p>
              Sent to {sentTo}. You can follow whether they have read and reviewed it in your
              Inbox, under Sent.
            </p>
            <div className="alert-dialog-actions">
              <button type="button" className="btn-primary" onClick={onClose}>
                Done
              </button>
            </div>
          </div>
        ) : (
          <form
            noValidate
            onSubmit={(event) => {
              event.preventDefault()
              void submit()
            }}
          >
            {attached !== null && <p className="alert-attached">{attached}</p>}
            {loadError !== null ? (
              <p className="error-line" role="alert">
                We couldn't load the people you can send to. {loadError}
              </p>
            ) : (
              <div className="alert-field">
                <label htmlFor={whoId}>Send to</label>
                <select
                  id={whoId}
                  value={recipient}
                  disabled={busy || people === null}
                  onChange={(event) => setRecipient(event.target.value)}
                >
                  <option value="">
                    {people === null
                      ? 'Loading…'
                      : people.length === 0
                        ? 'Nobody else may read this'
                        : 'Choose a person'}
                  </option>
                  {(people ?? []).map((person) => (
                    <option key={person.id} value={String(person.id)}>
                      {recipientLabel(person)}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <div className="alert-field">
              <label htmlFor={noteId}>Note</label>
              <textarea
                id={noteId}
                rows={4}
                value={note}
                disabled={busy}
                maxLength={INBOX_NOTE_MAX_CHARS}
                placeholder="What should they look at, and why?"
                onChange={(event) => setNote(event.target.value)}
              />
              <p className="hint">{remaining.toLocaleString('en-US')} characters left</p>
            </div>
            <div className="alert-field alert-field-narrow">
              <label htmlFor={dateId}>Review by (optional)</label>
              <input
                id={dateId}
                type="date"
                value={reviewBy}
                disabled={busy}
                onChange={(event) => setReviewBy(event.target.value)}
              />
            </div>
            {error !== null && (
              <p className="error-line" role="alert">
                {error}
              </p>
            )}
            <div className="alert-dialog-actions">
              <button type="button" className="secondary btn-secondary" onClick={close} disabled={busy}>
                Cancel
              </button>
              <button type="submit" className="btn-primary" disabled={busy} aria-busy={busy}>
                {busy ? 'Sending…' : 'Send alert'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>,
    document.body,
  )
}
