// Typed client for the counseling aggregate authorization (GET/PUT
// /api/admin/institution/counseling-authorization), plus the pure helpers
// the Institution settings section and the briefing render with.
//
// Recording the authorization never opens per-student counseling data to
// anyone. It lets CampusLens compute one count (M9), shown with no rows and
// withheld below the minimum group size.

import { apiFailure, failureFrom } from './adminErrors'
import { apiFetch } from './auth'
import type { Finding } from './api'

const URL = '/admin/institution/counseling-authorization'

export interface CounselingAuthorization {
  authorized: boolean
  authorizedBy: string | null
  documentReference: string | null
  recordedBy: string | null
  recordedAt: string | null
}

export type SaveAuthorizationResult =
  | { ok: true; authorization: CounselingAuthorization }
  | { ok: false; errors: string[] }

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null
}

/** The route's body as a typed record; anything malformed reads as off. */
export function authorizationFrom(body: unknown): CounselingAuthorization {
  const record =
    typeof body === 'object' && body !== null ? (body as Record<string, unknown>) : {}
  return {
    authorized: record.authorized === true,
    authorizedBy: text(record.authorized_by),
    documentReference: text(record.document_reference),
    recordedBy: text(record.recorded_by),
    recordedAt: text(record.recorded_at),
  }
}

export async function fetchCounselingAuthorization(): Promise<CounselingAuthorization> {
  const response = await apiFetch(URL)
  if (!response.ok) throw await apiFailure(response)
  return authorizationFrom(await response.json().catch(() => null))
}

async function put(body: Record<string, unknown>): Promise<SaveAuthorizationResult> {
  const response = await apiFetch(URL, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const parsed: unknown = await response.json().catch(() => null)
  if (response.ok) return { ok: true, authorization: authorizationFrom(parsed) }
  const record =
    typeof parsed === 'object' && parsed !== null ? (parsed as Record<string, unknown>) : {}
  const errors = Array.isArray(record.errors)
    ? record.errors.filter((line): line is string => typeof line === 'string')
    : []
  // A 422 is a refusal with its reasons; anything else throws for
  // friendlyError to word.
  if (response.status === 422) return { ok: false, errors }
  throw failureFrom(response.status, parsed)
}

/** Record the director's written authorization, as typed. */
export function recordCounselingAuthorization(
  authorizedBy: string,
  documentReference: string,
): Promise<SaveAuthorizationResult> {
  return put({
    authorized: true,
    authorized_by: authorizedBy.trim(),
    document_reference: documentReference.trim(),
  })
}

/** Revoke it. The earlier texts stay on record; M9 leaves the findings. */
export function revokeCounselingAuthorization(): Promise<SaveAuthorizationResult> {
  return put({ authorized: false })
}

// --- M9 in the briefing and the evidence drawer ---------------------------

/** True for a finding that is an aggregate with no rows behind it (M9). */
export function isAggregateOnly(finding: Pick<Finding, 'aggregate_only'>): boolean {
  return finding.aggregate_only === true
}

/** The finding's minimum group size, from the finding itself (never hardcoded). */
export function minimumGroupSize(finding: Pick<Finding, 'minimum_cell_size'>): number | null {
  const size = finding.minimum_cell_size
  return typeof size === 'number' && Number.isFinite(size) ? size : null
}

/**
 * The plain sentence that explains a withheld count, e.g. "The count is
 * withheld below 10 so no one can be identified." Null when the count is
 * shown.
 */
export function suppressionNote(
  finding: Pick<Finding, 'suppressed' | 'minimum_cell_size'>,
): string | null {
  if (finding.suppressed !== true) return null
  const size = minimumGroupSize(finding)
  return size === null
    ? 'The count is withheld because the group is small, so no one can be identified.'
    : `The count is withheld below ${size} so no one can be identified.`
}

/** The source label for M9 in the briefing: "aggregate, authorized by <name>". */
export function authorizedSourceLabel(finding: Pick<Finding, 'authorization'>): string {
  const name = text(finding.authorization?.authorized_by)
  return name === null ? 'aggregate, authorized' : `aggregate, authorized by ${name}`
}
