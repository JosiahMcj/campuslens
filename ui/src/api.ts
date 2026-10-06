// Typed client for the governance API, called through the Vite dev proxy
// at /api/* (see vite.config.ts). The UI does no arithmetic on metrics: it
// renders `display` strings and row-ID lists from the findings object exactly.
// Every call goes through apiFetch (ui/src/auth.ts): state-changing requests
// carry the session's X-CSRF-Token, and a 401 anywhere signs the UI out.

import type { AidQueueSummary } from './aid'
import { ApiError, SessionEndedError, apiDetail, apiFetch, retryAfterFrom } from './auth'
import { exploreResponseFrom, type ExploreCatalog, type ExploreResponse } from './explore'
import {
  analystFromResponse,
  cabinetBriefingFrom,
  type AnalystBriefing,
  type AuditEvent,
  type CabinetBriefing,
  type UiFlags,
} from './states'

export type { AnalystBriefing, AuditEvent, CabinetBriefing } from './states'
export type { ExploreCatalog, ExploreResponse } from './explore'

export interface OfficeHolds {
  office: string
  count: number
  hold_row_ids: string[]
}

export interface RatioRowIds {
  numerator: string[]
  denominator: string[]
}

/** One support indicator rule's evidence row (M8): the rule, its
 * plain-language reason, the fields it reads, and its aggregate count. The
 * per-student `row_ids` reach only the evidence drawer; models never see
 * them (the API strips row-level detail from role-scoped findings). */
export interface IndicatorRuleRow {
  id: string
  title: string
  reason: string
  fields_read: string[]
  count: number
  row_ids: string[]
}

export interface Finding {
  id: string
  title: string
  value: number | OfficeHolds[] | null
  display: string
  reason: string | null
  comparison: Record<string, unknown> | null
  source_fields: string[]
  row_ids: string[] | RatioRowIds
  definition: string
  closed?: boolean
  /** M8 only: the support indicator rules, each with count and fields read. */
  rules?: IndicatorRuleRow[]
  /** M8 only: pseudonymous student id -> the rule ids that fired for it. */
  row_rules?: Record<string, string[]>
  /** M9 only: an aggregate with no rows behind it, ever (no drill-down). */
  aggregate_only?: boolean
  /** M9 only: true when the count is withheld below the minimum group size. */
  suppressed?: boolean
  /** M9 only: the minimum group size below which the count is withheld. */
  minimum_cell_size?: number
  /** M9 only: the recorded authorization the figure rests on. */
  authorization?: FindingAuthorization
}

/** Who authorized an aggregate in writing, the document, and who recorded
 * it when (M9, the counseling aggregate). */
export interface FindingAuthorization {
  authorized_by: string | null
  document_reference: string | null
  recorded_by: string | null
  recorded_at: string | null
}

export interface FindingsMeta {
  as_of: string | null
  fixture: string
  terms: Record<string, string | null>
  /** True when the active dataset is the fictional demonstration set. */
  fictional?: boolean
  /** The active dataset these findings were computed from. */
  dataset?: { id: number; name: string; sha256: string }
}

export interface Findings {
  meta: FindingsMeta
  [id: string]: Finding | FindingsMeta
}

export function getFinding(findings: Findings, id: string): Finding | undefined {
  const value = findings[id]
  if (typeof value === 'object' && value !== null && 'source_fields' in value) {
    return value as Finding
  }
  return undefined
}

export const FINDING_IDS = ['M1', 'M2', 'M3', 'M4', 'M5', 'M6', 'M7', 'M8'] as const

export interface AskTask {
  task_id: string
  role: string
  granted_fields: string[]
  findings: string[]
  /** 'aggregate' on the Chief of Staff's task entry; absent on the analysts'. */
  level?: string
}

/** One approved question from GET /questions (the registry). */
export interface ApprovedQuestion {
  id: string
  text: string
}

export type AskResponse =
  | {
      accepted: true
      /** The registry id of the matched question (e.g. "unresolved-holds"). */
      question_id: string
      question: string
      tasks: AskTask[]
      findings: Record<string, Record<string, string>>
      /** The whole seven-section briefing this ask produced. */
      briefing: CabinetBriefing
      event_ids: number[]
    }
  | { accepted: false; refusal: string; event_ids: number[] }

export interface Decision {
  id: string
  title: string
  text: string
  follow_up: { office: string; description: string }
  approved: boolean
  /** The approver's email and the approval time (ISO), once approved. */
  approved_by?: string | null
  approved_at?: string | null
}

export interface SimulatedTask {
  id: string
  decision_id: string
  office: string
  description: string
  status: string
}

export interface ApproveResponse {
  task: SimulatedTask
  created: boolean
  event_ids: number[]
}

/**
 * POST /governance/request — the §5 field-request gate demo. A refused
 * request comes back with the one-sentence reason and the `data.refused`
 * event it logged; HTTP is still 200, so the gate's answer is in `granted`.
 */
export type GovernanceResponse =
  | { granted: true; role: string; granted_fields: string[] }
  | {
      granted: false
      role: string
      refused_fields: string[]
      reason: string
      event: AuditEvent
    }

/** The Beat 6 request: the Enrollment Analyst asks for hold amounts. */
export const DENIED_REQUEST = {
  role: 'enrollment_analyst',
  fields: ['holds.amount'],
} as const

export async function postGovernanceRequest(
  flags: UiFlags,
): Promise<GovernanceResponse> {
  await maybeSlow(flags)
  return apiPost<GovernanceResponse>('/governance/request', DENIED_REQUEST)
}

const SLOW_DELAY_MS = 3000

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function maybeSlow(flags: UiFlags): Promise<void> {
  if (flags.slow) await sleep(SLOW_DELAY_MS)
}

async function apiGet<T>(path: string): Promise<T> {
  const response = await apiFetch(path)
  if (!response.ok) {
    throw new ApiError(
      response.status,
      await apiDetail(response, `GET ${path} failed: HTTP ${response.status}`),
      retryAfterFrom(response),
    )
  }
  return (await response.json()) as T
}

async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const response = await apiFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    throw new ApiError(
      response.status,
      await apiDetail(response, `POST ${path} failed: HTTP ${response.status}`),
      retryAfterFrom(response),
    )
  }
  return (await response.json()) as T
}

export async function fetchQuestions(flags: UiFlags): Promise<ApprovedQuestion[]> {
  await maybeSlow(flags)
  const body = await apiGet<unknown>('/questions')
  if (!Array.isArray(body)) return []
  return body.filter(
    (item): item is ApprovedQuestion =>
      typeof item === 'object' &&
      item !== null &&
      typeof (item as ApprovedQuestion).id === 'string' &&
      typeof (item as ApprovedQuestion).text === 'string',
  )
}

export async function fetchFindings(flags: UiFlags): Promise<Findings> {
  if (flags.failFindings) {
    throw new Error(
      'Forced failure via ?fail=findings. The API call was never made.',
    )
  }
  await maybeSlow(flags)
  return apiGet<Findings>('/findings')
}

export async function fetchEvents(flags: UiFlags): Promise<AuditEvent[]> {
  await maybeSlow(flags)
  const body = await apiGet<{ events: AuditEvent[] }>('/events')
  return body.events
}

export async function fetchDecisions(flags: UiFlags): Promise<Decision[]> {
  await maybeSlow(flags)
  // {question_id, decisions} for the latest question asked (the registry
  // default before anything is asked); the panel approves by decision id.
  const body = await apiGet<{ question_id: string | null; decisions: Decision[] }>(
    '/decisions',
  )
  return body.decisions
}

export async function postAsk(question: string, flags: UiFlags): Promise<AskResponse> {
  await maybeSlow(flags)
  const raw = await apiPost<Record<string, unknown>>('/ask', { question })
  if (raw.accepted !== true) {
    return raw as unknown as Extract<AskResponse, { accepted: false }>
  }
  const briefing = cabinetBriefingFrom(raw.briefing)
  if (briefing === null) {
    throw new Error('The /ask response did not carry a produced briefing.')
  }
  return { ...(raw as object), accepted: true, briefing } as AskResponse
}

/**
 * GET /briefing — the last briefing this API process produced, so a page
 * reload renders it without re-running anything. 404 (none yet) maps to
 * null, exactly like first load. Fetched once per page load; after Ask the
 * /ask response itself carries the briefing.
 */
export function fetchCabinetBriefingOnce(
  flags: UiFlags,
): Promise<CabinetBriefing | null> {
  let pending = cabinetBriefingOnce.get('briefing')
  if (pending === undefined) {
    pending = (async () => {
      if (flags.modelDown) return null
      await maybeSlow(flags)
      let response: Response
      try {
        response = await apiFetch('/briefing')
      } catch (error) {
        if (error instanceof SessionEndedError) throw error
        return null
      }
      if (response.status === 404) return null
      if (!response.ok) {
        throw new ApiError(
          response.status,
          await apiDetail(response, `GET /briefing failed: HTTP ${response.status}`),
      retryAfterFrom(response),
        )
      }
      const body: unknown = await response.json().catch(() => null)
      return cabinetBriefingFrom(body)
    })()
    cabinetBriefingOnce.set('briefing', pending)
  }
  return pending
}

const cabinetBriefingOnce = new Map<string, Promise<CabinetBriefing | null>>()

export async function postApprove(
  decisionId: string,
  flags: UiFlags,
): Promise<ApproveResponse> {
  await maybeSlow(flags)
  return apiPost<ApproveResponse>('/decisions/approve', { decision_id: decisionId })
}

// --- The governed execution step: dispatches --------------------------------
//
// An approved decision's follow-up can go to its responsible office, but only
// when a named person clicks Send. The message is composed by the API from the
// findings (never the model, never free text here); the UI shows it read-only
// and never edits it.

export interface DispatchRecord {
  id: number
  task_id: string
  to_office: string
  channel: string
  subject: string
  body: string
  status: 'draft' | 'sent' | 'failed'
  created_by: string
  created_at: string
  sent_by: string | null
  sent_at: string | null
  provider: string | null
  provider_ref: string | null
  error: string | null
}

/** GET /decisions/{id}/dispatch: approval, the office mailbox, and the
 * draft or sent record when one exists. */
export interface DispatchInfo {
  decision_id: string
  task_id: string
  office: string
  office_contact: string | null
  approved: boolean
  approved_by?: string | null
  approved_at?: string | null
  dispatch: DispatchRecord | null
  /** The Financial Aid review queue for this decision: counts only. */
  aid_queue?: AidQueueSummary
}

export async function fetchDispatch(
  decisionId: string,
  flags: UiFlags,
): Promise<DispatchInfo> {
  await maybeSlow(flags)
  return apiGet<DispatchInfo>(`/decisions/${decisionId}/dispatch`)
}

export async function postComposeDispatch(
  decisionId: string,
  flags: UiFlags,
): Promise<{ dispatch: DispatchRecord; created: boolean }> {
  await maybeSlow(flags)
  return apiPost(`/decisions/${decisionId}/dispatch`, {})
}

export async function postSendDispatch(
  decisionId: string,
  flags: UiFlags,
): Promise<{ dispatch: DispatchRecord }> {
  await maybeSlow(flags)
  return apiPost(`/decisions/${decisionId}/dispatch/send`, {})
}

/**
 * GET /briefing/<role>. A provider that cannot answer yields HTTP 503
 * with {available: false, reason}; both map to the "model unavailable" branch
 * while the metrics and evidence keep rendering. Both analyst routes
 * (enrollment, student-success) share this shape and behaviour.
 */
async function fetchAnalystBriefing(
  path: string,
  flags: UiFlags,
): Promise<AnalystBriefing> {
  if (flags.modelDown) {
    return analystFromResponse(200, null, true)
  }
  await maybeSlow(flags)
  let response: Response
  try {
    response = await apiFetch(path)
  } catch (error) {
    if (error instanceof SessionEndedError) throw error
    return {
      kind: 'unavailable',
      reason: 'The briefing source could not be reached.',
    }
  }
  const body: unknown = await response.json().catch(() => null)
  return analystFromResponse(response.status, body, false)
}

const briefingOnce = new Map<string, Promise<AnalystBriefing>>()

/**
 * The once-per-page-load fetch, one promise per briefing route. React
 * StrictMode mounts the page effect twice in development; both mounts share
 * the route's one promise, so each route is fetched once no matter how many
 * times the effect runs.
 */
function fetchBriefingOnce(path: string, flags: UiFlags): Promise<AnalystBriefing> {
  let pending = briefingOnce.get(path)
  if (pending === undefined) {
    pending = fetchAnalystBriefing(path, flags)
    briefingOnce.set(path, pending)
  }
  return pending
}

/** Forget the once-per-load promises (used by tests). */
export function resetBriefingOnce(): void {
  briefingOnce.clear()
  cabinetBriefingOnce.clear()
}

/** The Enrollment Analyst's section, fetched once per page load. */
export function fetchEnrollmentBriefingOnce(flags: UiFlags): Promise<AnalystBriefing> {
  return fetchBriefingOnce('/briefing/enrollment', flags)
}

/** The Student Success Analyst's section, fetched once per page load. */
export function fetchStudentSuccessBriefingOnce(
  flags: UiFlags,
): Promise<AnalystBriefing> {
  return fetchBriefingOnce('/briefing/student-success', flags)
}

/**
 * "Check again": ask the API to re-run the analyst via
 * POST /briefing/<role>/refresh (the cache-bypass route). Where that
 * route does not exist yet (404/405), fall back to a plain GET.
 */
async function refreshAnalystBriefing(
  path: string,
  flags: UiFlags,
): Promise<AnalystBriefing> {
  if (flags.modelDown) {
    return analystFromResponse(200, null, true)
  }
  await maybeSlow(flags)
  try {
    const response = await apiFetch(`${path}/refresh`, {
      method: 'POST',
    })
    if (response.status !== 404 && response.status !== 405) {
      const body: unknown = await response.json().catch(() => null)
      return analystFromResponse(response.status, body, false)
    }
  } catch (error) {
    // The API is unreachable or blocked the POST; try the plain GET below. A
    // dead session (401) is not a fallback case: it signs the UI out.
    if (error instanceof SessionEndedError) throw error
  }
  return fetchAnalystBriefing(path, flags)
}

/** "Check again" for the Enrollment Analyst's section. */
export function refreshEnrollmentBriefing(flags: UiFlags): Promise<AnalystBriefing> {
  return refreshAnalystBriefing('/briefing/enrollment', flags)
}

/** "Check again" for the Student Success Analyst's section. */
export function refreshStudentSuccessBriefing(flags: UiFlags): Promise<AnalystBriefing> {
  return refreshAnalystBriefing('/briefing/student-success', flags)
}

// --- Explore: specific questions over Demonstration University --------------
//
// POST /explore answers from tables reviewed code computed; GET
// /explore/catalog lists the example questions. The aid role gets a 403 on
// both, so the page never calls them for that role (canExplore).

export async function postExplore(question: string, flags: UiFlags): Promise<ExploreResponse> {
  await maybeSlow(flags)
  return exploreResponseFrom(await apiPost<unknown>('/explore', { question }))
}

export async function fetchExploreCatalog(flags: UiFlags): Promise<ExploreCatalog> {
  await maybeSlow(flags)
  const body = await apiGet<unknown>('/explore/catalog')
  const examples =
    typeof body === 'object' && body !== null && Array.isArray((body as { examples?: unknown }).examples)
      ? (body as { examples: unknown[] }).examples.filter(
          (item): item is string => typeof item === 'string',
        )
      : []
  return { examples }
}
