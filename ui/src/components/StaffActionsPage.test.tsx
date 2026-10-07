// @vitest-environment jsdom

// The Staff actions worklist page: loading, error with Retry, empty; the
// summary strip and the office and status filters; saving status, owner and
// due date with the version it was opened at (and a refused save keeping
// the person's choices); notes; Send to office (sent, failed with Retry, no
// mailbox); and the read-only and comment-only views. The API is a mocked
// fetch; the page talks to it through the real ui/src/staffActions.ts.

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  changeWords,
  dueLabel,
  isPastDue,
  ownerLabel,
  statusCounts,
  type StaffAction,
  type StaffActionList,
} from '../staffActions'
import { StaffActionsPage } from './StaffActionsPage'

function action(overrides: Partial<StaffAction>): StaffAction {
  return {
    id: 1,
    office: 'Bursar',
    finding_id: 'M5',
    count: 24,
    title: "Resolve the Bursar office's holds",
    what: 'Work through the unresolved holds recorded for the Bursar office.',
    noun: '24 unresolved holds',
    status: 'todo',
    owner: null,
    due_date: null,
    updated_by: null,
    updated_at: null,
    created_at: '2026-10-06T12:00:00+00:00',
    notes: [],
    history: [],
    office_mailbox: 'bursar@example.edu',
    message: null,
    ...overrides,
  }
}

const BURSAR = action({})
const LIBRARY = action({
  id: 2,
  office: 'Library',
  noun: '1 unresolved hold',
  count: 1,
  title: "Resolve the Library office's holds",
  status: 'in_progress',
  office_mailbox: null,
})
const AID = action({
  id: 3,
  office: 'Financial Aid',
  finding_id: 'M3',
  count: 18,
  noun: '18 small-balance cases (holds under $1,000)',
  title: 'Review small-balance holds under $1,000',
  status: 'done',
})

function list(overrides: Partial<StaffActionList> = {}): StaffActionList {
  return {
    dataset_id: 1,
    fictional: true,
    assignees: ['staff@example.edu', 'admin@example.edu'],
    can_edit: true,
    can_note: true,
    can_send: true,
    items: [BURSAR, LIBRARY, AID],
    ...overrides,
  }
}

interface Call {
  url: string
  method: string
  body: unknown
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

type Handler = (call: Call) => Response | Promise<Response>

function stub(handlers: Record<string, Handler>) {
  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined
      const call = { url, method, body }
      calls.push(call)
      const handler = handlers[`${method} ${url}`]
      if (handler === undefined) return jsonResponse({ detail: 'unhandled' }, 500)
      return handler(call)
    }),
  )
  return calls
}

function renderPage(onOpenEvidence = vi.fn()) {
  render(
    <StaffActionsPage
      onOpenEvidence={onOpenEvidence}
      onOpenInstitution={null}
      viewerEmail="staff@example.edu"
    />,
  )
  return onOpenEvidence
}

function card(title: string): HTMLElement {
  return screen.getByRole('article', { name: title })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('staff action helpers', () => {
  it('names the owner, the due date and each change in plain words', () => {
    expect(ownerLabel(null, 'Bursar', 'a@x.edu')).toBe('Bursar office')
    expect(ownerLabel('A@x.edu', 'Bursar', 'a@x.edu')).toBe('You')
    expect(ownerLabel('b@x.edu', 'Bursar', 'a@x.edu')).toBe('b@x.edu')
    expect(dueLabel('2026-10-20')).toBe('Oct 20, 2026')
    expect(isPastDue({ due_date: '2026-10-01', status: 'todo' }, '2026-10-06')).toBe(true)
    expect(isPastDue({ due_date: '2026-10-01', status: 'done' }, '2026-10-06')).toBe(false)
    expect(isPastDue({ due_date: null, status: 'todo' }, '2026-10-06')).toBe(false)
    const change = { id: 1, actor: 'a@x.edu', at: '2026-10-06T12:00:00+00:00' }
    expect(
      changeWords({ ...change, change: 'status', from_value: 'todo', to_value: 'done' }, 'Bursar', null),
    ).toBe('moved it from To do to Done')
    expect(
      changeWords({ ...change, change: 'owner', from_value: 'b@x.edu', to_value: null }, 'Bursar', null),
    ).toBe('gave it back to the Bursar office')
    expect(
      changeWords({ ...change, change: 'due_date', from_value: null, to_value: '2026-10-20' }, 'Bursar', null),
    ).toBe('set the due date to Oct 20, 2026')
    expect(statusCounts([BURSAR, LIBRARY, AID])).toEqual({ todo: 1, in_progress: 1, done: 1 })
  })
})

describe('Staff actions page', () => {
  it('shows a loading line, then an error with Retry that loads again', async () => {
    let fail = true
    stub({
      'GET /api/staff-actions': () =>
        fail ? jsonResponse({ detail: 'boom' }, 500) : jsonResponse(list()),
    })
    renderPage()
    expect(screen.getByText('Loading the staff actions…')).toBeTruthy()
    await waitFor(() => screen.getByText(/Couldn't load the staff actions/))
    fail = false
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => card("Resolve the Bursar office's holds"))
  })

  it('says what to expect when there are no actions', async () => {
    stub({ 'GET /api/staff-actions': () => jsonResponse(list({ items: [] })) })
    renderPage()
    await waitFor(() => screen.getByText(/There are no staff actions for the data in use/))
  })

  it('counts by status, filters by office and status, and links each count to its evidence', async () => {
    stub({ 'GET /api/staff-actions': () => jsonResponse(list()) })
    const onOpen = renderPage()
    await waitFor(() => card("Resolve the Bursar office's holds"))
    const strip = screen.getByRole('group', { name: 'Actions by status' })
    expect(within(strip).getByRole('button', { name: '1 To do' })).toBeTruthy()

    fireEvent.click(within(strip).getByRole('button', { name: '1 Done' }))
    expect(screen.getAllByRole('article')).toHaveLength(1)
    expect(screen.getByText('Showing 1 of 3 actions, demonstration data.')).toBeTruthy()
    fireEvent.click(within(strip).getByRole('button', { name: '1 Done' }))
    expect(screen.getAllByRole('article')).toHaveLength(3)

    const filters = screen.getByRole('group', { name: 'Filter the actions' })
    fireEvent.change(within(filters).getByLabelText('Office'), { target: { value: 'Library' } })
    expect(screen.getAllByRole('article')).toHaveLength(1)
    fireEvent.change(within(filters).getByLabelText('Status'), { target: { value: 'done' } })
    expect(screen.getByText('No actions match these choices.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Show every action' }))
    expect(screen.getAllByRole('article')).toHaveLength(3)

    fireEvent.click(within(card("Resolve the Bursar office's holds")).getByRole('button', { name: /24 unresolved holds/ }))
    expect(onOpen).toHaveBeenCalledWith('M5')
  })

  it('saves only what changed, with the version it was opened at', async () => {
    const calls = stub({
      'GET /api/staff-actions': () => jsonResponse(list()),
      'PATCH /api/staff-actions/1': (call) => {
        const body = call.body as Record<string, unknown>
        return jsonResponse({
          item: action({
            status: body.status as StaffAction['status'],
            owner: body.owner as string,
            due_date: body.due_date as string,
            updated_by: 'staff@example.edu',
            updated_at: '2026-10-06T13:00:00+00:00',
            history: [
              {
                id: 1,
                actor: 'staff@example.edu',
                at: '2026-10-06T13:00:00+00:00',
                change: 'status',
                from_value: 'todo',
                to_value: 'in_progress',
              },
            ],
          }),
        })
      },
    })
    renderPage()
    await waitFor(() => card("Resolve the Bursar office's holds"))
    const bursar = card("Resolve the Bursar office's holds")
    // Nothing changed yet: no Save button.
    expect(within(bursar).queryByRole('button', { name: 'Save changes' })).toBeNull()
    fireEvent.change(within(bursar).getByLabelText('Status'), { target: { value: 'in_progress' } })
    fireEvent.change(within(bursar).getByLabelText('Owner'), { target: { value: 'staff@example.edu' } })
    fireEvent.change(within(bursar).getByLabelText('Due date'), { target: { value: '2026-10-20' } })
    fireEvent.click(within(bursar).getByRole('button', { name: 'Save changes' }))
    await waitFor(() => within(card("Resolve the Bursar office's holds")).getByText('Saved.'))
    const patch = calls.find((call) => call.method === 'PATCH')
    expect(patch?.body).toEqual({
      status: 'in_progress',
      owner: 'staff@example.edu',
      due_date: '2026-10-20',
      expected_updated_at: null,
    })
    const updated = card("Resolve the Bursar office's holds")
    expect(within(updated).getByText('In progress', { selector: '.action-status' })).toBeTruthy()
    expect(within(updated).getByText('History (1)')).toBeTruthy()
    expect(updated.textContent).toContain('You moved it from To do to In progress.')
  })

  it('keeps the choices when someone else saved first, and shows the latest', async () => {
    stub({
      'GET /api/staff-actions': () => jsonResponse(list()),
      'PATCH /api/staff-actions/1': () =>
        jsonResponse(
          {
            detail: 'Someone else changed this action.',
            item: action({
              status: 'done',
              updated_by: 'admin@example.edu',
              updated_at: '2026-10-06T13:00:00+00:00',
            }),
          },
          409,
        ),
    })
    renderPage()
    await waitFor(() => card("Resolve the Bursar office's holds"))
    const bursar = card("Resolve the Bursar office's holds")
    fireEvent.change(within(bursar).getByLabelText('Status'), { target: { value: 'in_progress' } })
    fireEvent.click(within(bursar).getByRole('button', { name: 'Save changes' }))
    await waitFor(() =>
      within(card("Resolve the Bursar office's holds")).getByText(/Someone else changed this action since you opened it/),
    )
    const after = card("Resolve the Bursar office's holds")
    expect((within(after).getByLabelText('Status') as HTMLSelectElement).value).toBe('in_progress')
    expect(after.textContent).toContain('Latest: Done, Bursar office, no due date.')
    expect(within(after).getByRole('button', { name: 'Save changes' })).toBeTruthy()
  })

  it('adds a note', async () => {
    const calls = stub({
      'GET /api/staff-actions': () => jsonResponse(list()),
      'POST /api/staff-actions/1/notes': () =>
        jsonResponse({
          item: action({
            notes: [
              {
                id: 1,
                author: 'staff@example.edu',
                text: 'Called the office.',
                created_at: '2026-10-06T13:00:00+00:00',
              },
            ],
          }),
        }),
    })
    renderPage()
    await waitFor(() => card("Resolve the Bursar office's holds"))
    const bursar = card("Resolve the Bursar office's holds")
    fireEvent.click(within(bursar).getByText('Notes: add the first one'))
    fireEvent.change(within(bursar).getByLabelText('Add a note'), {
      target: { value: 'Called the office.' },
    })
    fireEvent.click(within(bursar).getByRole('button', { name: 'Add note' }))
    await waitFor(() => within(card("Resolve the Bursar office's holds")).getByText('Notes (1)'))
    expect(calls.find((call) => call.url.endsWith('/notes'))?.body).toEqual({
      text: 'Called the office.',
    })
    // Focus goes back to the box (never to the page), and the page says so.
    const after = card("Resolve the Bursar office's holds")
    expect(document.activeElement).toBe(within(after).getByLabelText('Add a note'))
    expect(within(after).getByRole('status').textContent).toBe('Note added.')
  })

  it('never capitalises an email address at the start of a line', async () => {
    stub({
      'GET /api/staff-actions': () =>
        jsonResponse(
          list({
            items: [
              action({
                updated_by: 'reviewer@example.edu',
                updated_at: '2026-10-06T13:00:00+00:00',
                notes: [
                  {
                    id: 1,
                    author: 'reviewer@example.edu',
                    text: 'Checked.',
                    created_at: '2026-10-06T13:00:00+00:00',
                  },
                ],
                history: [
                  {
                    id: 1,
                    actor: 'reviewer@example.edu',
                    at: '2026-10-06T13:00:00+00:00',
                    change: 'note',
                    from_value: null,
                    to_value: null,
                  },
                ],
              }),
            ],
          }),
        ),
    })
    renderPage()
    await waitFor(() => card("Resolve the Bursar office's holds"))
    const text = card("Resolve the Bursar office's holds").textContent ?? ''
    expect(text).toContain('reviewer@example.edu')
    expect(text).not.toContain('Reviewer@example.edu')
  })

  it('shows one notice when no office has a mailbox, never a paragraph per card', async () => {
    stub({
      'GET /api/staff-actions': () =>
        jsonResponse(
          list({
            items: [
              action({ office_mailbox: null }),
              { ...LIBRARY, office_mailbox: null },
              { ...AID, office_mailbox: null },
            ],
          }),
        ),
    })
    renderPage()
    await waitFor(() => card("Resolve the Bursar office's holds"))
    const notices = document.querySelectorAll('.worklist-notice')
    expect(notices).toHaveLength(1)
    expect(notices[0].textContent).toBe(
      'No office mailboxes are set yet, so nothing can be sent to an office. An administrator can add them in Institution settings.',
    )
    expect(screen.getAllByText("Can't send yet: no mailbox for this office.")).toHaveLength(3)
    expect(screen.queryByRole('button', { name: /Send to/ })).toBeNull()
  })

  it('sends to the office mailbox, and shows who sent it', async () => {
    stub({
      'GET /api/staff-actions': () => jsonResponse(list()),
      'POST /api/staff-actions/1/send': () =>
        jsonResponse({
          item: action({
            message: {
              status: 'sent',
              to_office: 'Bursar',
              subject: 'Staff action for Bursar',
              body: 'To the Bursar office,',
              sent_by: 'staff@example.edu',
              sent_at: '2026-10-06T13:00:00+00:00',
              error: null,
            },
          }),
        }),
    })
    renderPage()
    await waitFor(() => card("Resolve the Bursar office's holds"))
    const bursar = card("Resolve the Bursar office's holds")
    expect(bursar.textContent).toContain('Goes to bursar@example.edu with the count and a sign-in link')
    fireEvent.click(within(bursar).getByRole('button', { name: 'Send to Bursar' }))
    await waitFor(() =>
      within(card("Resolve the Bursar office's holds")).getByText(/Sent to the Bursar office \(bursar@example.edu\) by you/),
    )
    expect(within(card("Resolve the Bursar office's holds")).queryByRole('button', { name: 'Send to Bursar' })).toBeNull()
  })

  it('shows a failed send plainly, with Retry', async () => {
    stub({
      'GET /api/staff-actions': () => jsonResponse(list()),
      'POST /api/staff-actions/1/send': () =>
        jsonResponse(
          {
            detail: 'The message was not sent: smtp said no',
            item: action({
              message: {
                status: 'failed',
                to_office: 'Bursar',
                subject: 's',
                body: 'b',
                sent_by: null,
                sent_at: null,
                error: 'smtp said no',
              },
            }),
          },
          503,
        ),
    })
    renderPage()
    await waitFor(() => card("Resolve the Bursar office's holds"))
    fireEvent.click(within(card("Resolve the Bursar office's holds")).getByRole('button', { name: 'Send to Bursar' }))
    await waitFor(() =>
      within(card("Resolve the Bursar office's holds")).getByRole('button', { name: 'Retry sending' }),
    )
    const after = card("Resolve the Bursar office's holds")
    expect(after.textContent).toContain('The message did not reach the office mailbox. Nothing was delivered.')
    expect(after.textContent).not.toContain('smtp said no')
  })

  it('says "Retry sending" after a dropped connection, with the error under the button', async () => {
    let calls = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if ((init?.method ?? 'GET') === 'GET' && url === '/api/staff-actions') {
          return jsonResponse(list())
        }
        calls += 1
        throw new TypeError('Failed to fetch')
      }),
    )
    renderPage()
    await waitFor(() => card("Resolve the Bursar office's holds"))
    fireEvent.click(within(card("Resolve the Bursar office's holds")).getByRole('button', { name: 'Send to Bursar' }))
    const retry = await waitFor(() =>
      within(card("Resolve the Bursar office's holds")).getByRole('button', { name: 'Retry sending' }),
    )
    expect(calls).toBe(1)
    // The reason sits directly under the button that failed.
    expect(retry.nextElementSibling?.getAttribute('role')).toBe('alert')
  })

  it('says when an office has no mailbox, instead of offering Send', async () => {
    stub({ 'GET /api/staff-actions': () => jsonResponse(list()) })
    renderPage()
    await waitFor(() => card("Resolve the Library office's holds"))
    const library = card("Resolve the Library office's holds")
    // One notice for the page; each card only says it cannot go yet.
    expect(library.textContent).toContain("Can't send yet: no mailbox for this office.")
    expect(within(library).queryByRole('button', { name: /Send to/ })).toBeNull()
    const notices = document.querySelectorAll('.worklist-notice')
    expect(notices).toHaveLength(1)
    expect(notices[0].textContent).toContain('No mailbox is set yet for Library')
  })

  it('is read only for a reader, and comment-only for the executive', async () => {
    stub({
      'GET /api/staff-actions': () =>
        jsonResponse(list({ can_edit: false, can_note: false, can_send: false })),
    })
    renderPage()
    await waitFor(() => card("Resolve the Bursar office's holds"))
    expect(screen.queryByRole('button', { name: 'Save changes' })).toBeNull()
    expect(document.querySelector('.action-editor')).toBeNull()
    expect(screen.queryByRole('button', { name: /Send to/ })).toBeNull()
    const bursar = card("Resolve the Bursar office's holds")
    expect(bursar.textContent).toContain('OwnerBursar office')
    expect(bursar.textContent).toContain('Not sent to the office yet. A staff member sends it.')
    cleanup()

    stub({
      'GET /api/staff-actions': () =>
        jsonResponse(list({ can_edit: false, can_note: true, can_send: false })),
    })
    renderPage()
    await waitFor(() => card("Resolve the Bursar office's holds"))
    expect(within(card("Resolve the Bursar office's holds")).getByLabelText('Add a note')).toBeTruthy()
  })
})
