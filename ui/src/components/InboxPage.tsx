import { useCallback, useEffect, useState } from 'react'

import { roleDisplayName } from '../auth'
import { friendlyError, friendlyLoadError } from '../errors'
import {
  deliveryLabel,
  fetchInbox,
  markMessage,
  reviewByLabel,
  type DecisionSnapshot,
  type ExploreSnapshot,
  type FindingSnapshot,
  type Inbox,
  type InboxMessage,
  type OverviewSnapshot,
} from '../inbox'
import { friendlyTime } from '../states'

import './Roles.css'

function who(person: InboxMessage['from']): string {
  return person === null ? 'Someone' : `${roleDisplayName(person.role)} (${person.email})`
}

/** The attachment, re-rendered from the aggregate the server stored. */
export function AlertAttachment({
  message,
  onAsk,
}: {
  message: InboxMessage
  onAsk: ((question: string) => void) | null
}) {
  const snapshot = message.snapshot
  if (message.source_kind !== 'note' && message.attachment_available === false) {
    return (
      <div className="alert-card">
        <p className="hint">
          What this alert pointed at is no longer available: the figure was withdrawn or
          changed, or your role cannot see it.
        </p>
      </div>
    )
  }
  if (snapshot === null) return null
  if (message.source_kind === 'finding') {
    const figure = snapshot as FindingSnapshot
    return (
      <div className="alert-card">
        <p className="alert-card-kicker">Briefing figure{figure.dataset ? ` · ${figure.dataset}` : ''}</p>
        <p className="alert-card-title">{figure.title}</p>
        <p className="alert-card-value">{figure.display}</p>
        {figure.definition && <p className="hint">{figure.definition}</p>}
      </div>
    )
  }
  if (message.source_kind === 'decision') {
    const decision = snapshot as DecisionSnapshot
    const due = reviewByLabel(decision.due)
    return (
      <div className="alert-card">
        <p className="alert-card-kicker">
          Approved leadership decision · for {decision.office}
          {decision.dataset ? ` · ${decision.dataset}` : ''}
        </p>
        <p className="alert-card-title">{decision.title}</p>
        <p className="alert-card-sentence">
          <strong>Approved action:</strong> {decision.action}
        </p>
        {due !== null && (
          <p className="alert-card-sentence">
            <strong>Deadline:</strong> {due.replace(/^Review by /, '')} (proposed)
          </p>
        )}
        {decision.figures.length > 0 && (
          <ul className="alert-card-figures">
            {decision.figures.map((figure) => (
              <li key={figure.id}>
                {figure.title}: <strong>{figure.display}</strong>
              </li>
            ))}
          </ul>
        )}
        <p className="hint">
          The figures are read again from the current data each time you open this. No student
          records travel with it.
        </p>
      </div>
    )
  }
  if (message.source_kind === 'overview') {
    const tile = snapshot as OverviewSnapshot
    return (
      <div className="alert-card">
        <p className="alert-card-kicker">
          {tile.department_name} overview · {tile.term}
        </p>
        <p className="alert-card-title">{tile.label}</p>
        <p className="alert-card-value">{tile.display}</p>
        {tile.note && <p className="hint">{tile.note}</p>}
      </div>
    )
  }
  const answer = snapshot as ExploreSnapshot
  return (
    <div className="alert-card">
      <p className="alert-card-kicker">
        Explore answer{answer.quoted_by_sender ? ' · quoted by the sender' : ''}
      </p>
      <p className="alert-card-title">{answer.question}</p>
      {answer.answer.map((sentence, index) => (
        <p key={index} className="alert-card-sentence">
          {sentence}
        </p>
      ))}
      {answer.answer_withheld && (
        <p className="hint">
          The answer named instructors, which your role does not see. Ask the question yourself
          for the answer your role may see.
        </p>
      )}
      {onAsk !== null && (
        <button type="button" className="link-button" onClick={() => onAsk(answer.question)}>
          Ask this question yourself
        </button>
      )}
    </div>
  )
}

/**
 * The per-account inbox: alerts others sent you (open one to see what it
 * points at, then mark it reviewed) and the alerts you sent, with whether
 * each has been read and reviewed. Opening a received alert marks it read.
 */
export function InboxPage({
  onChanged,
  onAsk,
}: {
  /** The unread count may have changed (the sidebar badge reloads). */
  onChanged: () => void
  /** Ask an attached Explore question again; null for roles that cannot. */
  onAsk: ((question: string) => void) | null
}) {
  const [inbox, setInbox] = useState<Inbox | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [tab, setTab] = useState<'received' | 'sent'>('received')
  const [openId, setOpenId] = useState<number | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setInbox(await fetchInbox())
      setLoadError(null)
    } catch (failure) {
      setLoadError(friendlyLoadError(failure))
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the fetch's setState lands after an await
    void load()
  }, [load])

  const replace = (updated: InboxMessage) =>
    setInbox((current) =>
      current === null
        ? current
        : {
            ...current,
            received: current.received.map((m) => (m.id === updated.id ? updated : m)),
            unread: current.received.filter((m) =>
              m.id === updated.id ? updated.read_at === null : m.read_at === null,
            ).length,
          },
    )

  const open = async (message: InboxMessage) => {
    const next = openId === message.id ? null : message.id
    setOpenId(next)
    setActionError(null)
    if (next === null || message.read_at !== null) return
    try {
      replace(await markMessage(message.id, 'read'))
      onChanged()
    } catch (failure) {
      setActionError(friendlyError(failure, 'Marking it read'))
    }
  }

  const review = async (message: InboxMessage) => {
    setBusyId(message.id)
    setActionError(null)
    try {
      replace(await markMessage(message.id, 'reviewed'))
      onChanged()
    } catch (failure) {
      setActionError(friendlyError(failure, 'Marking it reviewed'))
    } finally {
      setBusyId(null)
    }
  }

  if (inbox === null) {
    return loadError !== null ? (
      <div className="state-panel error-panel state-error" role="alert">
        <p>We couldn't load your inbox. {loadError}</p>
        <button type="button" className="secondary btn-secondary" onClick={() => void load()}>
          Retry
        </button>
      </div>
    ) : (
      <div role="status" aria-busy="true" className="panel-skeleton">
        <span className="visually-hidden">Loading your inbox…</span>
        <div className="skeleton skeleton-line skeleton-heading" />
        <div className="skeleton skeleton-line" />
      </div>
    )
  }

  const list = tab === 'received' ? inbox.received : inbox.sent
  return (
    <div className="inbox">
      <div className="inbox-tabs" role="tablist" aria-label="Inbox">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'received'}
          className="inbox-tab"
          onClick={() => setTab('received')}
        >
          Received{inbox.unread > 0 ? ` (${inbox.unread} new)` : ''}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'sent'}
          className="inbox-tab"
          onClick={() => setTab('sent')}
        >
          Sent
        </button>
        <button type="button" className="link-button inbox-refresh" onClick={() => void load()}>
          Refresh
        </button>
      </div>
      {actionError !== null && (
        <p className="error-line" role="alert">
          {actionError}
        </p>
      )}
      {list.length === 0 ? (
        <div className="state-panel state-empty">
          <p>
            {tab === 'received'
              ? 'Nothing here yet. When someone sends you an alert, it appears here.'
              : 'You have not sent an alert yet. Use Send alert on an answer, a figure or an overview.'}
          </p>
        </div>
      ) : (
        <ul className="inbox-list">
          {list.map((message) => {
            const due = reviewByLabel(message.review_by)
            const expanded = tab === 'sent' || openId === message.id
            const unread = tab === 'received' && message.read_at === null
            return (
              <li key={message.id} className="inbox-item" data-unread={unread ? 'true' : undefined}>
                <div className="inbox-item-head">
                  {tab === 'received' ? (
                    <button
                      type="button"
                      className="inbox-open"
                      aria-expanded={expanded}
                      onClick={() => void open(message)}
                    >
                      {unread && <span className="inbox-dot" aria-label="New" />}
                      <span className="inbox-who">From {who(message.from)}</span>
                      <span className="inbox-note-preview">{message.note}</span>
                    </button>
                  ) : (
                    <p className="inbox-who">To {who(message.to)}</p>
                  )}
                  <div className="inbox-meta">
                    {due !== null && <span className="inbox-due">{due}</span>}
                    <span>{friendlyTime(message.created_at) ?? ''}</span>
                    <span
                      className="inbox-status"
                      data-status={message.reviewed_at !== null ? 'reviewed' : message.read_at !== null ? 'read' : 'new'}
                    >
                      {deliveryLabel(message)}
                    </span>
                  </div>
                </div>
                {expanded && (
                  <div className="inbox-body">
                    <p className="inbox-note">{message.note}</p>
                    <AlertAttachment message={message} onAsk={tab === 'received' ? onAsk : null} />
                    {tab === 'received' && (
                      <div className="inbox-actions">
                        {message.reviewed_at === null ? (
                          <button
                            type="button"
                            className="btn-primary"
                            disabled={busyId === message.id}
                            aria-busy={busyId === message.id}
                            onClick={() => void review(message)}
                          >
                            {busyId === message.id
                              ? 'Saving…'
                              : message.source_kind === 'decision'
                                ? 'Acknowledge'
                                : 'Mark reviewed'}
                          </button>
                        ) : (
                          <p className="hint">
                            {message.source_kind === 'decision'
                              ? 'You acknowledged this'
                              : 'You marked this reviewed'}{' '}
                            {friendlyTime(message.reviewed_at) ?? ''}.
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
