// Typed client for the institution-admin office address book (GET/PUT
// /api/admin/offices), plus the pure helpers the Institution area's Offices
// section renders and validates with. The book holds office mailboxes only,
// never a student address, and it is the only place a dispatch can get a
// recipient from. A PUT replaces the whole book, so removing a row is simply
// leaving it out of the next Save.

import { apiFailure, failureFrom } from './adminErrors'
import { SessionEndedError, apiFetch } from './auth'

export interface OfficeContact {
  office: string
  email: string
}

/** The saved book, or the API's validation lines (shown folded, as
 * technical detail) when it refused the book. */
export type SaveOfficesResult =
  | { ok: true; offices: OfficeContact[] }
  | { ok: false; errors: string[] }

/** One editable row of the Offices section. `known` marks an office an
 * approved decision routes to, listed even before it has a mailbox. */
export interface OfficeDraftRow {
  key: string
  office: string
  email: string
  known: boolean
}

export interface OfficeRowErrors {
  office?: string
  email?: string
}

// The API's own shape check (backend EMAIL_RE): local@domain.tld.
const MAILBOX_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

function contactFrom(value: unknown): OfficeContact | null {
  if (typeof value !== 'object' || value === null) return null
  const record = value as Record<string, unknown>
  if (typeof record.office !== 'string' || typeof record.email !== 'string') return null
  return { office: record.office, email: record.email }
}

/** The `{offices: [...]}` body both routes answer with; exported for tests. */
export function officesFrom(body: unknown): OfficeContact[] {
  if (typeof body !== 'object' || body === null) return []
  const offices = (body as Record<string, unknown>).offices
  if (!Array.isArray(offices)) return []
  return offices.map(contactFrom).filter((contact): contact is OfficeContact => contact !== null)
}

export async function fetchOffices(): Promise<OfficeContact[]> {
  const response = await apiFetch('/admin/offices')
  if (!response.ok) throw await apiFailure(response)
  return officesFrom(await response.json().catch(() => null))
}

/**
 * The offices the current leadership decisions route their follow-up to,
 * from GET /api/decisions. Best effort: the Offices section lists them even
 * without a mailbox so the gap is visible, and any failure here yields an
 * empty list rather than an error (the address book itself still loads).
 */
export async function fetchDecisionOffices(): Promise<string[]> {
  try {
    const response = await apiFetch('/decisions')
    if (!response.ok) return []
    const body: unknown = await response.json().catch(() => null)
    if (typeof body !== 'object' || body === null) return []
    const decisions = (body as Record<string, unknown>).decisions
    if (!Array.isArray(decisions)) return []
    const offices: string[] = []
    for (const decision of decisions) {
      if (typeof decision !== 'object' || decision === null) continue
      const followUp = (decision as Record<string, unknown>).follow_up
      if (typeof followUp !== 'object' || followUp === null) continue
      const office = (followUp as Record<string, unknown>).office
      if (typeof office === 'string' && office.trim() !== '' && !offices.includes(office)) {
        offices.push(office)
      }
    }
    return offices
  } catch (error) {
    // A signed-out session still has to reach the sign-in screen.
    if (error instanceof SessionEndedError) throw error
    return []
  }
}

/**
 * PUT /api/admin/offices with the whole address book. A 422 carries the
 * API's validation lines; any other failure throws (worded on screen by
 * friendlyError).
 */
export async function saveOffices(offices: OfficeContact[]): Promise<SaveOfficesResult> {
  const response = await apiFetch('/admin/offices', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ offices }),
  })
  const body: unknown = await response.json().catch(() => null)
  if (response.ok) return { ok: true, offices: officesFrom(body) }
  const record = typeof body === 'object' && body !== null ? (body as Record<string, unknown>) : {}
  const errors = Array.isArray(record.errors)
    ? record.errors.filter((line): line is string => typeof line === 'string')
    : []
  if (response.status === 422) return { ok: false, errors }
  throw failureFrom(response.status, body)
}

/**
 * The editable rows: every office in the book, then every decision office
 * that has no mailbox yet, in that order. Keys are stable per office name.
 */
export function draftRowsFrom(
  book: OfficeContact[],
  decisionOffices: readonly string[],
): OfficeDraftRow[] {
  const rows: OfficeDraftRow[] = book.map((contact) => ({
    key: `book:${contact.office}`,
    office: contact.office,
    email: contact.email,
    known: decisionOffices.includes(contact.office),
  }))
  for (const office of decisionOffices) {
    if (!book.some((contact) => contact.office === office)) {
      rows.push({ key: `gap:${office}`, office, email: '', known: true })
    }
  }
  return rows
}

/** True for a row the admin has not filled in at all (left out of a Save). */
function isBlank(row: OfficeDraftRow): boolean {
  return row.email.trim() === '' && (row.known || row.office.trim() === '')
}

/**
 * Validate the rows exactly as the API will (non-empty office, no office
 * twice, a local@domain.tld mailbox) so an error lands under its field and
 * nothing is sent. A decision office left without a mailbox, or a new row
 * left wholly empty, is not an error: it is simply not saved.
 */
export function validateOfficeRows(rows: readonly OfficeDraftRow[]): Record<string, OfficeRowErrors> {
  const errors: Record<string, OfficeRowErrors> = {}
  const seen = new Map<string, string>()
  for (const row of rows) {
    if (isBlank(row)) continue
    const office = row.office.trim()
    const email = row.email.trim()
    const rowErrors: OfficeRowErrors = {}
    if (office === '') {
      rowErrors.office = 'Enter the office name.'
    } else {
      const key = office.toLowerCase()
      if (seen.has(key)) {
        rowErrors.office = `${office} is already listed. Each office gets one mailbox.`
      } else {
        seen.set(key, row.key)
      }
    }
    if (email === '') {
      rowErrors.email = 'Enter the mailbox address, or remove the row.'
    } else if (!MAILBOX_RE.test(email)) {
      rowErrors.email = 'Enter a mailbox address like office@example.edu.'
    }
    if (rowErrors.office !== undefined || rowErrors.email !== undefined) {
      errors[row.key] = rowErrors
    }
  }
  return errors
}

/** The PUT body from valid rows: trimmed, blanks left out. */
export function officesForSave(rows: readonly OfficeDraftRow[]): OfficeContact[] {
  return rows
    .filter((row) => !isBlank(row))
    .map((row) => ({ office: row.office.trim(), email: row.email.trim() }))
}
