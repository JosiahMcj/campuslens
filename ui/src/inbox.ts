// Typed client for the per-account inbox (GET/POST /api/inbox,
// /api/inbox/recipients, /api/inbox/{id}/read|reviewed), the department
// overviews (GET /api/departments/overview) and sign-in activity (GET
// /api/admin/sessions). The API builds every attachment's snapshot itself;
// the client only names what it points at.

import { useCallback, useEffect, useState } from 'react'

import { apiFailure, type KnownDetails } from './adminErrors'
import { ApiError, apiFetch, roleDisplayName, type Department, type Role } from './auth'

export const INBOX_NOTE_MAX_CHARS = 1000

export interface InboxPerson {
  id: number
  email: string
  role: Role
}

export interface FindingSnapshot {
  id: string
  title: string
  display: string
  definition: string | null
  reason: string | null
  dataset: string | null
}

export interface OverviewSnapshot {
  department: Department
  department_name: string
  term: string
  label: string
  display: string
  note: string
}

export interface ExploreSnapshot {
  question: string
  answer: string[]
  answer_withheld: boolean
  /** Explore keeps no copy of its answers: the text is the sender's quote. */
  quoted_by_sender?: boolean
}

export type SourceKind = 'note' | 'finding' | 'overview' | 'explore'

export interface InboxMessage {
  id: number
  from: InboxPerson | null
  to: InboxPerson | null
  note: string
  review_by: string | null
  source_kind: SourceKind
  source_ref: string | null
  snapshot: FindingSnapshot | OverviewSnapshot | ExploreSnapshot | null
  /** False when the attachment is no longer there for this reader (the
   * figure was withdrawn, or their role may no longer read it). */
  attachment_available?: boolean
  created_at: string
  read_at: string | null
  reviewed_at: string | null
}

export interface Inbox {
  received: InboxMessage[]
  sent: InboxMessage[]
  unread: number
}

/** What an alert points at, as the sender chose it. */
export type AlertSource =
  | { kind: 'note' }
  | { kind: 'finding'; ref: string; label: string }
  | { kind: 'overview'; ref: string; label: string }
  | { kind: 'explore'; question: string; answer: string[] }

const SEND_REFUSALS: KnownDetails = [
  ['short note', 'Write a short note so they know what to look at.'],
  ['at most 1000', 'The note is too long. Keep it under 1,000 characters.'],
  ['review_by', 'Choose a review-by date from the calendar, or leave it empty.'],
  ['unknown recipient', 'That person can no longer receive alerts. Choose someone else.'],
  ['other than yourself', 'Choose someone other than yourself.'],
  ['does not', 'Your role cannot attach this. Send the note on its own.'],
]

async function json<T>(response: Response, known: KnownDetails = []): Promise<T> {
  if (!response.ok) throw await apiFailure(response, known)
  return (await response.json()) as T
}

export async function fetchInbox(): Promise<Inbox> {
  return json<Inbox>(await apiFetch('/inbox'))
}

/** The people who may receive an alert with this attachment (the API
 * offers only those allowed to read it). */
export async function fetchRecipients(source: AlertSource = { kind: 'note' }): Promise<InboxPerson[]> {
  const ref = source.kind === 'finding' || source.kind === 'overview' ? source.ref : ''
  const query = new URLSearchParams({ kind: source.kind, ref })
  return json<InboxPerson[]>(await apiFetch(`/inbox/recipients?${query.toString()}`))
}

export async function sendAlert(input: {
  recipientId: number
  note: string
  reviewBy: string
  source: AlertSource
}): Promise<InboxMessage> {
  const source =
    input.source.kind === 'note'
      ? undefined
      : input.source.kind === 'explore'
        ? { kind: 'explore', question: input.source.question, answer: input.source.answer }
        : { kind: input.source.kind, ref: input.source.ref }
  const response = await apiFetch('/inbox', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      recipient_id: input.recipientId,
      note: input.note,
      review_by: input.reviewBy || null,
      ...(source !== undefined ? { source } : {}),
    }),
  })
  return json<InboxMessage>(response, SEND_REFUSALS)
}

export async function markMessage(id: number, how: 'read' | 'reviewed'): Promise<InboxMessage> {
  const response = await apiFetch(`/inbox/${id}/${how}`, { method: 'POST' })
  const body = await json<{ message: InboxMessage }>(response)
  return body.message
}

// --- department overviews ---------------------------------------------------

export interface OverviewTile {
  key: string
  label: string
  display: string
  note: string
}

export interface OverviewTable {
  key: string
  title: string
  columns: { key: string; label: string }[]
  rows: Record<string, string>[]
}

export interface DepartmentOverview {
  department: Department
  name: string
  term: { code: string; name: string }
  fictional: boolean
  institution: string
  minimum_cell_size: number
  tiles: OverviewTile[]
  tables: OverviewTable[]
}

export async function fetchOverview(department: Department): Promise<DepartmentOverview> {
  const response = await apiFetch(`/departments/overview?department=${department}`)
  if (response.status === 503) {
    throw new ApiError(
      503,
      'The school records are not installed on this server, so the overview cannot be computed.',
    )
  }
  return json<DepartmentOverview>(response)
}

// --- sign-in activity -------------------------------------------------------

export interface SessionRow {
  id: number
  email: string
  role: Role
  disabled: boolean
  live_sessions: number
  last_seen: string | null
}

export async function fetchSessions(): Promise<SessionRow[]> {
  return json<SessionRow[]>(await apiFetch('/admin/sessions'))
}

/** A message's state for its sender: Sent, Read or Reviewed. */
export function deliveryLabel(message: InboxMessage): string {
  if (message.reviewed_at !== null) return 'Reviewed'
  if (message.read_at !== null) return 'Read'
  return 'Not opened yet'
}

/** "Review by Oct 15" for a YYYY-MM-DD date (read as a calendar day). */
export function reviewByLabel(date: string | null): string | null {
  if (date === null) return null
  const [year, month, day] = date.split('-').map(Number)
  if (!year || !month || !day) return null
  const shown = new Date(year, month - 1, day).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
  return `Review by ${shown}`
}

/** How often the sidebar's unread count is checked while the app is open. */
const UNREAD_POLL_MS = 60_000

/**
 * The inbox's unread count for the sidebar badge: loaded on sign-in, every
 * minute, and whenever `refresh` is called (after opening or reviewing an
 * alert). A failed check keeps the last count; the inbox page itself shows
 * load errors.
 */
export function useInboxUnread(): [number, () => void] {
  const [unread, setUnread] = useState(0)
  const refresh = useCallback(() => {
    fetchInbox()
      .then((inbox) => setUnread(inbox.unread))
      .catch(() => undefined)
  }, [])
  useEffect(() => {
    refresh()
    const timer = window.setInterval(refresh, UNREAD_POLL_MS)
    return () => window.clearInterval(timer)
  }, [refresh])
  return [unread, refresh]
}

/** "Finance — Student Accounts (finance@demo.test)": who an alert reaches. */
export function recipientLabel(person: InboxPerson): string {
  return `${roleDisplayName(person.role)} (${person.email})`
}
