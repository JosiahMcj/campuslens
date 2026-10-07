// The AI staff (GET /staff, cabinet.staff in the API): one AI employee for
// each university department, with its job, the office it serves, what it
// may and may never read in plain words, and how many requests it handled
// today. Counts and words only; no student data.

import { apiFailure } from './adminErrors'
import { apiFetch } from './auth'

export interface StaffEmployee {
  role: string
  title: string
  job: string
  office: string
  /** What it may receive, always as totals, in plain words. */
  may_read: string[]
  /** What it may never read, whatever it is asked. */
  never_reads: string[]
  /** The data areas outside its job (refused if ever asked). */
  outside_scope: string[]
  /** Briefing figures it explains (M1-M8). */
  findings: string[]
  /** No data is connected for this department yet. */
  no_data: boolean
  /** The signed-in person's own department's employee. */
  yours: boolean
  requests_today: number
}

export interface StaffDirectory {
  employees: StaffEmployee[]
  yours: string[]
}

export async function fetchStaff(): Promise<StaffDirectory> {
  const response = await apiFetch('/staff')
  if (!response.ok) throw await apiFailure(response, [])
  return (await response.json()) as StaffDirectory
}

/** "A", "A and B", "A, B and C". */
export function joinTitles(titles: readonly string[]): string {
  if (titles.length <= 1) return titles.join('')
  return `${titles.slice(0, -1).join(', ')} and ${titles[titles.length - 1]}`
}

/** "Answered by: Student Accounts Analyst" under an Explore answer, or null
 * when the answer names no employee. */
export function answeredByLine(titles: readonly string[] | undefined): string | null {
  if (titles === undefined || titles.length === 0) return null
  return `Answered by: ${joinTitles(titles)}`
}
