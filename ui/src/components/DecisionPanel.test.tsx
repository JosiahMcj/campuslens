import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { Decision, DispatchInfo } from '../api'
import type { Role } from '../auth'
import { DecisionPanel, type DispatchUiState } from './DecisionPanel'

const DECISION: Decision = {
  id: 'D-spring-registration-1',
  title: 'Authorize the eligibility review',
  text: 'Authorize a focused review below the threshold.',
  follow_up: { office: 'Financial Aid', description: 'Report back in two weeks.' },
  approved: false,
}

const APPROVED: Decision = { ...DECISION, approved: true }

const DRAFT: DispatchInfo = {
  decision_id: DECISION.id,
  task_id: `TASK-${DECISION.id}`,
  office: 'Financial Aid',
  office_contact: 'financial-aid@example.edu',
  approved: true,
  dispatch: {
    id: 1,
    task_id: `TASK-${DECISION.id}`,
    to_office: 'Financial Aid',
    channel: 'email',
    subject: 'Approved follow-up for Financial Aid: the eligibility review',
    body: 'To the Financial Aid office,\n\n18 continuing students (finding M3).',
    status: 'draft',
    created_by: 'executive@example.edu',
    created_at: '2026-09-26T12:00:00+00:00',
    sent_by: null,
    sent_at: null,
    provider: null,
    provider_ref: null,
    error: null,
  },
}

const SENT: DispatchInfo = {
  ...DRAFT,
  dispatch: {
    ...DRAFT.dispatch!,
    status: 'sent',
    sent_by: 'staff@example.edu',
    sent_at: '2026-09-26T12:30:00+00:00',
    provider: 'outbox',
    provider_ref: 'bootstrap/1.eml',
  },
}

function ready(info: DispatchInfo): Record<string, DispatchUiState> {
  return { [DECISION.id]: { info, busy: null, error: null } }
}

function render(
  canApprove: boolean,
  options: {
    role?: Role
    decision?: Decision
    dispatches?: Record<string, DispatchUiState>
  } = {},
): string {
  return renderToStaticMarkup(
    <DecisionPanel
      decisions={[options.decision ?? DECISION]}
      events={[]}
      canApprove={canApprove}
      role={options.role ?? 'executive'}
      userEmail="exec@example.edu"
      approving={false}
      approveError={null}
      approvedTasks={{}}
      dispatches={options.dispatches ?? {}}
      onApprove={() => {}}
      onPrepareDispatch={() => {}}
      onSendDispatch={() => {}}
      onOpenEvidence={() => {}}
    />,
  )
}

describe('DecisionPanel — role gating of Approve', () => {
  it('offers the Approve button to a role that may approve', () => {
    const html = render(true)

    expect(html).toContain('approve-button')
    expect(html).toContain('Approve the review')
    expect(html).not.toContain('Only an executive can approve this')
  })

  it('shows "Only an executive can approve this" instead of the button for staff and reviewer', () => {
    const html = render(false)

    expect(html).toContain('Only an executive can approve this')
    expect(html).not.toContain('approve-button')
    // The decision itself is never hidden.
    expect(html).toContain('Authorize the eligibility review')
  })
})

describe('DecisionPanel — the governed execution step', () => {
  it('offers Prepare once the decision is approved', () => {
    const html = render(true, { decision: APPROVED })

    expect(html).toContain('dispatch-prepare')
    expect(html).toContain('Prepare the message to Financial Aid')
    // No draft exists yet, so no message and no Send.
    expect(html).not.toContain('dispatch-draft')
    expect(html).not.toContain('dispatch-send')
  })

  it('offers no Prepare before approval', () => {
    const html = render(true)

    expect(html).not.toContain('dispatch-prepare')
  })

  it('offers no Prepare to a reviewer', () => {
    const html = render(false, { decision: APPROVED, role: 'reviewer' })

    expect(html).not.toContain('dispatch-prepare')
  })

  it('shows the composed message read-only, with the finding link kept', () => {
    const html = render(false, {
      decision: APPROVED,
      role: 'staff',
      dispatches: ready(DRAFT),
    })

    expect(html).toContain('dispatch-draft')
    expect(html).toContain('Message to the office, not yet sent')
    expect(html).toContain('financial-aid@example.edu')
    expect(html).toContain('Approved follow-up for Financial Aid')
    expect(html).toContain('18 continuing students')
    // The M3 token renders as an evidence link, like numbers in the briefing.
    expect(html).toContain('finding-link')
    expect(html).toContain('Open the evidence for finding M3')
  })

  it('shows Send as the signed-in staff member, and not to the executive', () => {
    const staff = render(false, {
      decision: APPROVED,
      role: 'staff',
      dispatches: ready(DRAFT),
    })
    expect(staff).toContain('dispatch-send')
    expect(staff).toContain('Send as exec@example.edu')

    const executive = render(true, {
      decision: APPROVED,
      role: 'executive',
      dispatches: ready(DRAFT),
    })
    // The executive sees the draft and the note, never the Send button.
    expect(executive).toContain('dispatch-draft')
    expect(executive).not.toContain('dispatch-send')
    expect(executive).toContain('A staff member sends this message')
  })

  it('warns when the office has no mailbox configured', () => {
    const html = render(false, {
      decision: APPROVED,
      role: 'staff',
      dispatches: ready({ ...DRAFT, office_contact: null }),
    })

    expect(html).toContain('no mailbox configured')
    expect(html).toContain('No mailbox is configured for Financial Aid')
  })

  it('shows who sent it, when, and through which provider — and no Send', () => {
    const html = render(false, {
      decision: APPROVED,
      role: 'staff',
      dispatches: ready(SENT),
    })

    expect(html).toContain('Message sent')
    expect(html).toContain('Sent by staff@example.edu')
    expect(html).toContain('through outbox')
    expect(html).not.toContain('dispatch-send')
    expect(html).not.toContain('dispatch-prepare')
  })
})

describe('DecisionPanel — the reviewer and a draft', () => {
  it('shows the reviewer the draft and the note, never a Send button', () => {
    const reviewer = render(false, {
      decision: APPROVED,
      role: 'reviewer',
      dispatches: ready(DRAFT),
    })
    expect(reviewer).toContain('dispatch-draft')
    expect(reviewer).not.toContain('dispatch-send')
    expect(reviewer).toContain('Only staff and administrators can send this message')
  })
})
