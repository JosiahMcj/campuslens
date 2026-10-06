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
        return jsonResponse({ detail: 'note_length 1001 > limit 1000 (HTTP 422)' }, patchStatus)
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
      institution: { slug: 'bootstrap', name: 'Demonstration University' },
    },
    csrfToken: 'csrf-token-1',
  })
})

/** Find a student's row and open it (rows start folded to one line). */
async function openRow(name: string): Promise<HTMLElement> {
  const row = await screen.findByRole('listitem', { name })
  fireEvent.click(within(row).getByRole('button', { expanded: false }))
  return row
}

afterEach(() => {
  cleanup()
  clearSession()
  vi.unstubAllGlobals()
})

describe('AidQueuePanel', () => {
  it('lists every row with its facts and no ranking or label', async () => {
    stubApi()
    render(<AidQueuePanel canEdit />)
    const first = await openRow('Student STU-0007')
    expect(within(first).getByText('$412.50, placed 2026-10-02, held by Bursar')).toBeTruthy()
    expect(within(first).getByText('not registered')).toBeTruthy()
    expect(within(first).getByText('completed, last appointment 2026-09-01')).toBeTruthy()
    // One row open at a time: opening the second folds the first.
    const second = await openRow('Student STU-0120')
    expect(within(second).getByText('none, no appointment on record')).toBeTruthy()
    expect(within(first).queryByText('not registered')).toBeNull()
    expect(screen.getByText(/2 students, demonstration data: 1 open, 0 in review, 1 closed/)).toBeTruthy()
    const text = document.body.textContent ?? ''
    for (const word of ['eligible', 'ineligible', 'approve', 'deny', 'recommend', 'likely', 'score']) {
      expect(text.toLowerCase()).not.toContain(word)
    }
  })

  it('saves a status and a note exactly as typed, with the CSRF token', async () => {
    const { calls } = stubApi()
    render(<AidQueuePanel canEdit />)
    const row = await openRow('Student STU-0007')
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
      body: { status: 'in_review', note, expected_updated_at: null },
      csrf: 'csrf-token-1',
    })
    expect(row.querySelector('.aid-status')?.textContent).toBe('In review')
    expect(within(row).getByText(/Last updated by aid@example\.edu/)).toBeTruthy()
    expect(save.disabled).toBe(true)
  })

  it('shows the API refusal on the row and keeps the typed note', async () => {
    stubApi(422)
    render(<AidQueuePanel canEdit />)
    const row = await openRow('Student STU-0007')
    fireEvent.change(within(row).getByLabelText('Note'), { target: { value: 'x' } })
    fireEvent.click(within(row).getByRole('button', { name: 'Save' }))
    const alert = await within(row).findByRole('alert')
    // A technical server sentence never reaches the screen as is.
    expect(alert.textContent).toBe("That didn't work. The review was not saved.")
    expect((within(row).getByLabelText('Note') as HTMLTextAreaElement).value).toBe('x')
    // Editing again clears the error.
    fireEvent.change(within(row).getByLabelText('Note'), { target: { value: 'xy' } })
    expect(within(row).queryByRole('alert')).toBeNull()
  })

  it('says a network failure plainly, never "Failed to fetch"', async () => {
    stubApi()
    render(<AidQueuePanel canEdit />)
    const row = await openRow('Student STU-0007')
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )
    fireEvent.change(within(row).getByLabelText('Note'), { target: { value: 'x' } })
    fireEvent.click(within(row).getByRole('button', { name: 'Save' }))
    const alert = await within(row).findByRole('alert')
    expect(alert.textContent).toContain("We couldn't reach the Cabinet")
    expect(alert.textContent).not.toContain('Failed to fetch')
  })

  it('moves focus to the Saved line after a save, never to the page', async () => {
    stubApi()
    render(<AidQueuePanel canEdit />)
    const row = await openRow('Student STU-0007')
    fireEvent.change(within(row).getByLabelText('Note'), { target: { value: 'Called.' } })
    fireEvent.click(within(row).getByRole('button', { name: 'Save' }))
    const saved = await within(row).findByText('Saved')
    await waitFor(() => expect(document.activeElement).toBe(saved))
  })

  it('filters by status, with the count in each option', async () => {
    stubApi()
    render(<AidQueuePanel canEdit={false} />)
    await screen.findByRole('listitem', { name: 'Student STU-0007' })
    const show = screen.getByLabelText('Show') as HTMLSelectElement
    expect([...show.options].map((option) => option.textContent)).toEqual([
      'All (2)',
      'Open (1)',
      'In review (0)',
      'Closed (1)',
    ])
    fireEvent.change(show, { target: { value: 'closed' } })
    expect(screen.queryByRole('listitem', { name: 'Student STU-0007' })).toBeNull()
    expect(screen.getByRole('listitem', { name: 'Student STU-0120' })).toBeTruthy()
    fireEvent.change(show, { target: { value: 'in_review' } })
    expect(screen.getByText(/No students with this status/)).toBeTruthy()
  })

  it('folds each student to one line: id, hold, status', async () => {
    stubApi()
    render(<AidQueuePanel canEdit />)
    const row = await screen.findByRole('listitem', { name: 'Student STU-0007' })
    const head = within(row).getByRole('button', { expanded: false })
    expect(head.textContent).toContain('STU-0007')
    expect(head.textContent).toContain('$412.50, Bursar')
    expect(head.textContent).toContain('Open')
    expect(within(row).queryByLabelText('Note')).toBeNull()
  })

  it('shows a failed load with Retry, never the empty text', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: 'boom' }, 500)),
    )
    render(<AidQueuePanel canEdit />)
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain("Couldn't load the review queue")
    expect(alert.textContent).toContain('Something went wrong on our side')
    expect(within(alert).getByRole('button', { name: 'Retry' })).toBeTruthy()
    expect(screen.queryByText(/No students are queued yet/)).toBeNull()
  })

  it('sends only the fields that changed, with the updated_at it opened', async () => {
    const { calls } = stubApi()
    render(<AidQueuePanel canEdit />)
    const row = await openRow('Student STU-0120')
    fireEvent.change(within(row).getByLabelText('Status'), { target: { value: 'in_review' } })
    fireEvent.click(within(row).getByRole('button', { name: 'Save' }))
    await within(row).findByText('Saved')
    const patch = calls.find((call) => call.method === 'PATCH')
    expect(patch?.body).toEqual({
      status: 'in_review',
      expected_updated_at: '2026-10-05T13:00:00+00:00',
    })
  })

  it('counts characters as the server does, so an emoji counts once', async () => {
    const { calls } = stubApi()
    render(<AidQueuePanel canEdit />)
    const row = await openRow('Student STU-0007')
    const box = within(row).getByLabelText('Note') as HTMLTextAreaElement
    expect(within(row).getByText('0 of 1,000 characters')).toBeTruthy()
    // No maxLength: the browser counts UTF-16 units and would cut an emoji.
    expect(box.hasAttribute('maxlength')).toBe(false)
    const atCap = `${'a'.repeat(998)}\u{1F600}\u{1F600}`
    fireEvent.change(box, { target: { value: atCap } })
    expect(within(row).getByText('1,000 of 1,000 characters')).toBeTruthy()
    const save = within(row).getByRole('button', { name: 'Save' }) as HTMLButtonElement
    expect(save.disabled).toBe(false)
    fireEvent.click(save)
    await within(row).findByText('Saved')
    expect(calls.find((call) => call.method === 'PATCH')?.body).toMatchObject({ note: atCap })
  })

  it('refuses a note over 1,000 characters before sending it', async () => {
    const { calls } = stubApi()
    render(<AidQueuePanel canEdit />)
    const row = await openRow('Student STU-0007')
    fireEvent.change(within(row).getByLabelText('Note'), {
      target: { value: `${'a'.repeat(1000)}\u{1F600}` },
    })
    expect(within(row).getByText('1,001 of 1,000 characters')).toBeTruthy()
    expect(within(row).getByText(/1 character over the limit/)).toBeTruthy()
    const save = within(row).getByRole('button', { name: 'Save' }) as HTMLButtonElement
    expect(save.disabled).toBe(true)
    fireEvent.submit(save.closest('form')!)
    expect(calls.some((call) => call.method === 'PATCH')).toBe(false)
  })

  it('keeps the draft on a 409 and shows the saved version to compare', async () => {
    const message =
      'This row changed since you opened it. Your text is kept. Compare it with the saved version and save again.'
    const current: AidReviewRow = {
      ...ROWS[0],
      status: 'in_review',
      note: 'Another person saved this.',
      updated_by: 'other@example.edu',
      updated_at: '2026-10-05T15:00:00+00:00',
    }
    let gets = 0
    let patches = 0
    const calls: Call[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        const method = (init?.method ?? 'GET').toUpperCase()
        const body = typeof init?.body === 'string' ? JSON.parse(init.body) : null
        calls.push({ url, method, body, csrf: null })
        if (url === '/api/aid-queue' && method === 'GET') {
          gets += 1
          return jsonResponse({
            dataset_id: 1,
            fictional: true,
            rows: gets === 1 ? ROWS : [current, ROWS[1]],
          })
        }
        if (url === '/api/aid-queue/1' && method === 'PATCH') {
          patches += 1
          if (patches === 1) {
            return jsonResponse(
              {
                detail: 'This row changed since you opened it. Reload to see the latest.',
                row: current,
              },
              409,
            )
          }
          return jsonResponse({
            row: {
              ...current,
              ...(body as Partial<AidReviewRow>),
              updated_by: 'aid@example.edu',
              updated_at: '2026-10-05T16:00:00+00:00',
            },
            event_id: 10,
          })
        }
        return jsonResponse({ detail: `unhandled ${method} ${url}` }, 500)
      }),
    )
    render(<AidQueuePanel canEdit />)
    const row = await openRow('Student STU-0007')
    const box = within(row).getByLabelText('Note') as HTMLTextAreaElement
    fireEvent.change(box, { target: { value: 'My note.' } })
    fireEvent.click(within(row).getByRole('button', { name: 'Save' }))

    const alert = await within(row).findByRole('alert')
    expect(alert.textContent).toBe(message)
    const savedLine = await within(row).findByText(/^Saved version:/)
    expect(savedLine.textContent).toBe(
      'Saved version: In review. Note: Another person saved this.',
    )
    // The person's draft stays in the box; the reloaded row is the new base,
    // and a field they did not touch (the status) takes the saved value.
    expect(box.value).toBe('My note.')
    expect((within(row).getByLabelText('Status') as HTMLSelectElement).value).toBe('in_review')
    expect(row.querySelector('.aid-status')?.textContent).toBe('In review')
    expect(within(row).getByText(/Last updated by other@example\.edu/)).toBeTruthy()
    expect(gets).toBe(2)

    // Saving again sends the draft against the version just shown.
    const save = within(row).getByRole('button', { name: 'Save' }) as HTMLButtonElement
    expect(save.disabled).toBe(false)
    fireEvent.click(save)
    await within(row).findByText('Saved')
    const patchBodies = calls.filter((call) => call.method === 'PATCH').map((call) => call.body)
    expect(patchBodies).toEqual([
      { note: 'My note.', expected_updated_at: null },
      { note: 'My note.', expected_updated_at: '2026-10-05T15:00:00+00:00' },
    ])
    expect(within(row).queryByText(/^Saved version:/)).toBeNull()
    expect(within(row).queryByRole('alert')).toBeNull()
  })

  it('is read only without edit rights: no controls, the note as text', async () => {
    const { calls } = stubApi()
    render(<AidQueuePanel canEdit={false} />)
    const row = await openRow('Student STU-0120')
    // The only select is the status filter; no row has a form.
    expect(screen.queryAllByRole('combobox')).toHaveLength(1)
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
    expect(html).toContain('Queued 18 students')
    expect(html).toContain('Open the review queue')
  })

  it('gives staff the line but no button, since staff cannot read the rows', () => {
    const html = notice(QUEUED, { canOpen: false })
    expect(html).toContain('Queued 18 students')
    expect(html).not.toContain('Open the review queue')
  })

  it('offers to prepare the queue only after sign-off and only to roles that may', () => {
    expect(notice(NOT_YET)).toContain('Prepare the review queue')
    expect(notice(NOT_YET, { authorized: false })).toBe('')
    // A role that may not prepare reads where it stands, with no button.
    const readOnly = notice(NOT_YET, { canPrepare: false })
    expect(readOnly).toContain('Not prepared yet')
    expect(readOnly).not.toContain('<button')
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
        onPrepareAidQueue={() => {}}
        onOpenAidQueue={canOpen ? () => {} : null}
      />,
    )
  }

  it('tells the executive how many students were queued, with a way in', () => {
    const html = panel('executive', true)
    expect(html).toContain('Queued 18 students')
    expect(html).toContain('Open the review queue')
  })

  it('offers no Prepare when the approval belongs to an earlier dataset', () => {
    // The audit log still holds the earlier approval and its task, but GET
    // /decisions says the decision is not approved for the active dataset.
    const earlier = {
      id: 41,
      ts: '2026-10-01T12:00:00+00:00',
      type: 'task.created',
      actor: 'executive',
      payload: {
        decision_id: decision.id,
        task: {
          id: `TASK-${decision.id}`,
          decision_id: decision.id,
          office: 'Financial Aid',
          description: 'Conduct the review.',
          status: 'open',
        },
      },
    }
    const html = renderToStaticMarkup(
      <DecisionPanel
        decisions={[{ ...decision, approved: false }]}
        events={[earlier]}
        canApprove
        role="executive"
        userEmail="exec@example.edu"
        approving={false}
        approveError={null}
        approvedTasks={{}}
        dispatches={{
          [decision.id]: {
            info: { ...info, approved: false, aid_queue: NOT_YET },
            busy: null,
            error: null,
          },
        }}
        onApprove={() => {}}
        onPrepareDispatch={() => {}}
        onSendDispatch={() => {}}
        onPrepareAidQueue={() => {}}
        onOpenAidQueue={() => {}}
      />,
    )
    // The earlier dataset's task is not shown: this dataset's decision is not approved yet.
    expect(html).not.toContain('Next steps')
    expect(html).not.toContain('Prepare the review queue')
    expect(html).not.toContain('Prepare the message')
  })

  it('offers a first approval, not "Approve again", after a dataset switch', () => {
    const earlier = {
      id: 41,
      ts: '2026-10-01T12:00:00+00:00',
      type: 'task.created',
      actor: 'executive',
      payload: {
        decision_id: decision.id,
        task: {
          id: `TASK-${decision.id}`,
          decision_id: decision.id,
          office: 'Financial Aid',
          description: 'Conduct the review.',
          status: 'open',
        },
      },
    }
    const html = renderToStaticMarkup(
      <DecisionPanel
        decisions={[{ ...decision, approved: false }]}
        events={[earlier]}
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
      />,
    )
    expect(html).toContain('>Approve</button>')
    expect(html).not.toContain('Approve again')
    expect(html).not.toContain('Approved by')
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
      />,
    )
    expect(html).not.toContain('Queued 18 students')
  })
})
