// Typed client for the support programs (GET /api/interventions) and their
// governed outreach lists (/api/interventions/{program}/outreach,
// /api/outreach/{id}). Every figure arrives computed; the UI formats only.
// Outreach rows name pseudonymous students and are served only to the
// executive, the admin and the program's own office.

import { apiFailure } from './adminErrors'
import { apiFetch } from './auth'

export interface Side {
  label: string
  n: number | null
  value: number | null
}

export interface Comparison {
  method: 'before_after' | 'naive' | 'matched'
  label: string
  sentence: string
  a: Side
  b: Side
  difference: number | null
  low: number | null
  high: number | null
  withheld: boolean
  coverage_pct?: number | null
}

export interface OutcomeImpact {
  key: string
  label: string
  kind: 'pct' | 'gpa'
  better: 'higher' | 'lower'
  among: string
  comparisons: Comparison[]
  verdict: string
}

export interface ReachTerm {
  term: string
  term_name: string
  eligible: number | null
  offered: number | null
  accepted: number | null
  take_up_pct: number | null
}

export interface OutreachList {
  id: number
  program_id: string
  term: string
  status: 'pending_approval' | 'approved' | 'declined'
  count: number
  prepared_by: string
  prepared_at: string
  decided_by: string | null
  decided_at: string | null
}

export interface Program {
  id: string
  name: string
  rule: string
  offer: string
  owner_office: string
  start_term: string
  start_term_name: string
  reach: {
    current_term: string
    current_term_name: string
    eligible_now: number | null
    terms: ReachTerm[]
    total: { eligible: number | null; accepted: number | null; take_up_pct: number | null }
  }
  impact: {
    outcomes: OutcomeImpact[]
    caveat: string
    source: string
    planted: string | null
  }
  outreach: OutreachList | null
  can_prepare: boolean
  can_decide: boolean
  fact_labels: { key: string; label: string }[]
}

export interface InterventionsPage {
  current_term: string
  current_term_name: string
  fictional: boolean
  row_statuses: string[]
  programs: Program[]
}

export interface OutreachRow {
  id: number
  student_id: string
  facts: Record<string, string | number | null>
  status: RowStatus
  updated_by: string | null
  updated_at: string | null
}

export interface OutreachRows {
  list: OutreachList
  rows: OutreachRow[]
  statuses: RowStatus[]
  fact_labels: { key: string; label: string }[]
}

export type RowStatus = 'not_contacted' | 'offered' | 'accepted' | 'declined'

export const ROW_STATUS_WORDS: Record<RowStatus, string> = {
  not_contacted: 'Not contacted',
  offered: 'Offered',
  accepted: 'Accepted',
  declined: 'Declined',
}

export const LIST_STATUS_WORDS: Record<OutreachList['status'], string> = {
  pending_approval: 'Waiting for approval',
  approved: 'Approved',
  declined: 'Declined',
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) throw await apiFailure(response)
  return (await response.json()) as T
}

export async function fetchInterventions(): Promise<InterventionsPage> {
  return json(await apiFetch('/interventions'))
}

export async function prepareOutreach(programId: string): Promise<{ list: OutreachList }> {
  return json(
    await apiFetch(`/interventions/${encodeURIComponent(programId)}/outreach`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    }),
  )
}

export async function decideOutreach(
  listId: number,
  decision: 'approve' | 'decline',
): Promise<{ list: OutreachList }> {
  return json(
    await apiFetch(`/outreach/${listId}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision }),
    }),
  )
}

export async function fetchOutreachRows(listId: number): Promise<OutreachRows> {
  return json(await apiFetch(`/outreach/${listId}`))
}

export async function setOutreachRow(
  listId: number,
  rowId: number,
  status: RowStatus,
): Promise<{ status: RowStatus }> {
  return json(
    await apiFetch(`/outreach/${listId}/rows/${rowId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    }),
  )
}

/** A value as the page shows it: 91.8% or 2.74; withheld reads "fewer than 10". */
export function formatValue(value: number | null, kind: 'pct' | 'gpa'): string {
  if (value === null) return 'withheld'
  return kind === 'pct' ? `${value.toFixed(1)}%` : value.toFixed(2)
}

/** A difference: "+3.2 points" or "+0.15", with a true minus sign. */
export function formatDifference(value: number | null, kind: 'pct' | 'gpa'): string {
  if (value === null) return 'withheld'
  const sign = value > 0 ? '+' : value < 0 ? '−' : ''
  const body = kind === 'pct' ? `${Math.abs(value).toFixed(1)} points` : Math.abs(value).toFixed(2)
  return `${sign}${body}`
}

export function formatCount(value: number | null): string {
  return value === null ? 'fewer than 10' : value.toLocaleString('en-US')
}
