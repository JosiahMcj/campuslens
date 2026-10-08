// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { StudentSearch } from '../students'

const searchStudents = vi.fn<(query: string) => Promise<StudentSearch>>()
vi.mock('../students', () => ({
  MIN_QUERY_CHARS: 2,
  searchStudents: (query: string) => searchStudents(query),
}))

import { StudentLookup } from './StudentLookup'

const OBINNA = {
  student_id: 'DEM1002',
  name: 'Obiora Amato',
  program: 'Computer Science',
  degree_progress: 0.74,
  degree_progress_display: '74%',
  current_gpa_display: '3.68',
  previous_gpa_display: '3.59',
  gpa_change_display: '+0.09',
  holds: [],
  advisor: 'Dr. Fitzgerald',
}

function result(overrides: Partial<StudentSearch> = {}): StudentSearch {
  return {
    query: 'obiora',
    total: 1,
    shown: 1,
    directory_size: 5000,
    fictional: true,
    students: [OBINNA],
    ...overrides,
  }
}

beforeEach(() => {
  searchStudents.mockReset()
})
afterEach(cleanup)

function search(text: string) {
  fireEvent.change(screen.getByLabelText('Student name'), { target: { value: text } })
  fireEvent.click(screen.getByRole('button', { name: 'Search' }))
}

describe('StudentLookup', () => {
  it('labels the directory fictional and says searches are logged', () => {
    render(<StudentLookup />)
    expect(screen.getByText('Fictional data')).toBeTruthy()
    expect(screen.getByText(/Each search is recorded in the audit log/)).toBeTruthy()
  })

  it("searches once on Search and shows the student's record", async () => {
    searchStudents.mockResolvedValue(result())
    render(<StudentLookup />)
    // Typing alone never searches: each search is an audit entry.
    fireEvent.change(screen.getByLabelText('Student name'), { target: { value: ' Obiora ' } })
    expect(searchStudents).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(searchStudents).toHaveBeenCalledTimes(1)
    expect(searchStudents).toHaveBeenCalledWith('Obiora')

    const card = (await screen.findByRole('heading', { name: 'Obiora Amato' })).closest('li')!
    expect(card.textContent).toContain('DEM1002')
    expect(card.textContent).toContain('ProgramComputer Science')
    expect(card.textContent).toContain('AdvisorDr. Fitzgerald')
    expect(card.textContent).toContain('74%')
    expect(card.textContent).toContain('3.68 (+0.09 from 3.59)')
    expect(card.textContent).toContain('HoldsNone')
    expect(screen.getByText('1 student found.')).toBeTruthy()
  })

  it('lists holds, and says when only the first matches are shown', async () => {
    searchStudents.mockResolvedValue(
      result({
        total: 40,
        shown: 1,
        students: [{ ...OBINNA, holds: ['Financial Balance', 'Advising Required'] }],
      }),
    )
    render(<StudentLookup />)
    search('ob')
    expect(await screen.findByText('Financial Balance')).toBeTruthy()
    expect(screen.getByText('Advising Required')).toBeTruthy()
    expect(screen.getByText(/40 students found\. Showing the first 1/)).toBeTruthy()
  })

  it('refuses a one-letter search without calling the API, and says nobody matched', async () => {
    render(<StudentLookup />)
    search('o')
    expect(searchStudents).not.toHaveBeenCalled()
    expect(screen.getByRole('alert').textContent).toContain('Type at least 2 letters')

    searchStudents.mockResolvedValue(result({ query: 'zzz', total: 0, shown: 0, students: [] }))
    search('zzz')
    expect(await screen.findByText(/No student matches “zzz”/)).toBeTruthy()
  })

  it('shows a plain error when the search fails', async () => {
    searchStudents.mockRejectedValue(new Error('boom'))
    render(<StudentLookup />)
    search('obiora')
    expect((await screen.findByRole('alert')).textContent).toContain(
      "Couldn't search the directory.",
    )
  })
})
