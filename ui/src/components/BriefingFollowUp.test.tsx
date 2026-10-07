// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Decision } from '../api'
import { followUpFrom, type FollowUpAnswer, type FollowUpDenied } from '../followup'
import { BriefingFollowUp, FollowUpDeniedCard } from './BriefingFollowUp'

afterEach(cleanup)

const plan = followUpFrom({
  matched: true,
  kind: 'answer',
  intent: 'approval',
  title: 'Follow-up for leadership approval',
  finding_ids: ['M3'],
  suggestions: ['Show me the evidence and audit trail behind this recommendation.'],
  source: 'Source: Demonstration (fictional).',
  event_ids: [7, 8],
  blocks: [
    {
      type: 'table',
      label: 'recommendation',
      title: 'Seven-day action plan (proposed)',
      columns: ['Office', 'Proposed action', 'Deadline', 'Success measure'],
      rows: [['Enrollment', 'Review the 42 cases', 'Nov 22 (2 days)', 'All 42 cases assigned']],
      row_findings: [['M2']],
    },
    { type: 'text', label: 'interpretation', text: 'A reading.', author: 'Enrollment Analyst (AI)' },
    { type: 'text', label: 'fact', text: 'A computed fact.' },
    {
      type: 'approval',
      label: 'recommendation',
      decision_id: 'D-spring-registration-1',
      title: 'Emergency-aid eligibility review',
      text: 'Decide whether to authorize the review.',
      office: 'Financial Aid',
      follow_up: 'Conduct the review.',
      approved: false,
      approved_by: null,
      approved_at: null,
    },
    { type: 'mystery', label: 'fact' },
  ],
}) as FollowUpAnswer

function show(overrides: Partial<Parameters<typeof BriefingFollowUp>[0]> = {}) {
  const props = {
    response: plan,
    onOpenEvidence: vi.fn(),
    onAsk: vi.fn(),
    busy: false,
    decisions: null,
    canApprove: true,
    approving: false,
    approveError: null,
    onApprove: vi.fn(),
    onSeeAuditLog: vi.fn(),
    ...overrides,
  }
  render(<BriefingFollowUp {...props} />)
  return props
}

describe('followUpFrom', () => {
  it('treats anything unexpected as not a follow-up', () => {
    expect(followUpFrom(null)).toEqual({ matched: false })
    expect(followUpFrom({ matched: true, kind: 'weird' })).toEqual({ matched: false })
    expect(followUpFrom({ matched: true, kind: 'approved', question: 'Q?' })).toEqual({
      matched: true,
      kind: 'approved',
      question: 'Q?',
    })
  })

  it('drops unknown blocks', () => {
    expect(plan.blocks.map((b) => b.type)).toEqual(['table', 'text', 'text', 'approval'])
  })
})

describe('BriefingFollowUp', () => {
  it('renders the plan as a real table with its labels', () => {
    show()
    const table = screen.getByRole('table')
    expect(table.querySelectorAll('thead th')).toHaveLength(5) // + Evidence
    expect(screen.getByText('Nov 22 (2 days)')).toBeTruthy()
    expect(screen.getAllByText('Proposed · not executed').length).toBeGreaterThan(0)
    expect(screen.getByText('Interpretation · Enrollment Analyst (AI)')).toBeTruthy()
    expect(screen.getByText('Fact · computed from the records')).toBeTruthy()
  })

  it('opens the evidence for a row', () => {
    const props = show()
    fireEvent.click(screen.getByRole('button', { name: /See the evidence/ }))
    expect(props.onOpenEvidence).toHaveBeenCalledWith('M2')
  })

  it('approves only on the click, through the shared approval', () => {
    const props = show()
    expect(props.onApprove).not.toHaveBeenCalled()
    expect(screen.getByText('Awaiting your approval')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    expect(props.onApprove).toHaveBeenCalledWith('D-spring-registration-1')
  })

  it('shows the live approval state from the decisions', () => {
    const decision: Decision = {
      id: 'D-spring-registration-1',
      title: 't',
      text: 't',
      follow_up: { office: 'Financial Aid', description: 'd' },
      approved: true,
      approved_by: 'president@demo.test',
      approved_at: '2026-10-07T20:00:00+00:00',
    }
    show({ decisions: [decision] })
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull()
    expect(screen.getByRole('status').textContent).toContain('Approved by president@demo.test')
    expect(screen.getByRole('status').textContent).toContain('Nothing was sent')
  })

  it('asks the suggested next question', () => {
    const props = show()
    fireEvent.click(screen.getByText('Show me the evidence and audit trail behind this recommendation.'))
    expect(props.onAsk).toHaveBeenCalledWith(
      'Show me the evidence and audit trail behind this recommendation.',
    )
  })

  it('tells a role that cannot approve who can', () => {
    show({ canApprove: false })
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull()
    expect(screen.getByText(/Only the executive or an admin/)).toBeTruthy()
  })
})

describe('FollowUpDeniedCard', () => {
  it('names the AI employee and links the audit log', () => {
    const denied = followUpFrom({
      matched: true,
      kind: 'denied',
      employee: 'enrollment_analyst',
      employee_title: 'Enrollment Analyst',
      message: 'Access denied. Counseling and chaplain notes are outside the Enrollment Analyst’s authorized scope.',
      event_ids: [3, 4],
    }) as FollowUpDenied
    const onSee = vi.fn()
    render(<FollowUpDeniedCard response={denied} onSeeAuditLog={onSee} />)
    expect(screen.getByRole('alert').textContent).toContain('Access denied · Enrollment Analyst')
    fireEvent.click(screen.getByText('See the refusal in the audit log'))
    expect(onSee).toHaveBeenCalled()
  })
})
