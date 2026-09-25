import { useState } from 'react'

import type { AuditEvent } from '../api'
import { DENIED_REQUEST } from '../api'
import { EVENT_TYPES, filterEvents, formatTimestamp } from '../states'
import { ChevronIcon } from './icons'

export type DeniedRequestState =
  | { kind: 'idle' }
  | { kind: 'sending' }
  | { kind: 'shown'; reason: string; eventId: number }
  | { kind: 'error'; message: string }

interface AuditLogProps {
  events: AuditEvent[] | null
  /** True for the reviewer: the log renders, but the denied-request demo
   * (a POST the API would refuse for this role) is not offered. */
  readOnly: boolean
  onRefresh: () => void
  deniedRequest: DeniedRequestState
  onShowDeniedRequest: () => void
}

/**
 * The audit log view (Beat 6): the append-only event list, filterable by
 * event type, rendered oldest first — newest last. Refreshed
 * automatically after every action, with a manual Refresh button. The
 * "Show a denied data request" button runs Beat 6(a): the Enrollment
 * Analyst's out-of-role field request, refused by the gate before any model
 * call, with the refusal sentence and the new data.refused event highlighted.
 */
export function AuditLog({
  events,
  readOnly,
  onRefresh,
  deniedRequest,
  onShowDeniedRequest,
}: AuditLogProps) {
  const [filter, setFilter] = useState<string | null>(null)
  const visible = filterEvents(events ?? [], filter)
  const highlightId = deniedRequest.kind === 'shown' ? deniedRequest.eventId : null

  return (
    <section aria-labelledby="audit-heading" id="audit-log" className="audit-log">
      <h2 id="audit-heading">Audit log</h2>
      <p>
        Every question, grant, refusal, finding, and decision is recorded here,
        append-only. Newest events appear last.
      </p>
      {!readOnly && (
        <div className="denied-demo">
        <button
          type="button"
          className="secondary"
          onClick={onShowDeniedRequest}
          disabled={deniedRequest.kind === 'sending'}
        >
          {deniedRequest.kind === 'sending'
            ? 'Asking the gate…'
            : 'Show a denied data request'}
        </button>
        <p className="hint">
          Sends the Enrollment Analyst request for{' '}
          <code>{DENIED_REQUEST.fields.join(', ')}</code>, a field outside its
          role, to the field-request gate. The request is refused before any
          model call, and the refusal is recorded here.
        </p>
        {deniedRequest.kind === 'shown' && (
          <div className="refusal-card" role="status">
            <h3>Request refused</h3>
            <p>{deniedRequest.reason}</p>
            <p className="hint">
              Recorded as <code>data.refused</code> event #{deniedRequest.eventId},
              highlighted in the log below.
            </p>
          </div>
        )}
        {deniedRequest.kind === 'error' && (
          <p className="error-line" role="alert">
            {deniedRequest.message}
          </p>
        )}
        </div>
      )}
      <div className="filter-bar" role="group" aria-label="Filter events by type">
        <FilterButton
          label="All"
          active={filter === null}
          onClick={() => setFilter(null)}
        />
        {EVENT_TYPES.map((type) => (
          <FilterButton
            key={type}
            label={type}
            active={filter === type}
            onClick={() => setFilter(type)}
          />
        ))}
        <button type="button" className="secondary refresh" onClick={onRefresh}>
          Refresh
        </button>
      </div>
      {events === null ? (
        <p className="status-line">Loading the audit log…</p>
      ) : visible.length === 0 ? (
        <p className="hint">
          No events{filter !== null ? ` of type ${filter}` : ''} yet. Ask the
          approved question above, or type an out-of-scope question to see a
          refusal recorded.
        </p>
      ) : (
        <ol className="event-list">
          {visible.map((event) => (
            <li
              key={event.id}
              id={`event-${event.id}`}
              className={
                `event event-${event.type.replace('.', '-')}` +
                (event.id === highlightId ? ' highlighted' : '')
              }
            >
              <div className="event-line">
                <span className="event-id">#{event.id}</span>
                <span className="event-ts">{formatTimestamp(event.ts)}</span>
                <strong className="event-type">{event.type}</strong>
                <span className="event-actor">{event.actor}</span>
                {event.id === highlightId && (
                  <span className="event-badge">new, from the denied request</span>
                )}
              </div>
              <EventSummary event={event} />
              <details open={event.id === highlightId}>
                <summary>
                  <ChevronIcon />
                  Full payload
                </summary>
                <pre>{JSON.stringify(event.payload, null, 2)}</pre>
              </details>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

function FilterButton({
  label,
  active,
  onClick,
}: {
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      className={active ? 'filter active' : 'filter'}
      aria-pressed={active}
      onClick={onClick}
    >
      {label}
    </button>
  )
}

/** A one-line human summary of an event's payload, when one is obvious. */
function EventSummary({ event }: { event: AuditEvent }) {
  const payload = event.payload
  if (event.type === 'question.asked' && typeof payload.question === 'string') {
    return <p className="event-summary">“{payload.question}”</p>
  }
  if (event.type === 'data.refused' && typeof payload.reason === 'string') {
    const refused = Array.isArray(payload.refused_fields)
      ? ` Refused fields: ${payload.refused_fields.join(', ')}.`
      : ''
    return (
      <p className="event-summary refusal">
        {payload.reason}
        {refused}
      </p>
    )
  }
  if (event.type === 'task.created') {
    const task = payload.task as Record<string, unknown> | undefined
    if (task && typeof task.id === 'string') {
      return (
        <p className="event-summary">
          {task.id}: {String(task.status ?? '')}
        </p>
      )
    }
  }
  if (event.type === 'decision.approved' && typeof payload.decision_id === 'string') {
    return <p className="event-summary">Decision {payload.decision_id} approved.</p>
  }
  return null
}
