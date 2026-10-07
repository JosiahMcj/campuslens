import { Fragment, useEffect, useMemo, useState, type ReactNode } from 'react'

import type { AuditEvent } from '../api'
import { DENIED_REQUEST } from '../api'
import { aidStatusLabel, type AidStatus } from '../aid'
import {
  AUDIT_FILTERS,
  filterAuditEvents,
  NO_FILTERS,
  peopleFrom,
  type AuditFilterId,
  type AuditFilters,
  type People,
} from '../auditFilters'
import { plainSentence } from '../errors'
import { fieldLabels } from '../fieldLabels'
import { findingLabel } from '../findingLabels'
import { getSession } from '../auth'
import { formatTimestamp, formatTimestampFull, personName } from '../states'
import {
  ApprovedIcon,
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
  /** "Show how a refusal works": the Enrollment Analyst's out-of-role
   * request. */
  onShowDeniedRequest: () => void
  /** An entry to scroll to and highlight (e.g. the refusal a chat reply
   * points at). The refusal test's own entry is highlighted too. */
  highlightEventId?: number | null
  /** The log could not be loaded: a plain sentence, shown with Retry. */
  loadError?: string | null
  /** Who is looking: their own entries read "You approved …". Defaults to
   * the signed-in person. */
  viewerEmail?: string | null
}

const ROLE_NAMES: Record<string, string> = {
  enrollment_analyst: 'the Enrollment Analyst',
  student_success_analyst: 'the Student Success Analyst',
  chief_of_staff: 'the Chief of Staff',
  executive: 'the executive',
}

/** Sign-in roles in words, for a refusal's reason. */
const SIGN_IN_ROLES: Record<string, string> = {
  admin: 'an administrator',
  executive: 'the executive',
  staff: 'staff',
  reviewer: 'the reviewer',
  aid: 'Financial Aid',
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** A name at the start of a sentence: "You", "The Chief of Staff", but an
 * email address keeps its own case. */
function sentenceStart(name: string): string {
  return name.includes('@') ? name : capitalize(name)
}

/** Quoted text inside a sentence, with no doubled punctuation: the closing
 * full stop is dropped when the quote already ends in . ? or !. */
function quoted(text: string, end = '.'): string {
  return /[.?!…]$/.test(text.trim()) ? `“${text.trim()}”` : `“${text.trim()}”${end}`
}

const NO_PEOPLE: People = { key: (actor) => actor, question: () => null }

/** Who did it, in words: an AI employee by name, the viewer as "you",
 * anyone else by email. `actor` is a key from People. */
function actorName(actor: string, viewerEmail: string | null = null): string {
  if (ROLE_NAMES[actor] !== undefined) return ROLE_NAMES[actor]
  if (actor.includes('@')) return personName(actor, viewerEmail)
  if (/^\d+$/.test(actor)) return 'a signed-in person'
  if (actor === 'anonymous') return 'someone who was not signed in'
  return 'CampusLens'
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
    ? aidStatusLabel(value as AidStatus).toLowerCase()
    : 'another status'
}

/** A staff action status as the tiles show it ("To do", "In progress"). */
function actionStatusWord(value: unknown): string {
  return value === 'todo'
    ? 'To do'
    : value === 'in_progress'
      ? 'In progress'
      : value === 'done'
        ? 'Done'
        : 'another status'
}

/** The question an Explore entry belongs to: its question entry's id, or
 * the number in its task id ("explore-14"). */
function questionEventId(payload: Record<string, unknown>): number | null {
  if (typeof payload.question_event_id === 'number') return payload.question_event_id
  const match = /^explore-(\d+)$/.exec(str(payload.task_id) ?? '')
  return match !== null ? Number(match[1]) : null
}

/** A refusal recorded with a category or a security reason, in words: the
 * sentence and a plain reason (never the recorded method, path or role code). */
function refusalWords(
  payload: Record<string, unknown>,
  who: string,
): { sentence: string; reason: string | null } {
  const recorded = str(payload.reason) ?? ''
  const plain = plainSentence(recorded)
  switch (payload.category) {
    case 'counseling':
      return {
        sentence: 'A counseling question was refused before any AI employee was asked.',
        reason: plain ?? 'Counseling and spiritual-care records are never disclosed.',
      }
    case 'individual_student':
      return {
        sentence: 'A question about a single student was refused before any AI employee was asked.',
        reason: plain ?? 'CampusLens answers with totals only, never about a single student.',
      }
    case 'prediction':
      return {
        sentence:
          'A question asking to predict what a student will do was refused before any AI employee was asked.',
        reason: plain ?? 'CampusLens does not predict what an individual student will do.',
      }
    case 'off_topic':
      return {
        sentence: 'A request that was not about the student records was turned away before any analysis ran.',
        reason: plain ?? 'CampusLens answers questions about students, courses and majors only.',
      }
        case 'instructor_level': {
      const role = SIGN_IN_ROLES[str(payload.role) ?? ''] ?? 'this person'
      return {
        sentence: `Instructor names were left out of an answer for ${role}.`,
        reason: 'Instructor-level rows are shown to the executive and administrators only.',
      }
    }
  }
  if (/no valid session/i.test(recorded)) {
    return {
      sentence: 'A request without a valid sign-in was refused.',
      reason: 'The browser was not signed in, or its sign-in had ended or been turned off.',
    }
  }
  if (/csrf|origin\/referer/i.test(recorded)) {
    return {
      sentence: 'A request without a valid security check was refused.',
      reason: /csrf/i.test(recorded)
        ? 'The request did not carry the page’s security check, so it could not be trusted.'
        : 'The request came from another site, so it could not be trusted.',
    }
  }
  const role = /^role '([a-z_]+)' is not allowed/i.exec(recorded)
  if (role !== null) {
    return {
      sentence: `A request ${SIGN_IN_ROLES[role[1]] ?? 'this role'} may not make was refused, and nothing was changed.`,
      reason: `Signed in as ${SIGN_IN_ROLES[role[1]] ?? 'a role'}, which may not do this.`,
    }
  }
  return { sentence: `A request from ${who} was refused, and nothing was changed.`, reason: plain }
}

type AuditMark = 'granted' | 'refused' | 'approved' | 'sent' | null

interface DescribedEvent {
  sentence: string
  mark: AuditMark
  /** Plain label and value pairs for the "Details" fold. */
  details: [string, string][]
}

/** One audit entry as a plain sentence, its governance mark, and details
 * (only what the sentence does not already say). */
function describeEvent(
  event: AuditEvent,
  viewerEmail: string | null,
  people: People = NO_PEOPLE,
): DescribedEvent {
  const payload = event.payload
  const who = actorName(people.key(event.actor), viewerEmail)
  // An email address keeps its own case; a name starts the sentence.
  const Who = sentenceStart(who)
  const details: [string, string][] = []
  const add = (label: string, value: string | null) => {
    if (value !== null && value !== '') details.push([label, value])
  }
  switch (event.type) {
    case 'question.asked':
      return {
        sentence: `${Who} asked ${quoted(str(payload.question) ?? 'a question')}`,
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
        const read = fieldLabels(list(payload.fields_read))
        add('Fields read', read.join(', '))
        // Only the counseling figure (M9) rests on a recorded authorization;
        // every other totals-only grant is an answer from the records.
        return {
          sentence:
            payload.finding_id === 'M9'
              ? `${Who} was given the authorized counseling total, with no student records.`
              : `${Who} was given totals for ${read.length} field${read.length === 1 ? '' : 's'} (${read.slice(0, 3).join(', ')}${read.length > 3 ? ', …' : ''}), never student records.`,
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
          sentence: `${Who} asked to see the ${quotedFields(refused)} and was refused before any AI employee was asked.`,
          mark: 'refused',
          details,
        }
      }
      const question = str(payload.question)
      if (question !== null) {
        const reason = str(payload.reason)
        add('Reason', reason === null ? null : plainSentence(reason))
        return {
          sentence: `A question outside the approved list was refused: ${quoted(question)}`,
          mark: 'refused',
          details,
        }
      }
      const words = refusalWords(payload, who)
      const asked = questionEventId(payload)
      add('Reason', words.reason)
      add('Question', asked !== null ? people.question(asked) : null)
      return { sentence: words.sentence, mark: 'refused', details }
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
    case 'student.searched': {
      const matches = typeof payload.matches === 'number' ? payload.matches : null
      return {
        sentence:
          matches === null
            ? `${Who} searched the student directory by name.`
            : `${Who} searched the student directory by name and found ${matches} student${matches === 1 ? '' : 's'}.`,
        mark: null,
        details,
      }
    }
    case 'explore.answered': {
      const steps = list(payload.steps).length
      if (payload.answered === false) {
        return {
          sentence: `${Who} could not answer a question from the records, and said so.`,
          mark: null,
          details,
        }
      }
      return {
        sentence: `${Who} answered a question from the records${steps > 0 ? `, in ${steps} step${steps === 1 ? '' : 's'}` : ''}. Only totals were used.`,
        mark: null,
        details,
      }
    }
    case 'action.updated': {
      const office = str(payload.office) ?? 'an office'
      const fields = list(payload.fields)
      const parts: string[] = []
      if (fields.includes('status')) {
        parts.push(`moved the ${office} action from ${actionStatusWord(payload.status_from)} to ${actionStatusWord(payload.status_to)}`)
      }
      if (fields.includes('owner')) parts.push(`changed who has the ${office} action`)
      if (fields.includes('due_date')) parts.push(`changed the ${office} action's due date`)
      return {
        sentence: `${Who} ${parts.length > 0 ? parts.join(' and ') : `saved the ${office} action`}.`,
        mark: null,
        details,
      }
    }
    case 'action.noted':
      return {
        sentence: `${Who} added a note to the ${str(payload.office) ?? 'office'} action.`,
        mark: null,
        details,
      }
    case 'action.sent':
      return {
        sentence: `${Who} sent the ${str(payload.office) ?? 'office'} action to that office's mailbox.`,
        mark: 'sent',
        details,
      }
    case 'action.send_failed':
      return {
        sentence: `${Who} tried to send the ${str(payload.office) ?? 'office'} action to its office, and it did not go through.`,
        mark: 'refused',
        details,
      }
    case 'admin.changed': {
      const byEmail = str(payload.by)
      const by = byEmail !== null ? sentenceStart(personName(byEmail, viewerEmail)) : 'An administrator'
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

/** Entries listed at first, and added by each "Show more". */
const PAGE_SIZE = 25

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
  ])} and was refused before any AI employee was asked.`
}

/**
 * The audit log (Beat 6): every entry as a compact plain sentence and its
 * time, newest first, filtered by a five-option "Show" select. An entry
 * with more to say than its sentence has a "Details" fold of plain labels
 * and values (never the raw record), ending with the full record time. The
 * newest 25 entries are listed first, with "Show more". "Show how a refusal
 * works" sends
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
  viewerEmail = getSession()?.user.email ?? null,
}: AuditLogProps) {
  const [filters, setFilters] = useState<AuditFilters>(NO_FILTERS)
  // How many entries are listed: the newest page first, then "Show more".
  const [limit, setLimit] = useState(PAGE_SIZE)
  const filtered = filters.kind !== 'all' || filters.actor !== 'all' || filters.from !== '' || filters.to !== ''
  const setFilter = (change: Partial<AuditFilters>) => {
    setFilters((current) => ({ ...current, ...change }))
    setLimit(PAGE_SIZE)
  }
  const people = useMemo(() => peopleFrom(events ?? []), [events])
  // Newest first: the API returns the chain in order (ascending ids).
  const visible = filterAuditEvents(events ?? [], filters, people.key)
    .slice()
    .sort((a, b) => b.id - a.id)
  // Who appears in the log, one choice per person (a person recorded by
  // role, by sign-in number and by email is still one name).
  const actorChoices = [...new Set((events ?? []).map((event) => people.key(event.actor)))]
    .map((key) => ({ key, name: sentenceStart(actorName(key, viewerEmail)) }))
    .sort((a, b) => a.name.localeCompare(b.name))
  const highlightId =
    deniedRequest.kind === 'shown' ? deniedRequest.eventId : (highlightEventId ?? null)
  const highlightIndex =
    highlightId === null ? -1 : visible.findIndex((event) => event.id === highlightId)
  const highlightPresent = highlightIndex !== -1
  // An entry to highlight is always listed, even past the first page.
  const shownCount = Math.max(limit, highlightIndex + 1)
  const shown = visible.slice(0, shownCount)
  const more = visible.length - shown.length

  useEffect(() => {
    if (!highlightPresent || highlightId === null) return
    document.getElementById(`event-${highlightId}`)?.scrollIntoView({ block: 'center' })
  }, [highlightId, highlightPresent])

  const sending = deniedRequest.kind === 'sending'

  return (
    <section aria-label="Audit log" id="audit-log" className="audit-log">
      <div className="audit-filters" role="group" aria-label="Filter the log">
        <label className="audit-filter-field">
          <span>Show</span>
          <select
            className="field"
            value={filters.kind}
            onChange={(event) => setFilter({ kind: event.target.value as AuditFilterId })}
          >
            {AUDIT_FILTERS.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>
        <label className="audit-filter-field">
          <span>Who</span>
          <select
            className="field"
            value={filters.actor}
            onChange={(event) => setFilter({ actor: event.target.value })}
          >
            <option value="all">Anyone</option>
            {actorChoices.map((choice) => (
              <option key={choice.key} value={choice.key}>
                {choice.name}
              </option>
            ))}
          </select>
        </label>
        <label className="audit-filter-field">
          <span>From</span>
          <input
            className="field"
            type="date"
            value={filters.from}
            max={filters.to !== '' ? filters.to : undefined}
            onChange={(event) => setFilter({ from: event.target.value })}
          />
        </label>
        <label className="audit-filter-field">
          <span>To</span>
          <input
            className="field"
            type="date"
            value={filters.to}
            min={filters.from !== '' ? filters.from : undefined}
            onChange={(event) => setFilter({ to: event.target.value })}
          />
        </label>
        {filtered && (
          <button
            type="button"
            className="btn-secondary secondary audit-clear"
            onClick={() => {
              setFilters(NO_FILTERS)
              setLimit(PAGE_SIZE)
            }}
          >
            Clear filters
          </button>
        )}
      </div>

      <div className="audit-tools">
        <p className="audit-count" aria-live="polite">
          {events === null
            ? ''
            : filtered
              ? `Showing ${visible.length.toLocaleString('en-US')} of ${events.length.toLocaleString('en-US')} entries.`
              : `${events.length.toLocaleString('en-US')} entr${events.length === 1 ? 'y' : 'ies'}.`}
        </p>
        {/* The refusal demonstration sits on the right beside Refresh: the
            first of the two carries .audit-refresh, which pushes them right. */}
        {!readOnly && (
          <button
            type="button"
            className="btn-secondary secondary audit-refresh"
            aria-busy={sending}
            disabled={events === null && !sending}
            onClick={() => {
              if (!sending && events !== null) onShowDeniedRequest()
            }}
          >
            {sending && <span className="spinner" aria-hidden="true" />}
            {sending ? 'Showing…' : 'Show how a refusal works'}
          </button>
        )}
        <button
          type="button"
          className={readOnly ? 'icon-button audit-refresh' : 'icon-button'}
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
          <div role="status" aria-busy="true" className="audit-loading">
            <span className="visually-hidden">Loading the audit log…</span>
            <div className="skeleton skeleton-line" />
            <div className="skeleton skeleton-line" />
            <div className="skeleton skeleton-line short" />
          </div>
        )
      ) : visible.length === 0 ? (
        <p className="state-empty hint">
          {!filtered
            ? 'Nothing is recorded yet. Ask an approved question and every step appears here.'
            : 'No entries match these filters. Clear the filters to see the whole log.'}
        </p>
      ) : (
        <>
        <ol className="event-list">
          {shown.map((event) => {
            const described = describeEvent(event, viewerEmail, people)
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
                <p className="event-ts">
                  <time dateTime={event.ts}>{formatTimestamp(event.ts)}</time>
                </p>
                {described.details.length > 0 && (
                  <details className="fold technical-detail event-details" open={highlighted}>
                    <summary>
                      Details
                    </summary>
                    <dl className="kv">
                      {described.details.map(([label, value]) => (
                        <Fragment key={label}>
                          <dt>{label}</dt>
                          <dd>{value}</dd>
                        </Fragment>
                      ))}
                      <dt>Recorded</dt>
                      <dd>{formatTimestampFull(event.ts)}</dd>
                    </dl>
                  </details>
                )}
              </li>
            )
          })}
        </ol>
        {more > 0 && (
          <div className="state-actions audit-more">
            <button
              type="button"
              className="btn-secondary secondary"
              onClick={() => setLimit(shownCount + PAGE_SIZE)}
            >
              Show {Math.min(more, PAGE_SIZE)} more
            </button>
            <p className="hint">
              Showing the newest {shown.length.toLocaleString('en-US')} of{' '}
              {visible.length.toLocaleString('en-US')}.
            </p>
          </div>
        )}
        </>
      )}
    </section>
  )
}

