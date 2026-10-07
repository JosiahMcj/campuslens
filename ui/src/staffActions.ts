// Typed client for the staff action worklist (GET /api/staff-actions,
// PATCH /api/staff-actions/{id}, POST /api/staff-actions/{id}/notes and
// /send) and the small pure helpers its page renders with.
//
// An action is an office, a count and the figure it comes from: the API
// sends no student name or id, and this client adds none. What a person
// sets is a status from a fixed list, an owner, a due date and notes.

import { ApiError, apiDetail, apiFetch } from './auth'

export type ActionStatus = 'todo' | 'in_progress' | 'done'

export const ACTION_STATUSES: readonly ActionStatus[] = ['todo', 'in_progress', 'done']

export const ACTION_NOTE_MAX_CHARS = 1000

export interface ActionNote {
  id: number
  author: string
  text: string
  created_at: string
}

export interface ActionChange {
  id: number
  actor: string
  at: string
  change: 'status' | 'owner' | 'due_date' | 'note' | 'sent' | 'send_failed'
  from_value: string | null
  to_value: string | null
}

export interface ActionMessage {
  status: 'draft' | 'sent' | 'failed'
  to_office: string
  subject: string
  body: string
  sent_by: string | null
  sent_at: string | null
  error: string | null
}

export interface StaffAction {
  id: number
  office: string
  finding_id: string
  count: number | null
  title: string
  what: string
  noun: string
  status: ActionStatus
  owner: string | null
  due_date: string | null
  updated_by: string | null
  updated_at: string | null
  created_at: string
  notes: ActionNote[]
  history: ActionChange[]
  office_mailbox: string | null
  message: ActionMessage | null
}

export interface StaffActionList {
  dataset_id: number
  fictional: boolean
  assignees: string[]
  can_edit: boolean
  can_note: boolean
  can_send: boolean
  items: StaffAction[]
}

/** What a person saves: only the fields they changed, plus the action's
 * updated_at as they opened it (the API refuses a save over someone else's
 * with 409). owner null gives the action back to the office; due_date null
 * clears it. */
export interface ActionSave {
  status?: ActionStatus
  owner?: string | null
  due_date?: string | null
  expected_updated_at: string | null
}

/** The page's sentence when a save is refused because someone else saved
 * first: the person's own choices stay on screen. */
export const ACTION_CHANGED_MESSAGE =
  'Someone else changed this action since you opened it. Your choices are kept; check the latest below and save again.'

export function statusLabel(status: ActionStatus): string {
  switch (status) {
    case 'todo':
      return 'To do'
    case 'in_progress':
      return 'In progress'
    case 'done':
      return 'Done'
  }
}

/** Who the action is with: "you", a person, or the office itself. */
export function ownerLabel(
  owner: string | null,
  office: string,
  viewerEmail: string | null,
): string {
  if (owner === null || owner === '') return `${office} office`
  if (viewerEmail !== null && owner.toLowerCase() === viewerEmail.toLowerCase()) return 'You'
  return owner
}

/** 2026-10-20 -> "Oct 20, 2026", read as a calendar date (no time zone shift). */
export function dueLabel(due: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(due)
  if (match === null) return due
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(date)
}

/** Today's calendar date in the viewer's own zone, as YYYY-MM-DD. */
export function todayIso(now: Date = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

/** A due date that has passed on an action that is not done. */
export function isPastDue(action: Pick<StaffAction, 'due_date' | 'status'>, today: string): boolean {
  return action.status !== 'done' && action.due_date !== null && action.due_date < today
}

/** One history row in plain words ("moved it to In progress"). */
export function changeWords(change: ActionChange, office: string, viewerEmail: string | null): string {
  const status = (value: string | null) =>
    value === 'todo' || value === 'in_progress' || value === 'done' ? statusLabel(value) : 'unknown'
  switch (change.change) {
    case 'status':
      return `moved it from ${status(change.from_value)} to ${status(change.to_value)}`
    case 'owner':
      return change.to_value === null
        ? `gave it back to the ${office} office`
        : `gave it to ${ownerLabel(change.to_value, office, viewerEmail).replace(/^You$/, 'you')}`
    case 'due_date':
      return change.to_value === null
        ? 'removed the due date'
        : `set the due date to ${dueLabel(change.to_value)}`
    case 'note':
      return 'added a note'
    case 'sent':
      return `sent it to the ${office} office mailbox`
    case 'send_failed':
      return 'tried to send it to the office, but it did not go'
  }
}

/** Counts per status, for the summary strip. */
export function statusCounts(items: StaffAction[]): Record<ActionStatus, number> {
  const counts: Record<ActionStatus, number> = { todo: 0, in_progress: 0, done: 0 }
  for (const item of items) counts[item.status] += 1
  return counts
}

async function failure(response: Response, fallback: string): Promise<ApiError> {
  return new ApiError(response.status, await apiDetail(response, fallback))
}

/** An answer that carries the action as it now stands (a 409 or a failed
 * send), so the page can show the latest state next to the message. */
export class ActionError extends ApiError {
  item: StaffAction | null

  constructor(status: number, message: string, item: StaffAction | null) {
    super(status, message)
    this.name = 'ActionError'
    this.item = item
  }
}

async function actionFailure(response: Response, fallback: string): Promise<ActionError> {
  let detail = fallback
  let item: StaffAction | null = null
  try {
    const body = (await response.json()) as { detail?: unknown; item?: StaffAction }
    if (typeof body.detail === 'string') detail = body.detail
    if (body.item !== undefined) item = body.item
  } catch {
    // Not a JSON body; the fallback stands.
  }
  return new ActionError(response.status, detail, item)
}

export async function fetchStaffActions(): Promise<StaffActionList> {
  const response = await apiFetch('/staff-actions')
  if (!response.ok) throw await failure(response, 'The staff actions could not be loaded.')
  return (await response.json()) as StaffActionList
}

export async function saveStaffAction(id: number, change: ActionSave): Promise<StaffAction> {
  const response = await apiFetch(`/staff-actions/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(change),
  })
  if (!response.ok) throw await actionFailure(response, 'The action could not be saved.')
  return ((await response.json()) as { item: StaffAction }).item
}

export async function addActionNote(id: number, text: string): Promise<StaffAction> {
  const response = await apiFetch(`/staff-actions/${id}/notes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  if (!response.ok) throw await actionFailure(response, 'The note could not be added.')
  return ((await response.json()) as { item: StaffAction }).item
}

export async function sendActionToOffice(id: number): Promise<StaffAction> {
  const response = await apiFetch(`/staff-actions/${id}/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
  if (!response.ok) throw await actionFailure(response, 'The action could not be sent.')
  return ((await response.json()) as { item: StaffAction }).item
}
