import { Fragment, useEffect, useState, type ReactNode } from 'react'

import type { AuditEvent } from '../api'
import { DENIED_REQUEST } from '../api'
import { aidStatusLabel, type AidStatus } from '../aid'
import { plainSentence } from '../errors'
import { fieldLabels } from '../fieldLabels'
import { findingLabel } from '../findingLabels'
import { formatTimestamp } from '../states'
import {
  ApprovedIcon,
  ChevronIcon,
  GrantedIcon,
  RefreshIcon,
  RefusedIcon,
  SentIcon,
} from './icons'

export type DeniedRequestState =
  | { kind: 'idle' }
  | { kind: 'sending' }
  | { kind: 'shown'; reason: string; eventId: number }
  | { kind: 'error'; message: string }

interface AuditLogProps {
  events: AuditEvent[] | null
  /** True for the reviewer: the log renders, but the refusal test (a POST
   * the API would refuse for this role) is not offered. */
  readOnly: boolean
  /** Reads the log again: the small refresh button, and Retry after a
   * failed load. */
  onRefresh: () => void
  deniedRequest: DeniedRequestState
  /** "Test a refusal": the Enrollment Analyst's out-of-role request. */
  onShowDeniedRequest: () => void
  /** An entry to scroll to and highlight (e.g. the refusal a chat reply
   * points at). The refusal test's own entry is highlighted too. */
  highlightEventId?: number | null
  /** The log could not be loaded: a plain sentence, shown with Retry. */
  loadError?: string | null
}

/** The "Show" filter: five plain groups instead of the raw event types. */
const AUDIT_FILTERS = [
  { id: 'all', label: 'Everything', types: null },
  {
    id: 'questions',
    label: 'Questions',
    types: ['question.asked', 'task.assigned', 'finding.produced', 'briefing.produced'],
  },
  { id: 'access', label: 'Data access', types: ['data.granted', 'data.refused'] },
  {
    id: 'decisions',
    label: 'Decisions and messages',
    types: [
      'decision.approved',
      'task.created',
      'task.dispatched',
      'task.send_failed',
      'task.sent',
      'aid.queued',
      'aid.updated',
    ],
  },
  { id: 'refusals', label: 'Refusals', types: ['data.refused'] },
] as const

type AuditFilterId = (typeof AUDIT_FILTERS)[number]['id']

function filterAuditEvents(events: AuditEvent[], filter: AuditFilterId): AuditEvent[] {
  const group = AUDIT_FILTERS.find((entry) => entry.id === filter)
  const types: readonly string[] | null = group?.types ?? null
  return types === null ? events : events.filter((event) => types.includes(event.type))
}

const ROLE_NAMES: Record<string, string> = {
  enrollment_analyst: 'the Enrollment Analyst',
  student_success_analyst: 'the Student Success Analyst',
  chief_of_staff: 'the Chief of Staff',
  executive: 'the executive',
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** Who did it, in words: an AI employee by name, a person by email. */
function actorName(actor: string): string {
  if (ROLE_NAMES[actor] !== undefined) return ROLE_NAMES[actor]
  if (actor.includes('@')) return actor
  if (/^\d+$/.test(actor)) return 'a signed-in person'
  return 'the Cabinet'
}

function str(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null
}

function list(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function quotedFields(fields: string[]): string {
  const labels = fieldLabels(fields).map((label) => `“${label}”`)
  return `${labels.join(', ')} field${labels.length === 1 ? '' : 's'}`
}

function statusWord(value: unknown): string {
  return value === 'open' || value === 'in_review' || value === 'closed'
    ? aidStatusLabel(value as AidStatus)
    : 'another status'
}

type AuditMark = 'granted' | 'refused' | 'approved' | 'sent' | null

interface DescribedEvent {
  sentence: string
  mark: AuditMark
  /** Plain label and value pairs for the "Details" fold. */
  details: [string, string][]
}

/** One audit entry as a plain sentence, its governance mark, and details. */
function describeEvent(event: AuditEvent): DescribedEvent {
  const payload = event.payload
  const who = actorName(event.actor)
  // An email address keeps its own case; a name starts the sentence.
  const Who = who.includes('@') ? who : capitalize(who)
  const details: [string, string][] = []
  const add = (label: string, value: string | null) => {
    if (value !== null && value !== '') details.push([label, value])
  }
  switch (event.type) {
    case 'question.asked':
      add('Question', str(payload.question))
      return {
        sentence: `${Who} asked “${str(payload.question) ?? 'a question'}”.`,
        mark: null,
        details,
      }
    case 'task.assigned': {
      const role = str(payload.role) ?? ''
      const fields = list(payload.fields)
      add('Figures', list(payload.findings).map((id) => findingLabel(id)).join('; '))
      add('Fields', fieldLabels(fields).join(', '))
      return {
        sentence:
          role === 'chief_of_staff'
            ? 'The Chief of Staff took on the summary, from totals only.'
            : `${Who} gave ${actorName(role)} its part of the question (${fields.length} fields).`,
        mark: null,
        details,
      }
    }
    case 'data.granted': {
      const fields = list(payload.granted_fields)
      if (payload.aggregate_only === true) {
        add('Fields read', fieldLabels(list(payload.fields_read)).join(', '))
        return {
          sentence: `${Who} was given the authorized counseling total, with no student records.`,
          mark: 'granted',
          details,
        }
      }
      add('Fields', fieldLabels(fields).join(', '))
      return {
        sentence:
          payload.level === 'aggregate'
            ? `${Who} was given totals for ${fields.length} fields, never student records.`
            : `${Who} was given access to ${fields.length} fields.`,
        mark: 'granted',
        details,
      }
    }
    case 'data.refused': {
      const refused = list(payload.refused_fields)
      if (refused.length > 0) {
        add('Fields refused', fieldLabels(refused).join(', '))
        return {
          sentence: `${Who} asked to see the ${quotedFields(refused)} and was refused before any model was called.`,
          mark: 'refused',
          details,
        }
      }
      const question = str(payload.question)
      if (question !== null) {
        add('Question', question)
        add('Reason', plainSentence(str(payload.reason)))
        return {
          sentence: `A question outside the approved list was refused: “${question}”.`,
          mark: 'refused',
          details,
        }
      }
      return {
        sentence: `A request from ${who} was refused, and nothing was changed.`,
        mark: 'refused',
        details,
      }
    }
    case 'finding.produced': {
      const findings = list(payload.findings)
      add('Figures', findings.map((id) => findingLabel(id)).join('; '))
      return {
        sentence: `${Who} finished explaining ${findings.length} figure${findings.length === 1 ? '' : 's'}.`,
        mark: null,
        details,
      }
    }
    case 'briefing.produced':
      return { sentence: 'The Chief of Staff delivered the briefing.', mark: null, details }
    case 'decision.approved':
      return { sentence: `${Who} approved the leadership decision.`, mark: 'approved', details }
    case 'task.created': {
      const task = (payload.task ?? {}) as Record<string, unknown>
      add('Office', str(task.office))
      add('Follow-up', str(task.description))
      return {
        sentence: `A follow-up for ${str(task.office) ?? 'the responsible office'} was recorded.`,
        mark: null,
        details,
      }
    }
    case 'task.dispatched':
      add('Subject', str(payload.subject))
      return {
        sentence: `${Who} prepared the message to ${str(payload.to_office) ?? 'the office'}.`,
        mark: null,
        details,
      }
    case 'task.send_failed':
      return {
        sentence: `${Who} tried to send the message to ${str(payload.to_office) ?? 'the office'}, and it did not go through.`,
        mark: 'refused',
        details,
      }
    case 'task.sent':
      return {
        sentence: `${Who} sent the message to ${str(payload.to_office) ?? 'the office'}.`,
        mark: 'sent',
        details,
      }
    case 'aid.queued': {
      const count = typeof payload.count === 'number' ? payload.count : null
      return {
        sentence:
          count === null
            ? `${Who} prepared the Financial Aid review queue.`
            : `${Who} queued ${count} student${count === 1 ? '' : 's'} for Financial Aid review.`,
        mark: null,
        details,
      }
    }
    case 'aid.updated': {
      const changedStatus = payload.status_from !== payload.status_to
      const parts: string[] = []
      if (changedStatus) {
        parts.push(`moved one student's review from ${statusWord(payload.status_from)} to ${statusWord(payload.status_to)}`)
      }
      if (payload.note_changed === true) parts.push("updated one student's review note")
      return {
        sentence: `${Who} ${parts.length > 0 ? parts.join(' and ') : "saved one student's review"}.`,
        mark: null,
        details,
      }
    }
    case 'admin.changed': {
      const by = str(payload.by) ?? 'An administrator'
      switch (payload.action) {
        case 'office_contacts':
          return { sentence: `${by} updated the office mailboxes.`, mark: null, details }
        case 'counseling_authorization':
          return {
            sentence:
              payload.authorized === true
                ? `${by} recorded the counseling authorization.`
                : `${by} turned off the counseling authorization.`,
            mark: null,
            details,
          }
        case 'created':
          return { sentence: `${by} added a person who can sign in.`, mark: null, details }
        case 'disabled':
          return { sentence: `${by} turned off one person's sign-in.`, mark: null, details }
        case 'enabled':
          return { sentence: `${by} turned on one person's sign-in.`, mark: null, details }
        case 'role_changed':
          return { sentence: `${by} changed one person's role.`, mark: null, details }
        default:
          return { sentence: `${by} changed an institution setting.`, mark: null, details }
      }
    }
    case 'dataset.uploaded':
      add('Data', str(payload.name))
      return { sentence: `${Who} uploaded new briefing data.`, mark: null, details }
    case 'dataset.activated':
      add('Data', str(payload.name))
      return { sentence: `${Who} switched the briefing to new data.`, mark: null, details }
    case 'dataset.deleted':
      add('Data', str(payload.name))
      return { sentence: `${Who} deleted briefing data.`, mark: null, details }
    default:
      return { sentence: `${Who} recorded an entry.`, mark: null, details }
  }
}

const MARKS: Record<Exclude<AuditMark, null>, { icon: ReactNode; label: string }> = {
  granted: { icon: <GrantedIcon />, label: 'Granted' },
  refused: { icon: <RefusedIcon />, label: 'Refused' },
  approved: { icon: <ApprovedIcon />, label: 'Approved' },
  sent: { icon: <SentIcon />, label: 'Sent' },
}

/** The refusal test's request, in words. */
function deniedRequestSentence(): string {
  return `${capitalize(actorName(DENIED_REQUEST.role))} asked to see the ${quotedFields([
    ...DENIED_REQUEST.fields,
  ])} and was refused before any model was called.`
}

/**
 * The audit log (Beat 6): every entry as a plain sentence, oldest first,
 * filtered by a five-option "Show" select, each with a "Details" fold of
 * plain labels and values (never the raw record). "Test a refusal" sends
 * the Enrollment Analyst's out-of-role request; the refusal is recorded and
 * its entry highlighted. An entry passed in (highlightEventId) is scrolled
 * to and highlighted the same way.
 */
export function AuditLog({
  events,
  readOnly,
  onRefresh,
  deniedRequest,
  onShowDeniedRequest,
  highlightEventId = null,
  loadError = null,
}: AuditLogProps) {
  const [filter, setFilter] = useState<AuditFilterId>('all')
  const visible = filterAuditEvents(events ?? [], filter)
  const highlightId =
    deniedRequest.kind === 'shown' ? deniedRequest.eventId : (highlightEventId ?? null)
  const highlightPresent =
    highlightId !== null && visible.some((event) => event.id === highlightId)

  useEffect(() => {
    if (!highlightPresent || highlightId === null) return
    document.getElementById(`event-${highlightId}`)?.scrollIntoView({ block: 'center' })
  }, [highlightId, highlightPresent])

  const sending = deniedRequest.kind === 'sending'

  return (
    <section aria-label="Audit log" id="audit-log" className="audit-log">
      <p className="panel-intro">
        Every question, data request, refusal and decision is recorded here and can
        never be changed. Newest entries are last.
      </p>

      <div className="audit-tools">
        <label className="audit-filter">
          <span>Show</span>{' '}
          <select
            value={filter}
            onChange={(event) => setFilter(event.target.value as AuditFilterId)}
          >
            {AUDIT_FILTERS.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>
        {!readOnly && (
          <button
            type="button"
            className="btn-secondary secondary"
            aria-busy={sending}
            onClick={() => {
              if (!sending) onShowDeniedRequest()
            }}
          >
            {sending && <span className="spinner" aria-hidden="true" />}
            {sending ? 'Testing…' : 'Test a refusal'}
          </button>
        )}
        <button
          type="button"
          className="icon-button audit-refresh"
          aria-label="Refresh the audit log"
          title="Refresh"
          onClick={onRefresh}
        >
          <RefreshIcon />
        </button>
      </div>

      {deniedRequest.kind === 'shown' && (
        <div className="refusal-card" role="status">
          <h3>
            <RefusedIcon /> Request refused
          </h3>
          <p>{deniedRequestSentence()}</p>
          <p className="hint">The refusal is recorded and highlighted in the log below.</p>
        </div>
      )}
      {deniedRequest.kind === 'error' && (
        <p className="error-line" role="alert">
          {plainSentence(deniedRequest.message) ??
            "The refusal test didn't run. Try again in a minute."}
        </p>
      )}

      {events === null ? (
        loadError !== null ? (
          <div className="state-error state-panel error-panel" role="alert">
            <p>Couldn't load the audit log. {loadError}</p>
            <button type="button" className="btn-secondary secondary" onClick={onRefresh}>
              Retry
            </button>
          </div>
        ) : (
          <p className="status-line">Loading the audit log…</p>
        )
      ) : visible.length === 0 ? (
        <p className="state-empty hint">
          {filter === 'all'
            ? 'Nothing is recorded yet. Ask an approved question and every step appears here.'
            : 'No entries of this kind yet. Choose Everything to see the whole log.'}
        </p>
      ) : (
        <ol className="event-list">
          {visible.map((event) => {
            const described = describeEvent(event)
            const highlighted = event.id === highlightId
            const mark = described.mark !== null ? MARKS[described.mark] : null
            return (
              <li
                key={event.id}
                id={`event-${event.id}`}
                className={
                  `event event-${event.type.replace('.', '-')}` +
                  (highlighted ? ' highlighted' : '')
                }
              >
                <p className="event-summary">
                  {mark !== null && (
                    <span className={`event-mark mark-${described.mark}`}>
                      {mark.icon}
                      <span className="visually-hidden">{mark.label}: </span>
                    </span>
                  )}{' '}
                  {described.sentence}
                  {highlighted && <span className="event-badge">Just recorded</span>}
                </p>
                <p className="event-ts">{formatTimestamp(event.ts)}</p>
                {described.details.length > 0 && (
                  <details className="fold technical-detail" open={highlighted}>
                    <summary>
                      <ChevronIcon />
                      Details
                    </summary>
                    <dl className="kv">
                      {described.details.map(([label, value]) => (
                        <Fragment key={label}>
                          <dt>{label}</dt>
                          <dd>{value}</dd>
                        </Fragment>
                      ))}
                    </dl>
                  </details>
                )}
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}

