// Typed client for the Financial Aid review queue (GET /api/aid-queue,
// PATCH /api/aid-queue/{id}, POST /api/decisions/{id}/aid-queue) and the
// small pure helpers its panel renders with.
//
// The queue carries facts for the aid office and nothing else: the API
// computes no outcome for any student, and this client adds none. The only
// values a person sets are a status from the API's fixed list and a note
// typed by hand, sent exactly as typed.

import { ApiError, apiDetail, apiFetch } from './auth'

export type AidStatus = 'open' | 'in_review' | 'closed'

export const AID_STATUSES: readonly AidStatus[] = ['open', 'in_review', 'closed']

export const AID_NOTE_MAX_CHARS = 1000

/** The note's length as the API counts it: characters (code points), so an
 * emoji counts once, never the two UTF-16 units `String.length` sees. */
export function noteLength(note: string): number {
  return [...note].length
}

/** What the panel says when a save is refused (409) because someone else
 * saved the row first: the person's typed text stays in the box. */
export const AID_ROW_CHANGED_MESSAGE =
  'This row changed since you opened it. Your text is kept. Compare it with the saved version and save again.'

export interface AidHoldFact {
  amount: number
  hold_date: string
  responsible_office: string
}

export interface AidFacts {
  registration_status: string
  holds: AidHoldFact[]
  advising_appointment_status: string
  advising_last_appointment_date: string | null
}

export interface AidReviewRow {
  id: number
  decision_id: string
  dataset_id: number
  student_id: string
  facts: AidFacts
  status: AidStatus
  note: string
  updated_by: string | null
  updated_at: string | null
  created_at: string
}

export interface AidQueue {
  dataset_id: number
  fictional: boolean
  rows: AidReviewRow[]
}

/** What the decision panel knows about one decision's queue (from GET
 * /decisions/{id}/dispatch): whether the decision opens one and, once it is
 * prepared, how many students it holds. */
export interface AidQueueSummary {
  supported: boolean
  count: number | null
  created_at: string | null
}

export function aidStatusLabel(status: AidStatus): string {
  switch (status) {
    case 'open':
      return 'Not started'
    case 'in_review':
      return 'In review'
    case 'closed':
      return 'Closed'
  }
}

/** A dollar amount as the briefing writes it: $412.50. */
export function formatAmount(amount: number): string {
  return `$${amount.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

/** The record's own words, made readable: not_registered -> not registered. */
export function plainValue(value: string): string {
  return value.replaceAll('_', ' ')
}

/** "Queued 18 students for Financial Aid review" (one student, singular). */
export function queuedLine(count: number): string {
  return `Queued ${count} student${count === 1 ? '' : 's'} for Financial Aid review`
}

async function failure(response: Response, fallback: string): Promise<ApiError> {
  return new ApiError(response.status, await apiDetail(response, fallback))
}

export async function fetchAidQueue(): Promise<AidQueue> {
  const response = await apiFetch('/aid-queue')
  if (!response.ok) {
    throw await failure(response, 'The review queue could not be loaded.')
  }
  return (await response.json()) as AidQueue
}

/** One save: only the fields the person changed, plus the row's updated_at
 * as it was opened, so the API refuses (409) a save over someone else's. */
export interface AidReviewChange {
  status?: AidStatus
  note?: string
  expected_updated_at: string | null
}

/** Save the changed fields of one row. The note goes exactly as typed. A row
 * that changed since it was opened throws an ApiError with status 409. */
export async function patchAidReview(
  id: number,
  change: AidReviewChange,
): Promise<AidReviewRow> {
  const response = await apiFetch(`/aid-queue/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(change),
  })
  if (!response.ok) {
    throw await failure(response, 'The review could not be saved.')
  }
  const body = (await response.json()) as { row: AidReviewRow }
  return body.row
}

/** Prepare the queue for an authorized decision (idempotent on the API). */
export async function postAidQueue(
  decisionId: string,
): Promise<{ count: number; created: boolean }> {
  const response = await apiFetch(`/decisions/${decisionId}/aid-queue`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
  if (!response.ok) {
    throw await failure(
      response,
      'The review queue could not be prepared.',
    )
  }
  return (await response.json()) as { count: number; created: boolean }
}
