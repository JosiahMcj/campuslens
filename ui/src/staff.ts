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

/** Every AI employee's title by its audit actor (cabinet.staff.EMPLOYEES in
 * the API; a backend test keeps the two lists equal). */
export const EMPLOYEE_TITLES: Readonly<Record<string, string>> = {
  chief_of_staff: 'Chief of Staff',
  enrollment_analyst: 'Enrollment Analyst',
  student_success_analyst: 'Student Success Analyst',
  registrar_analyst: 'Registrar Analyst',
  student_accounts_analyst: 'Student Accounts Analyst',
  financial_aid_analyst: 'Financial Aid Analyst',
  advising_analyst: 'Advising Analyst',
  student_life_analyst: 'Student Life Analyst',
  academic_affairs_analyst: 'Academic Affairs Analyst',
  institutional_research_analyst: 'Institutional Research Analyst',
  admissions_analyst: 'Admissions Analyst',
  career_outcomes_analyst: 'Career & Alumni Outcomes Analyst',
  advancement_analyst: 'Advancement Analyst',
  finance_budget_analyst: 'Finance & Budget Analyst',
  it_data_steward: 'IT & Data Steward',
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
