// The audit log's filters: a kind of entry (plain groups of event types),
// who did it, and a date range in the viewer's own calendar. Pure helpers,
// shared by the log and its tests.

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
  /** An actor as recorded (an email or an AI employee), or 'all'. */
  actor: string
  /** Calendar dates (YYYY-MM-DD), inclusive; '' for no bound. */
  from: string
  to: string
}

export const NO_FILTERS: AuditFilters = { kind: 'all', actor: 'all', from: '', to: '' }

export function filterAuditEvents(events: AuditEvent[], filters: AuditFilters): AuditEvent[] {
  const group = AUDIT_FILTERS.find((entry) => entry.id === filters.kind)
  const types: readonly string[] | null = group?.types ?? null
  return events.filter((event) => {
    if (types !== null && !types.includes(event.type)) return false
    if (filters.actor !== 'all' && event.actor !== filters.actor) return false
    if (filters.from !== '' || filters.to !== '') {
      const day = localDate(event.ts)
      if (day === null) return false
      if (filters.from !== '' && day < filters.from) return false
      if (filters.to !== '' && day > filters.to) return false
    }
    return true
  })
}

