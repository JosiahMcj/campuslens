// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Decision, DispatchInfo, Finding, Findings } from '../api'
import { FirstResult } from './FirstResult'

function finding(id: string, display: string, extra: Partial<Finding> = {}): Finding {
  return {
    id,
    title: id,
    value: 1,
    display,
    reason: null,
    comparison: null,
    source_fields: [],
    row_ids: [],
    definition: '',
    ...extra,
  }
}

const FINDINGS: Findings = {
  meta: {
    as_of: '2026-11-20',
    fixture: 'fixture.json',
    terms: { prior_year_equivalent_date: '2025-11-20', registration_close_date: '2026-12-01' },
    fictional: true,
  },
  M1: finding('M1', '-12.0%', {
    comparison: { prior_year_registered_continuing: 125, prior_year_equivalent_date: '2025-11-20' },
  }),
  M2: finding('M2', '41'),
  M3: finding('M3', '18'),
  M4: finding('M4', '23'),
  M8: finding('M8', '52'),
}

const DECISION: Decision = {
  id: 'D-spring-registration-1',
  title: 'Authorize the eligibility review',
  text: 'Authorize a focused review below the threshold.',
  follow_up: { office: 'Financial Aid', description: 'Report back in two weeks.' },
  approved: false,
}

const INFO: DispatchInfo = {
  decision_id: DECISION.id,
  task_id: `TASK-${DECISION.id}`,
  office: 'Financial Aid',
  office_contact: null,
  approved: false,
  dispatch: null,
  delivery: 'outbox',
  proposed_due: '2026-11-27',
}

afterEach(cleanup)

function show(overrides: Partial<Parameters<typeof FirstResult>[0]> = {}) {
  const onOpenEvidence = vi.fn()
  const onReviewNextSteps = vi.fn()
  render(
    <FirstResult
      findings={FINDINGS}
      fictional
      role="executive"
      decision={DECISION}
      dispatch={INFO}
      onOpenEvidence={onOpenEvidence}
      onReviewNextSteps={onReviewNextSteps}
      {...overrides}
    />,
  )
  return { onOpenEvidence, onReviewNextSteps }
}

describe('FirstResult', () => {
  it('shows the finding, its comparison period, the counts and the fictional label', () => {
    show()
    const section = screen.getByRole('region', { name: 'Main findings' })
    expect(section.textContent).toContain('Spring registration is -12.0%')
    expect(section.textContent).toContain(
      'Comparison period: registrations as of Nov 20, 2026, against Nov 20, 2025 last year.',
    )
    expect(section.textContent).toContain('Fictional data')
    // The four student-support counts, each a button that opens its evidence.
    for (const display of ['41', '18', '23', '52']) {
      expect(screen.getByText(display).closest('button')).not.toBeNull()
    }
  })

  it('shows the holds findings and scope for the unresolved-holds question', () => {
    const findings: Findings = {
      ...FINDINGS,
      M5: {
        ...finding('M5', '28 unresolved holds'),
        value: [
          { office: 'Bursar', count: 20, hold_row_ids: [] },
          { office: 'Registrar', count: 8, hold_row_ids: [] },
        ],
      },
      M6: finding('M6', '28 days'),
    }
    show({ findings, questionId: 'unresolved-holds' })
    const text = screen.getByRole('region', { name: 'Main findings' }).textContent
    expect(text).toContain('Unresolved holds affecting continued enrollment')
    expect(text).toContain('28 unresolved holds')
    expect(text).toContain('Bursar 20, Registrar 8')
    expect(text).toContain('28 days')
    expect(text).not.toContain('Comparison period')
  })

  it('scopes the registration answer and labels the snapshot as fictional', () => {
    show()
    const text = screen.getByRole('region', { name: 'Main findings' }).textContent
    expect(text).toContain('Spring registration window · continuing students not yet registered')
    expect(text).toContain('Fictional snapshot dated Nov 20, 2026.')
  })

  it('names the human-owned next step, its office and the proposed deadline', () => {
    show()
    const section = screen.getByRole('region', { name: 'Main findings' })
    expect(section.textContent).toContain('Next step · you decide')
    expect(section.textContent).toContain('Authorize the eligibility review')
    expect(section.textContent).toContain('Waiting for your approval')
    expect(section.textContent).toContain('Responsible officeFinancial Aid')
    expect(section.textContent).toContain('Proposed deadlineNov 27, 2026 (demo)')
  })

  it('says leadership decides for a role that may not approve, and drops the label on real data', () => {
    show({ role: 'staff', fictional: false, dispatch: { ...INFO, proposed_due: null } })
    const section = screen.getByRole('region', { name: 'Main findings' })
    expect(section.textContent).toContain('Next step · leadership decides')
    expect(section.textContent).toContain('Waiting for leadership approval')
    expect(section.textContent).not.toContain('Fictional data')
    expect(section.textContent).not.toContain('deadline')
  })

  it('opens the evidence and the next steps from its two actions', () => {
    const { onOpenEvidence, onReviewNextSteps } = show()
    fireEvent.click(screen.getByRole('button', { name: 'View evidence' }))
    expect(onOpenEvidence).toHaveBeenCalledWith('M1')
    fireEvent.click(screen.getByRole('button', { name: 'Review next steps' }))
    expect(onReviewNextSteps).toHaveBeenCalledTimes(1)
  })
})
