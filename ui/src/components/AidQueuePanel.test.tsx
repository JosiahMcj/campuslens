// @vitest-environment jsdom

// The Financial Aid review queue: the role gating (who edits, who only
// reads, who sees the queued line and the open button in the decision
// card) and the row editing (a status and a note saved through PATCH
// /api/aid-queue/{id} with the session's CSRF token, exactly as typed).
// The API is a mocked fetch; the components talk to it through the real
// ui/src/aid.ts client.

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AidQueueSummary, AidReviewRow } from '../aid'
import type { Decision, DispatchInfo } from '../api'
import { clearSession, setSession, type Role } from '../auth'
import { AidQueueNotice } from './AidQueueNotice'
import { AidQueuePanel } from './AidQueuePanel'
import { DecisionPanel } from './DecisionPanel'

const ROWS: AidReviewRow[] = [
  {
    id: 1,
    decision_id: 'D-spring-registration-1',
    dataset_id: 1,
    student_id: 'STU-0007',
    facts: {
      registration_status: 'not_registered',
      holds: [{ amount: 412.5, hold_date: '2026-10-02', responsible_office: 'Bursar' }],
      advising_appointment_status: 'completed',
      advising_last_appointment_date: '2026-09-01',
    },
    status: 'open',
    note: '',
    updated_by: null,
    updated_at: null,
    created_at: '2026-10-05T12:00:00+00:00',
  },
  {
    id: 2,
    decision_id: 'D-spring-registration-1',
    dataset_id: 1,
    student_id: 'STU-0120',
    facts: {
      registration_status: 'not_registered',
      holds: [{ amount: 95, hold_date: '2026-09-15', responsible_office: 'Bursar' }],
      advising_appointment_status: 'none',
      advising_last_appointment_date: null,
    },
    status: 'closed',
    note: 'Paid at the window.',
    updated_by: 'aid@example.edu',
    updated_at: '2026-10-05T13:00:00+00:00',
    created_at: '2026-10-05T12:00:00+00:00',
  },
]

interface Call {
  url: string
  method: string
  body: unknown
  csrf: string | null
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function stubApi(patchStatus = 200) {
  const rows = ROWS.map((row) => ({ ...row }))
  const calls: Call[] = []
  const stub = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    const headers = new Headers(init?.headers)
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : null
    calls.push({ url, method, body, csrf: headers.get('X-CSRF-Token') })
    if (url === '/api/aid-queue' && method === 'GET') {
      return jsonResponse({ dataset_id: 1, fictional: true, rows })
    }
    const match = /^\/api\/aid-queue\/(\d+)$/.exec(url)
    if (match !== null && method === 'PATCH') {
      if (patchStatus !== 200) {
        return jsonResponse({ detail: 'the note is 1,001 characters; the limit is 1,000' }, patchStatus)
      }
      const index = rows.findIndex((row) => row.id === Number(match[1]))
      rows[index] = {
        ...rows[index],
        ...(body as { status: AidReviewRow['status']; note: string }),
        updated_by: 'aid@example.edu',
        updated_at: '2026-10-05T14:00:00+00:00',
      }
      return jsonResponse({ row: rows[index], event_id: 9 })
    }
    return jsonResponse({ detail: `unhandled ${method} ${url}` }, 500)
  })
  vi.stubGlobal('fetch', stub)
  return { calls }
}

beforeEach(() => {
  setSession({
    user: {
      id: 5,
      email: 'aid@example.edu',
      role: 'aid',
      institution_id: 1,
      institution: { slug: 'bootstrap', name: 'Bootstrap Institution' },
    },
    csrfToken: 'csrf-token-1',
  })
})

afterEach(() => {
  cleanup()
  clearSession()
  vi.unstubAllGlobals()
})

describe('AidQueuePanel', () => {
  it('lists every row with its facts and no ranking or label', async () => {
    stubApi()
    render(<AidQueuePanel canEdit />)
    const first = await screen.findByRole('listitem', { name: 'Student STU-0007' })
    expect(within(first).getByText('$412.50, placed 2026-10-02, held by Bursar')).toBeTruthy()
    expect(within(first).getByText('not registered')).toBeTruthy()
    expect(within(first).getByText('completed, last appointment 2026-09-01')).toBeTruthy()
    const second = screen.getByRole('listitem', { name: 'Student STU-0120' })
    expect(within(second).getByText('none, no appointment on record')).toBeTruthy()
    expect(screen.getByText(/2 students, demonstration data\. Open 1, In review 0, Closed 1/)).toBeTruthy()
    const text = document.body.textContent ?? ''
    for (const word of ['eligible', 'ineligible', 'approve', 'deny', 'recommend', 'likely', 'score']) {
      expect(text.toLowerCase()).not.toContain(word)
    }
  })

  it('saves a status and a note exactly as typed, with the CSRF token', async () => {
    const { calls } = stubApi()
    render(<AidQueuePanel canEdit />)
    const row = await screen.findByRole('listitem', { name: 'Student STU-0007' })
    const save = within(row).getByRole('button', { name: 'Save' }) as HTMLButtonElement
    expect(save.disabled).toBe(true) // nothing changed yet
    fireEvent.change(within(row).getByLabelText('Status'), { target: { value: 'in_review' } })
    const note = '  Called the student.\nWaiting on paperwork.  '
    fireEvent.change(within(row).getByLabelText('Note'), { target: { value: note } })
    expect(save.disabled).toBe(false)
    fireEvent.click(save)
    await within(row).findByText('Saved')
    const patch = calls.find((call) => call.method === 'PATCH')
    expect(patch).toEqual({
      url: '/api/aid-queue/1',
      method: 'PATCH',
      body: { status: 'in_review', note },
      csrf: 'csrf-token-1',
    })
    expect(row.querySelector('.aid-status')?.textContent).toBe('In review')
    expect(within(row).getByText(/Last updated by aid@example\.edu/)).toBeTruthy()
    expect(save.disabled).toBe(true)
  })

  it('shows the API refusal on the row and keeps the typed note', async () => {
    stubApi(422)
    render(<AidQueuePanel canEdit />)
    const row = await screen.findByRole('listitem', { name: 'Student STU-0007' })
    fireEvent.change(within(row).getByLabelText('Note'), { target: { value: 'x' } })
    fireEvent.click(within(row).getByRole('button', { name: 'Save' }))
    const alert = await within(row).findByRole('alert')
    expect(alert.textContent).toContain('the limit is 1,000')
    expect((within(row).getByLabelText('Note') as HTMLTextAreaElement).value).toBe('x')
  })

  it('caps the note box at 1,000 characters', async () => {
    stubApi()
    render(<AidQueuePanel canEdit />)
    const row = await screen.findByRole('listitem', { name: 'Student STU-0007' })
    expect((within(row).getByLabelText('Note') as HTMLTextAreaElement).maxLength).toBe(1000)
    expect(within(row).getByText('0 of 1,000 characters')).toBeTruthy()
  })

  it('is read only without edit rights: no controls, the note as text', async () => {
    const { calls } = stubApi()
    render(<AidQueuePanel canEdit={false} />)
    const row = await screen.findByRole('listitem', { name: 'Student STU-0120' })
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()
    expect(within(row).getByText('Paid at the window.')).toBeTruthy()
    expect(screen.getByText(/This view is read only/)).toBeTruthy()
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('says how the queue starts when it is empty', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ dataset_id: 1, fictional: true, rows: [] })),
    )
    render(<AidQueuePanel canEdit />)
    await waitFor(() => expect(screen.getByText(/No students are queued yet/)).toBeTruthy())
  })
})

const QUEUED: AidQueueSummary = { supported: true, count: 18, created_at: '2026-10-05T12:00:00+00:00' }
const NOT_YET: AidQueueSummary = { supported: true, count: null, created_at: null }

function notice(
  summary: AidQueueSummary | undefined,
  options: { authorized?: boolean; canPrepare?: boolean; canOpen?: boolean } = {},
): string {
  return renderToStaticMarkup(
    <AidQueueNotice
      summary={summary}
      authorized={options.authorized ?? true}
      canPrepare={options.canPrepare ?? true}
      state={undefined}
      onPrepare={() => {}}
      onOpen={options.canOpen === false ? null : () => {}}
    />,
  )
}

describe('AidQueueNotice gating', () => {
  it('shows the queued line and the open button once the queue exists', () => {
    const html = notice(QUEUED)
    expect(html).toContain('Queued 18 students for Financial Aid review')
    expect(html).toContain('Open the review queue')
  })

  it('gives staff the line but no button, since staff cannot read the rows', () => {
    const html = notice(QUEUED, { canOpen: false })
    expect(html).toContain('Queued 18 students for Financial Aid review')
    expect(html).not.toContain('Open the review queue')
  })

  it('offers to prepare the queue only after sign-off and only to roles that may', () => {
    expect(notice(NOT_YET)).toContain('Prepare the Financial Aid review queue')
    expect(notice(NOT_YET, { authorized: false })).toBe('')
    expect(notice(NOT_YET, { canPrepare: false })).toBe('')
  })

  it('renders nothing for a decision that opens no queue', () => {
    expect(notice({ supported: false, count: null, created_at: null })).toBe('')
    expect(notice(undefined)).toBe('')
  })
})

describe('DecisionPanel with the queue block', () => {
  const decision: Decision = {
    id: 'D-spring-registration-1',
    title: 'Emergency-aid review',
    text: 'Authorize the review.',
    follow_up: { office: 'Financial Aid', description: 'Conduct the review.' },
    approved: true,
  }
  const info: DispatchInfo = {
    decision_id: decision.id,
    task_id: `TASK-${decision.id}`,
    office: 'Financial Aid',
    office_contact: null,
    approved: true,
    dispatch: null,
    aid_queue: QUEUED,
  }

  function panel(role: Role, canOpen: boolean): string {
    return renderToStaticMarkup(
      <DecisionPanel
        decisions={[decision]}
        events={[]}
        canApprove={role === 'executive'}
        role={role}
        userEmail="exec@example.edu"
        approving={false}
        approveError={null}
        approvedTasks={{}}
        dispatches={{ [decision.id]: { info, busy: null, error: null } }}
        onApprove={() => {}}
        onPrepareDispatch={() => {}}
        onSendDispatch={() => {}}
        onOpenEvidence={() => {}}
        onPrepareAidQueue={() => {}}
        onOpenAidQueue={canOpen ? () => {} : null}
      />,
    )
  }

  it('tells the executive how many students were queued, with a way in', () => {
    const html = panel('executive', true)
    expect(html).toContain('Queued 18 students for Financial Aid review')
    expect(html).toContain('Open the review queue')
  })

  it('stays hidden when the caller passes no queue handlers', () => {
    const html = renderToStaticMarkup(
      <DecisionPanel
        decisions={[decision]}
        events={[]}
        canApprove
        role="executive"
        userEmail="exec@example.edu"
        approving={false}
        approveError={null}
        approvedTasks={{}}
        dispatches={{ [decision.id]: { info, busy: null, error: null } }}
        onApprove={() => {}}
        onPrepareDispatch={() => {}}
        onSendDispatch={() => {}}
        onOpenEvidence={() => {}}
      />,
    )
    expect(html).not.toContain('Queued 18 students')
  })
})
