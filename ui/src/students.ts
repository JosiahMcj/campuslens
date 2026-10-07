// Typed client for the demonstration student directory
// (GET /api/students/search?q=). The executive and the admin only; every
// search is written to the audit log by the API. The percentages and the
// GPA change arrive as display strings: nothing is computed here.

import { apiFailure } from './adminErrors'
import { apiFetch } from './auth'

export const MIN_QUERY_CHARS = 2

export interface StudentRecord {
  student_id: string
  name: string
  program: string
  degree_progress: number
  degree_progress_display: string
  current_gpa_display: string
  previous_gpa_display: string
  gpa_change_display: string
  holds: string[]
  advisor: string
}

export interface StudentSearch {
  query: string
  total: number
  shown: number
  directory_size: number
  fictional: boolean
  students: StudentRecord[]
}

export async function searchStudents(query: string): Promise<StudentSearch> {
  const response = await apiFetch(`/students/search?q=${encodeURIComponent(query)}`)
  if (!response.ok) throw await apiFailure(response)
  return (await response.json()) as StudentSearch
}
