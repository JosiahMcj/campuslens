// The audit log's filters: a kind of entry (plain groups of event types),
// who did it, and a date range in the viewer's own calendar. Pure helpers,
// shared by the log and its tests, and who is who in the log (peopleFrom).

import type { AuditEvent } from './api'

/** The "Show" filter: plain groups instead of the raw event types. */
export const AUDIT_FILTERS = [
  { id: 'all', label: 'Everything', types: null },
  {
    id: 'questions',
    label: 'Questions and answers',
    types: [
      'question.asked',
      'task.assigned',
      'finding.produced',
      'briefing.produced',
      'explore.answered',
    ],
  },
  {
    id: 'access',
    label: 'Data access',
    types: ['data.granted', 'data.refused', 'student.searched'],
  },
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
  {
    id: 'actions',
    label: 'Staff actions',
    types: ['action.updated', 'action.noted', 'action.sent', 'action.send_failed'],
  },
  {
    id: 'admin',
    label: 'Data and people',
    types: ['admin.changed', 'dataset.uploaded', 'dataset.activated', 'dataset.deleted'],
  },
  { id: 'refusals', label: 'Refusals', types: ['data.refused'] },
] as const

export type AuditFilterId = (typeof AUDIT_FILTERS)[number]['id']

/** The calendar date of a recorded time in the viewer's own zone. */
function localDate(ts: string): string | null {
  const date = new Date(ts)
  if (Number.isNaN(date.getTime())) return null
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

export interface AuditFilters {
  kind: AuditFilterId
  /** A person's key (see filterAuditEvents' actorKey), or 'all'. */
  actor: string
  /** Calendar dates (YYYY-MM-DD), inclusive; '' for no bound. */
  from: string
  to: string
}

export const NO_FILTERS: AuditFilters = { kind: 'all', actor: 'all', from: '', to: '' }

/** Filters the log. `actorKey` maps a recorded actor to the person the
 * "Who" filter lists (one person can be recorded both by role and by email);
 * by default the recorded actor is the key. */
export function filterAuditEvents(
  events: AuditEvent[],
  filters: AuditFilters,
  actorKey: (actor: string) => string = (actor) => actor,
): AuditEvent[] {
  const group = AUDIT_FILTERS.find((entry) => entry.id === filters.kind)
  const types: readonly string[] | null = group?.types ?? null
  return events.filter((event) => {
    if (types !== null && !types.includes(event.type)) return false
    if (filters.actor !== 'all' && actorKey(event.actor) !== filters.actor) return false
    if (filters.from !== '' || filters.to !== '') {
      const day = localDate(event.ts)
      if (day === null) return false
      if (filters.from !== '' && day < filters.from) return false
      if (filters.to !== '' && day > filters.to) return false
    }
    return true
  })
}


function str(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null
}

/**
 * Who is who in the log. One person can be recorded three ways: by email
 * (most entries), by sign-in number (security refusals and administrator
 * changes) and, for the approved questions, by role ("executive"). The log
 * itself says which is which: an administrator change carries both the
 * number and the email, and a question carries the asker's role.
 */
export interface People {
  /** The person an actor stands for: an email (lower case) when known,
   * else the actor as recorded. */
  key: (actor: string) => string
  /** The asked question for a question entry's id (already redacted). */
  question: (eventId: number) => string | null
}

export function peopleFrom(events: AuditEvent[]): People {
  const idEmail = new Map<string, string>()
  const executives = new Set<string>()
  const questions = new Map<number, string>()
  for (const event of events) {
    const by = str(event.payload.by)
    if (/^\d+$/.test(event.actor) && by !== null) idEmail.set(event.actor, by.toLowerCase())
    if (event.type === 'question.asked') {
      const question = str(event.payload.question)
      if (question !== null) questions.set(event.id, question)
      if (event.actor.includes('@') && event.payload.role === 'executive') {
        executives.add(event.actor.toLowerCase())
      }
    }
  }
  // The approved questions record the role, not the person: when exactly
  // one executive appears in the log, that role is that person.
  const onlyExecutive = executives.size === 1 ? [...executives][0] : null
  return {
    key: (actor) => {
      if (actor.includes('@')) return actor.toLowerCase()
      if (actor === 'executive' && onlyExecutive !== null) return onlyExecutive
      return idEmail.get(actor) ?? actor
    },
    question: (eventId) => questions.get(eventId) ?? null,
  }
}
