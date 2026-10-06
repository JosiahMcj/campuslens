// @vitest-environment jsdom

import { cleanup, fireEvent, render as mount, screen } from '@testing-library/react'
import { useState } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AuditEvent, Decision, DispatchInfo } from '../api'
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
    body: 'To the Financial Aid office,\n\n18 continuing students have an unresolved financial hold below $1,000.',
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
    events?: AuditEvent[]
    approvedTasks?: Parameters<typeof DecisionPanel>[0]['approvedTasks']
    approveError?: string | null
  } = {},
): string {
  return renderToStaticMarkup(
    <DecisionPanel
      decisions={[options.decision ?? DECISION]}
      events={options.events ?? []}
      canApprove={canApprove}
      role={options.role ?? 'executive'}
      userEmail="exec@example.edu"
      approving={false}
      approveError={options.approveError ?? null}
      approvedTasks={options.approvedTasks ?? {}}
      dispatches={options.dispatches ?? {}}
      onApprove={() => {}}
      onPrepareDispatch={() => {}}
      onSendDispatch={() => {}}
    />,
  )
}

afterEach(() => cleanup())

describe('DecisionPanel — the decision and Approve', () => {
  it('says who decides in one line, shows the decision once, and offers Approve', () => {
    const html = render(true)

    expect(html).toContain('The Cabinet advises. You decide. Nothing is sent on its own.')
    expect(html).toContain('btn-approve')
    expect(html).toContain('>Approve</button>')
    expect(html.split('Authorize a focused review below the threshold.')).toHaveLength(2)
    // No event codes, no task ids, no "simulated".
    expect(html).not.toContain('decision.approved')
    expect(html).not.toContain('TASK-')
    expect(html).not.toContain('<code>')
    expect(html).not.toContain('simulated')
  })

  it('tells staff and reviewers leadership approves, with no "only an executive" hint', () => {
    const html = render(false)

    expect(html).toContain('Waiting for leadership approval.')
    expect(html).not.toContain('btn-approve')
    expect(html).not.toContain('Only an executive')
    expect(html).toContain('Authorize the eligibility review')
  })

  it('replaces Approve with a gold "Approved by" line, never "Approve again"', () => {
    const approvedEvent: AuditEvent = {
      id: 7,
      ts: '2026-09-26T11:00:00+00:00',
      type: 'decision.approved',
      actor: 'executive',
      payload: { decision_id: DECISION.id },
    }
    const html = render(true, { decision: APPROVED, events: [approvedEvent] })

    expect(html).toContain('approved-line')
    expect(html).toContain('Approved by leadership at 2026-09-26')
    expect(html).not.toContain('>Approve</button>')
    expect(html).not.toContain('Approve again')
  })

  it('names the approver as "you" right after this person approved', () => {
    const html = render(true, {
      decision: APPROVED,
      approvedTasks: {
        [DECISION.id]: {
          task: {
            id: 'TASK-1',
            decision_id: DECISION.id,
            office: 'Financial Aid',
            description: 'x',
            status: 'open',
          },
          created: true,
        },
      },
    })
    expect(html).toContain('Approved by you')
  })

  it('uses the approver and time from the API when it sends them', () => {
    const html = render(true, {
      decision: {
        ...APPROVED,
        approved_by: 'president@example.edu',
        approved_at: '2026-09-26T11:00:00+00:00',
      },
    })
    expect(html).toContain('Approved by president@example.edu at 2026-09-26')
  })

  it('names the approver to staff too, from the decision or the dispatch state', () => {
    const fromDecision = render(false, {
      decision: { ...APPROVED, approved_by: 'president@example.edu' },
      role: 'staff',
    })
    expect(fromDecision).toContain('Approved by president@example.edu')
    const fromDispatch = render(false, {
      decision: APPROVED,
      role: 'staff',
      dispatches: ready({
        ...DRAFT,
        approved_by: 'president@example.edu',
        approved_at: '2026-09-26T11:00:00+00:00',
      }),
    })
    expect(fromDispatch).toContain('Approved by president@example.edu at 2026-09-26')
  })

  it('says "Approved by leadership" when the approver is not known yet', () => {
    const html = render(false, { decision: APPROVED, role: 'staff' })
    expect(html).toContain('Approved by leadership')
    expect(html).not.toContain('Only an executive')
    expect(html).not.toContain('Waiting for leadership approval')
  })

  it('turns a technical approval error into a plain sentence', () => {
    const html = render(true, { approveError: 'HTTP 500: Internal Server Error' })
    expect(html).toContain("That didn&#x27;t work. The approval was not saved.")
    expect(html).not.toContain('HTTP')
  })
})

describe('DecisionPanel — next steps', () => {
  it('offers Prepare once the decision is approved, and not before', () => {
    const html = render(true, { decision: APPROVED })
    expect(html).toContain('Next steps')
    expect(html).toContain('Message to Financial Aid:')
    expect(html).toContain('Not prepared yet')
    expect(html).toContain('Prepare the message')
    expect(html).not.toContain('dispatch-draft')

    expect(render(true)).not.toContain('Prepare the message')
  })

  it('offers no Prepare to a reviewer', () => {
    const html = render(false, { decision: APPROVED, role: 'reviewer' })
    expect(html).not.toContain('Prepare the message')
  })

  it('shows To and Subject, with the message itself folded, in plain words', () => {
    const html = render(false, { decision: APPROVED, role: 'staff', dispatches: ready(DRAFT) })

    expect(html).toContain('Prepared, not sent')
    expect(html).toContain('financial-aid@example.edu')
    expect(html).toContain('Approved follow-up for Financial Aid')
    expect(html).toContain('Show message')
    expect(html).toMatch(/<details[^>]*>(?:(?!<\/details>).)*18 continuing students/s)
    expect(html).not.toContain('finding-link')
    expect(html.replace(/<[^>]*>/g, ' ')).not.toMatch(/\bM\d\b|finding M|D-spring/)
  })

  it('offers Send to staff (behind a confirmation), and not to the executive', () => {
    const staff = render(false, { decision: APPROVED, role: 'staff', dispatches: ready(DRAFT) })
    expect(staff).toContain('dispatch-send')
    expect(staff).toContain('Send…')

    const executive = render(true, { decision: APPROVED, dispatches: ready(DRAFT) })
    expect(executive).toContain('dispatch-draft')
    expect(executive).not.toContain('dispatch-send')
    expect(executive).toContain('A staff member sends this message.')
  })

  it('says the office has no mailbox yet, and offers no Send', () => {
    const html = render(false, {
      decision: APPROVED,
      role: 'staff',
      dispatches: ready({ ...DRAFT, office_contact: null }),
    })
    expect(html).toContain('(no mailbox set up yet)')
    expect(html).toContain(
      'Financial Aid has no mailbox yet. An administrator adds one in Institution settings before it can be sent.',
    )
    expect(html).not.toContain('href="/institution#inst-offices"')
    expect(html).not.toContain('dispatch-send')
  })

  it('gives an admin a link to Institution settings, Offices', () => {
    const html = render(false, {
      decision: APPROVED,
      role: 'admin',
      dispatches: ready({ ...DRAFT, office_contact: null }),
    })
    expect(html).toContain('href="/institution#inst-offices"')
    expect(html).toContain('>Institution settings, Offices</a>')
    expect(html).not.toContain('An administrator adds one')
  })

  it('shows who sent it and when, with no provider name and no Send', () => {
    const html = render(false, { decision: APPROVED, role: 'staff', dispatches: ready(SENT) })

    expect(html).toContain('>Sent</span>')
    expect(html).toContain('Sent by staff@example.edu at 2026-09-26')
    expect(html).not.toContain('outbox')
    expect(html).not.toContain('dispatch-send')
    expect(html).not.toContain('Prepare the message')
    expect(html).not.toContain('nothing sent')
  })

  it('keeps a failed send plain, with the provider text folded', () => {
    const failed: DispatchInfo = {
      ...DRAFT,
      dispatch: { ...DRAFT.dispatch!, status: 'failed', error: 'SMTP 550 mailbox unavailable' },
    }
    const html = render(false, { decision: APPROVED, role: 'staff', dispatches: ready(failed) })
    expect(html).toContain('Not sent yet')
    expect(html).toContain('The last send did not go through. Nothing was delivered.')
    expect(html).toMatch(/Technical detail(?:(?!<\/details>).)*SMTP 550/s)
  })

  it('shows the reviewer the draft, never a Send button', () => {
    const reviewer = render(false, { decision: APPROVED, role: 'reviewer', dispatches: ready(DRAFT) })
    expect(reviewer).toContain('dispatch-draft')
    expect(reviewer).not.toContain('dispatch-send')
  })

  it('shows a failed decision load with Retry instead of Loading forever', () => {
    const html = renderToStaticMarkup(
      <DecisionPanel
        decisions={null}
        events={[]}
        canApprove
        role="executive"
        userEmail="exec@example.edu"
        approving={false}
        approveError={null}
        approvedTasks={{}}
        dispatches={{}}
        onApprove={() => {}}
        onPrepareDispatch={() => {}}
        onSendDispatch={() => {}}
          loadError="Check your connection and try again."
        onRetry={() => {}}
      />,
    )
    expect(html).toContain("Couldn&#x27;t load the decision.")
    expect(html).toContain('>Retry</button>')
    expect(html).not.toContain('Loading the decision')
  })
})

/** The panel with live state, as the app drives it: Send resolves to "sent". */
function LivePanel({ onSend, onApprove }: { onSend: () => void; onApprove?: () => void }) {
  const [dispatches, setDispatches] = useState(ready(DRAFT))
  const [decision, setDecision] = useState<Decision>(onApprove ? DECISION : APPROVED)
  const [approving, setApproving] = useState(false)
  return (
    <DecisionPanel
      decisions={[decision]}
      events={[]}
      canApprove
      role="admin"
      userEmail="admin@example.edu"
      approving={approving}
      approveError={null}
      approvedTasks={{}}
      dispatches={dispatches}
      onApprove={() => {
        onApprove?.()
        setApproving(true)
        setTimeout(() => {
          setApproving(false)
          setDecision(APPROVED)
        }, 0)
      }}
      onPrepareDispatch={() => {}}
      onSendDispatch={() => {
        onSend()
        setDispatches({ [DECISION.id]: { info: DRAFT, busy: 'send', error: null } })
        setTimeout(() => setDispatches(ready(SENT)), 0)
      }}
    />
  )
}

describe('DecisionPanel — Send confirmation and focus', () => {
  it('asks "Send to <office> at <address>?" before sending, and Cancel sends nothing', () => {
    const onSend = vi.fn()
    mount(<LivePanel onSend={onSend} />)

    fireEvent.click(screen.getByRole('button', { name: 'Send…' }))
    const confirm = screen.getByRole('alertdialog')
    expect(confirm.textContent).toContain('Send to Financial Aid at financial-aid@example.edu?')
    expect(document.activeElement).toBe(confirm)
    expect(onSend).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(onSend).not.toHaveBeenCalled()
  })

  it('sends once on Send, keeps the busy button focusable, then focuses the Sent line', async () => {
    const onSend = vi.fn()
    mount(<LivePanel onSend={onSend} />)
    fireEvent.click(screen.getByRole('button', { name: 'Send…' }))
    const send = screen.getByRole('button', { name: 'Send' })
    fireEvent.click(send)
    expect(onSend).toHaveBeenCalledTimes(1)
    const busy = screen.getByRole('button', { name: 'Sending…' }) as HTMLButtonElement
    expect(busy.disabled).toBe(false)
    expect(busy.getAttribute('aria-busy')).toBe('true')
    fireEvent.click(busy) // a second click while busy does nothing
    expect(onSend).toHaveBeenCalledTimes(1)

    const sentLine = await screen.findByText(/Sent by staff@example\.edu/)
    expect(document.activeElement).toBe(sentLine)
  })

  it('moves focus to the Approved line once the approval lands', async () => {
    const onApprove = vi.fn()
    mount(<LivePanel onSend={() => {}} onApprove={onApprove} />)
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    expect(onApprove).toHaveBeenCalledTimes(1)
    const line = await screen.findByText(/Approved by leadership/)
    expect(document.activeElement).toBe(line)
  })
})
