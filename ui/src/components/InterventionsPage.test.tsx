// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { InterventionsPage as PageData, Program } from '../interventions'

const fetchInterventions = vi.fn<() => Promise<PageData>>()
const prepareOutreach = vi.fn<(id: string) => Promise<unknown>>()
const decideOutreach = vi.fn<(id: number, d: string) => Promise<unknown>>()
vi.mock('../interventions', async (importOriginal) => {
  const real = await importOriginal<typeof import('../interventions')>()
  return {
    ...real,
    fetchInterventions: () => fetchInterventions(),
    prepareOutreach: (id: string) => prepareOutreach(id),
    decideOutreach: (id: number, d: string) => decideOutreach(id, d),
  }
})

import { InterventionsPage } from './InterventionsPage'

function program(overrides: Partial<Program> = {}): Program {
  return {
    id: 'ai_tutoring',
    name: 'AI tutoring and coaching',
    rule: 'Students whose cumulative GPA entering the term is in the bottom 30 %.',
    offer: 'Free AI tutoring and a weekly coaching check-in.',
    owner_office: 'Student Success',
    start_term: '202510',
    start_term_name: 'Fall 2024',
    reach: {
      current_term: '202620',
      current_term_name: 'Spring 2026',
      eligible_now: 4114,
      terms: [
        { term: '202510', term_name: 'Fall 2024', eligible: 3564, offered: 3564, accepted: 2050, take_up_pct: 57.5 },
      ],
      total: { eligible: 15539, accepted: 8938, take_up_pct: 57.5 },
    },
    impact: {
      outcomes: [
        {
          key: 'term_gpa',
          label: 'Term GPA',
          kind: 'gpa',
          better: 'higher',
          among: 'eligible students with a term GPA',
          verdict: 'Likely helping: participants did better than similar students.',
          comparisons: [
            {
              method: 'naive',
              label: 'Took part or not (naive)',
              sentence: 'Naive: the two groups may have differed before the program.',
              a: { label: 'Took part', n: 8933, value: 2.74 },
              b: { label: 'Did not take part', n: 6600, value: 2.53 },
              difference: 0.21,
              low: 0.19,
              high: 0.23,
              withheld: false,
            },
            {
              method: 'matched',
              label: 'Took part or not, similar GPA (fairer)',
              sentence: 'Participants against non-participants with a similar GPA.',
              a: { label: 'Took part', n: 8932, value: 2.74 },
              b: { label: 'Similar students who did not', n: 6597, value: 2.59 },
              difference: 0.15,
              low: 0.13,
              high: 0.17,
              withheld: false,
            },
            {
              method: 'before_after',
              label: 'Before and after',
              sentence: 'Everyone eligible before and after.',
              a: { label: 'Eligible since Fall 2024', n: null, value: null },
              b: { label: 'Eligible before Fall 2024', n: null, value: null },
              difference: null,
              low: null,
              high: null,
              withheld: true,
            },
          ],
        },
      ],
      caveat: "This comparison isn't a randomized trial.",
      source: 'Outcomes come from the program follow-up records.',
      planted: null,
    },
    outreach: null,
    can_prepare: false,
    can_decide: false,
    fact_labels: [],
    ...overrides,
  }
}

function page(programs: Program[]): PageData {
  return {
    current_term: '202620',
    current_term_name: 'Spring 2026',
    fictional: true,
    row_statuses: ['not_contacted', 'offered', 'accepted', 'declined'],
    programs,
  }
}

beforeEach(() => {
  fetchInterventions.mockReset()
  prepareOutreach.mockReset()
  decideOutreach.mockReset()
})
afterEach(cleanup)

describe('InterventionsPage', () => {
  it('shows the rule, the offer, the comparisons and the caveat', async () => {
    fetchInterventions.mockResolvedValue(page([program()]))
    render(<InterventionsPage />)
    expect(await screen.findByRole('heading', { name: 'AI tutoring and coaching' })).toBeTruthy()
    expect(screen.getByText(/bottom 30 %/)).toBeTruthy()
    expect(screen.getByText(/weekly coaching check-in/)).toBeTruthy()
    expect(screen.getByText(/Likely helping/)).toBeTruthy()
    expect(screen.getByText("This comparison isn't a randomized trial.")).toBeTruthy()
    expect(screen.getByText('+0.15')).toBeTruthy()
    expect(screen.getByText('Withheld: a group is under 10 students.')).toBeTruthy()
    // A role without per-student rows sees no outreach button.
    expect(screen.queryByRole('button', { name: 'Prepare outreach list' })).toBeNull()
  })

  it('prepares a list for a role allowed per-student rows, then reloads', async () => {
    fetchInterventions.mockResolvedValue(page([program({ can_prepare: true, can_decide: true })]))
    prepareOutreach.mockResolvedValue({})
    render(<InterventionsPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Prepare outreach list' }))
    await waitFor(() => expect(prepareOutreach).toHaveBeenCalledWith('ai_tutoring'))
    await waitFor(() => expect(fetchInterventions).toHaveBeenCalledTimes(2))
  })

  it('offers approval of a pending list to the executive', async () => {
    const pending = {
      id: 7,
      program_id: 'ai_tutoring',
      term: '202620',
      status: 'pending_approval' as const,
      count: 4114,
      prepared_by: 'admin@test.example',
      prepared_at: '2026-10-07T12:00:00+00:00',
      decided_by: null,
      decided_at: null,
    }
    fetchInterventions.mockResolvedValue(
      page([program({ can_prepare: true, can_decide: true, outreach: pending })]),
    )
    decideOutreach.mockResolvedValue({})
    render(<InterventionsPage />)
    expect(await screen.findByText('Waiting for approval')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Approve list' }))
    await waitFor(() => expect(decideOutreach).toHaveBeenCalledWith(7, 'approve'))
  })
})
