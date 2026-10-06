// @vitest-environment jsdom

// Institution Users section: the list rendering (emails, roles, the
// admin's own "you" row), the add flow with the one-time password shown
// exactly once, and the disable flow behind its inline confirmation.
// Institution Offices section: loading, empty, error (with Retry), the
// decision office listed without a mailbox, validation under the field with
// nothing sent, saving (disabled Save), saved, and a failed save. The API is
// a mocked fetch; the component talks to it through the real
// ui/src/users.ts and ui/src/offices.ts clients.

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Institution } from './Institution'

// friendlyError belongs to ui/src/errors.ts (group B); stubbed here so the
// tests check that every failure goes through it, not its exact wording.
// A plain server sentence passes through, as the real helper does.
vi.mock('../errors', () => ({
  friendlyError: (error: unknown, action: string) => {
    const message = error instanceof Error ? error.message : ''
    return /^[A-Z].*\.$/.test(message) ? message : `Friendly: ${action}`
  },
}))

const ADMIN_ROW = {
  id: 1,
  email: 'admin@example.edu',
  role: 'admin',
  disabled: false,
  created_at: '2026-09-25T00:00:00+00:00',
}
const STAFF_ROW = {
  id: 2,
  email: 'staff@example.edu',
  role: 'staff',
  disabled: false,
  created_at: '2026-09-25T00:00:00+00:00',
}
const NEW_ROW = {
  id: 3,
  email: 'new@example.edu',
  role: 'staff',
  disabled: false,
  created_at: '2026-09-25T01:00:00+00:00',
}
const ONE_TIME_PASSWORD = 'pw-shown-exactly-once-1'

interface MockCall {
  url: string
  method: string
  body?: string
}

interface OfficeContact {
  office: string
  email: string
}

interface StubOptions {
  /** The address book GET /api/admin/offices starts with. */
  offices?: OfficeContact[]
  /** The follow-up offices GET /api/decisions answers with. */
  decisionOffices?: string[]
  /** A status for GET /api/admin/offices other than 200. */
  officesStatus?: number
  /** A status for PUT /api/admin/offices other than 200. */
  putStatus?: number
  /** When set, PUT waits for this promise before answering. */
  putGate?: Promise<void>
  /** Make these "METHOD url" calls fail as a network failure would. */
  reject?: string[]
  /** Answer these "METHOD url" calls with this status and detail. */
  refuse?: Record<string, [number, string]>
  /** When set, an upload is refused (422) with these lines. */
  uploadErrors?: string[]
  /** The datasets GET /api/admin/datasets lists. */
  datasets?: unknown[]
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** A fetch stub over the in-memory user list, recording every call. */
function stubApi(options: StubOptions = {}) {
  const users = [{ ...ADMIN_ROW }, { ...STAFF_ROW }]
  let offices: OfficeContact[] = [...(options.offices ?? [])]
  const calls: MockCall[] = []
  const stub = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined })
    const call = `${method} ${url}`
    if (options.reject?.includes(call)) throw new TypeError('Failed to fetch')
    const refusal = options.refuse?.[call]
    if (refusal !== undefined) return jsonResponse({ detail: refusal[1] }, refusal[0])
    if (url === '/api/admin/offices' && method === 'GET') {
      if (options.officesStatus !== undefined) {
        return jsonResponse({ detail: 'the office book is unavailable' }, options.officesStatus)
      }
      return jsonResponse({ offices })
    }
    if (url === '/api/admin/offices' && method === 'PUT') {
      if (options.putGate !== undefined) await options.putGate
      if (options.putStatus !== undefined) {
        return jsonResponse(
          {
            detail: 'the office address book failed validation',
            errors: ["office 'Bursar' appears twice"],
          },
          options.putStatus,
        )
      }
      const body = JSON.parse(String(init?.body)) as { offices: OfficeContact[] }
      offices = [...body.offices].sort((a, b) => a.office.localeCompare(b.office))
      return jsonResponse({ offices })
    }
    if (url === '/api/decisions' && method === 'GET') {
      return jsonResponse({
        question_id: 'q1',
        decisions: (options.decisionOffices ?? []).map((office, index) => ({
          id: `D-${index}`,
          title: 'A decision',
          text: 'Text.',
          follow_up: { office, description: 'Follow up.' },
          approved: false,
        })),
      })
    }
    if (url === '/api/admin/users' && method === 'GET') {
      return jsonResponse(users)
    }
    if (url === '/api/admin/users' && method === 'POST') {
      users.push({ ...NEW_ROW })
      return jsonResponse(
        {
          id: NEW_ROW.id,
          email: NEW_ROW.email,
          role: NEW_ROW.role,
          one_time_password: ONE_TIME_PASSWORD,
        },
        201,
      )
    }
    if (url === '/api/admin/users/2/disable' && method === 'POST') {
      users[1] = { ...users[1], disabled: true }
      return jsonResponse({ user: users[1], changed: true })
    }
    if (url === '/api/admin/institution/counseling-authorization' && method === 'GET') {
      return jsonResponse({
        authorized: false,
        authorized_by: null,
        document_reference: null,
        recorded_by: null,
        recorded_at: null,
      })
    }
    if (url === '/api/admin/datasets' && method === 'POST' && options.uploadErrors) {
      return jsonResponse(
        { detail: 'the dataset failed validation', errors: options.uploadErrors },
        422,
      )
    }
    if (url === '/api/admin/datasets' && method === 'GET') {
      return jsonResponse({ datasets: options.datasets ?? [] })
    }
    return jsonResponse({ detail: `unhandled ${method} ${url}` }, 500)
  })
  vi.stubGlobal('fetch', stub)
  return { calls, puts: () => calls.filter((c) => c.url === '/api/admin/offices' && c.method === 'PUT') }
}

function renderInstitution() {
  return render(
    <Institution
      institutionName="Golden Eagle College"
      activeDataset={null}
      currentUserEmail="admin@example.edu"
      onDataChanged={() => {}}
    />,
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Institution Users — the list', () => {
  it('renders the users with role, status, and "you" on the admin’s own row', async () => {
    stubApi()
    renderInstitution()

    await waitFor(() => screen.getByText('staff@example.edu'))
    expect(screen.getByText('admin@example.edu')).toBeTruthy()
    expect(screen.getByText('you')).toBeTruthy()
    // The role column is a select per row, showing the current role.
    expect(
      (screen.getByLabelText('Role for staff@example.edu') as HTMLSelectElement).value,
    ).toBe('staff')
    // Only the other user's row offers Disable; the admin's own row does not.
    expect(screen.getAllByRole('button', { name: 'Disable' }).length).toBe(1)
    // Statuses render.
    expect(screen.getAllByText('Active').length).toBe(2)
  })
})

describe('Institution Users — add flow', () => {
  it('shows the one-time password once, with the mandated sentence, then never again', async () => {
    const { calls } = stubApi()
    renderInstitution()
    await waitFor(() => screen.getByText('staff@example.edu'))

    fireEvent.change(screen.getByLabelText('Email'), {
      target: { value: 'new@example.edu' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Add the user' }))

    // The creation POST went out, and the callout carries the password.
    await waitFor(() => screen.getByText(ONE_TIME_PASSWORD))
    expect(
      calls.some((c) => c.url === '/api/admin/users' && c.method === 'POST'),
    ).toBe(true)
    expect(
      screen.getByText('Shown once. Share it privately; it is not stored.'),
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Copy the password' })).toBeTruthy()

    // Done dismisses the callout; the password is nowhere on the page, even
    // though the new user is now listed (the list reload included them).
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    await waitFor(() => screen.getByText('new@example.edu'))
    expect(document.body.innerHTML).not.toContain(ONE_TIME_PASSWORD)
  })
})

describe('Institution Users — disable flow', () => {
  it('requires the inline confirmation, and Cancel sends nothing', async () => {
    const { calls } = stubApi()
    renderInstitution()
    await waitFor(() => screen.getByText('staff@example.edu'))

    fireEvent.click(screen.getByRole('button', { name: 'Disable' }))
    const dialog = screen.getByRole('alertdialog')
    expect(dialog.textContent).toContain(
      'Disable staff@example.edu? They cannot sign in, and any open session ends at the next request.',
    )

    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(
      calls.some((c) => c.url.includes('/disable') && c.method === 'POST'),
    ).toBe(false)
  })

  it('confirming sends the disable POST and refreshes the list', async () => {
    const { calls } = stubApi()
    renderInstitution()
    await waitFor(() => screen.getByText('staff@example.edu'))

    fireEvent.click(screen.getByRole('button', { name: 'Disable' }))
    const dialog = screen.getByRole('alertdialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Disable' }))

    await waitFor(() => screen.getByText('Disabled'))
    expect(
      calls.some((c) => c.url === '/api/admin/users/2/disable' && c.method === 'POST'),
    ).toBe(true)
  })
})

describe('Institution Offices — loading, empty, error', () => {
  it('shows the loading line, then the empty state with the intro', async () => {
    stubApi()
    renderInstitution()

    expect(screen.getByText('Loading the office contacts…')).toBeTruthy()
    await waitFor(() =>
      screen.getByText('No office has a mailbox yet. Add the first one below.'),
    )
    expect(
      screen.getByText(
        'Where approved follow-ups are sent. Each office gets one mailbox. Nothing is sent until a staff member presses Send.',
      ),
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Add an office' })).toBeTruthy()
  })

  it('shows the error panel and loads again on Retry', async () => {
    stubApi({ officesStatus: 500 })
    renderInstitution()

    await waitFor(() => screen.getByText('We couldn’t load the office contacts'))
    expect(screen.getByText('Friendly: The office contacts')).toBeTruthy()
    expect(screen.queryByText('the office book is unavailable')).toBeNull()
    // The Users and Datasets sections are unaffected.
    await waitFor(() => screen.getByText('staff@example.edu'))

    vi.unstubAllGlobals()
    stubApi({ offices: [{ office: 'Bursar', email: 'bursar@example.edu' }] })
    const panel = screen.getByText('We couldn’t load the office contacts').closest('div')!
    fireEvent.click(within(panel).getByRole('button', { name: 'Retry' }))
    await waitFor(() => screen.getByLabelText('Mailbox for Bursar'))
  })
})

describe('Institution Offices — the book', () => {
  it('lists a decision office without a mailbox next to the saved ones', async () => {
    stubApi({
      offices: [{ office: 'Bursar', email: 'bursar@example.edu' }],
      decisionOffices: ['Financial Aid'],
    })
    renderInstitution()

    await waitFor(() => screen.getByLabelText('Mailbox for Financial Aid'))
    expect(
      (screen.getByLabelText('Mailbox for Bursar') as HTMLInputElement).value,
    ).toBe('bursar@example.edu')
    expect(
      (screen.getByLabelText('Mailbox for Financial Aid') as HTMLInputElement).value,
    ).toBe('')
    expect(screen.getByText('No mailbox yet. Approved follow-ups go here.')).toBeTruthy()
  })

  it('adds an office and saves the whole book, then says so', async () => {
    const { puts } = stubApi({ offices: [{ office: 'Bursar', email: 'bursar@example.edu' }] })
    renderInstitution()
    await waitFor(() => screen.getByLabelText('Mailbox for Bursar'))

    fireEvent.click(screen.getByRole('button', { name: 'Add an office' }))
    fireEvent.change(screen.getByLabelText('Office name, row 2'), {
      target: { value: 'Financial Aid' },
    })
    fireEvent.change(screen.getByLabelText('Mailbox for Financial Aid'), {
      target: { value: ' financial-aid@example.edu ' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save the office contacts' }))

    await waitFor(() =>
      screen.getByText('Saved. 2 offices have a mailbox. The audit log records the change.'),
    )
    expect(puts().length).toBe(1)
    expect(JSON.parse(puts()[0].body!)).toEqual({
      offices: [
        { office: 'Bursar', email: 'bursar@example.edu' },
        { office: 'Financial Aid', email: 'financial-aid@example.edu' },
      ],
    })
  })

  it('removes a mailbox by leaving it out of the next Save', async () => {
    const { puts } = stubApi({
      offices: [
        { office: 'Bursar', email: 'bursar@example.edu' },
        { office: 'Registrar', email: 'registrar@example.edu' },
      ],
    })
    renderInstitution()
    await waitFor(() => screen.getByLabelText('Mailbox for Registrar'))

    fireEvent.click(screen.getByRole('button', { name: 'Remove the mailbox for Registrar' }))
    expect(screen.queryByLabelText('Mailbox for Registrar')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Save the office contacts' }))

    await waitFor(() => screen.getByText(/Saved\. 1 office has a mailbox/))
    expect(JSON.parse(puts()[0].body!)).toEqual({
      offices: [{ office: 'Bursar', email: 'bursar@example.edu' }],
    })
  })
})

describe('Institution Offices — validation', () => {
  it('shows an invalid mailbox error under the field and sends nothing', async () => {
    const { puts } = stubApi({ decisionOffices: ['Financial Aid'] })
    renderInstitution()
    await waitFor(() => screen.getByLabelText('Mailbox for Financial Aid'))

    const input = screen.getByLabelText('Mailbox for Financial Aid')
    fireEvent.change(input, { target: { value: 'financial-aid' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save the office contacts' }))

    const message = await waitFor(() =>
      screen.getByText('Enter a mailbox address like office@example.edu.'),
    )
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(input.getAttribute('aria-describedby')).toBe(message.id)
    // The error sits in the same cell as the field it describes.
    expect(input.closest('td')!.contains(message)).toBe(true)
    expect(
      screen.getByText('Nothing was saved. Fix the marked fields, then save again.'),
    ).toBeTruthy()
    expect(puts().length).toBe(0)

    // Editing the field clears its error.
    fireEvent.change(input, { target: { value: 'financial-aid@example.edu' } })
    expect(screen.queryByText('Enter a mailbox address like office@example.edu.')).toBeNull()
  })

  it('points each field at its error by an id a screen reader can resolve', async () => {
    stubApi({
      offices: [{ office: 'Student Accounts', email: 'accounts@example.edu' }],
      decisionOffices: ['Financial Aid'],
    })
    renderInstitution()
    await waitFor(() => screen.getByLabelText('Mailbox for Financial Aid'))

    fireEvent.change(screen.getByLabelText('Mailbox for Student Accounts'), {
      target: { value: 'not a mailbox' },
    })
    fireEvent.change(screen.getByLabelText('Mailbox for Financial Aid'), {
      target: { value: 'financial-aid' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save the office contacts' }))
    await waitFor(() => screen.getAllByText('Enter a mailbox address like office@example.edu.'))

    for (const office of ['Student Accounts', 'Financial Aid']) {
      const input = screen.getByLabelText(`Mailbox for ${office}`)
      const ids = (input.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean)
      expect(ids.length).toBe(1)
      const described = document.getElementById(ids[0])
      expect(described).not.toBeNull()
      expect(described!.textContent).toBe('Enter a mailbox address like office@example.edu.')
      expect(input.closest('td')!.contains(described)).toBe(true)
    }
  })

  it('flags a duplicate office under the office name and sends nothing', async () => {
    const { puts } = stubApi({ offices: [{ office: 'Bursar', email: 'bursar@example.edu' }] })
    renderInstitution()
    await waitFor(() => screen.getByLabelText('Mailbox for Bursar'))

    fireEvent.click(screen.getByRole('button', { name: 'Add an office' }))
    fireEvent.change(screen.getByLabelText('Office name, row 2'), {
      target: { value: 'bursar' },
    })
    fireEvent.change(screen.getByLabelText('Mailbox for bursar'), {
      target: { value: 'other@example.edu' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save the office contacts' }))

    const message = await waitFor(() =>
      screen.getByText('bursar is already listed. Each office gets one mailbox.'),
    )
    expect(screen.getByLabelText('Office name, row 2').closest('td')!.contains(message)).toBe(
      true,
    )
    expect(puts().length).toBe(0)
  })

  it('asks for a mailbox on a new office that has only a name', async () => {
    const { puts } = stubApi()
    renderInstitution()
    await waitFor(() => screen.getByRole('button', { name: 'Add an office' }))

    fireEvent.click(screen.getByRole('button', { name: 'Add an office' }))
    fireEvent.change(screen.getByLabelText('Office name, row 1'), {
      target: { value: 'Registrar' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save the office contacts' }))

    await waitFor(() => screen.getByText('Enter the mailbox address, or remove the row.'))
    expect(puts().length).toBe(0)
  })
})

describe('Institution Offices — saving and failure', () => {
  it('disables Save with a spinner while saving', async () => {
    let release: () => void = () => {}
    const putGate = new Promise<void>((resolve) => {
      release = resolve
    })
    stubApi({ decisionOffices: ['Financial Aid'], putGate })
    renderInstitution()
    await waitFor(() => screen.getByLabelText('Mailbox for Financial Aid'))

    fireEvent.change(screen.getByLabelText('Mailbox for Financial Aid'), {
      target: { value: 'financial-aid@example.edu' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save the office contacts' }))

    const saving = await waitFor(() => screen.getByRole('button', { name: 'Saving…' }))
    expect((saving as HTMLButtonElement).disabled).toBe(true)
    expect(saving.getAttribute('aria-busy')).toBe('true')
    expect(saving.querySelector('.save-spinner')).not.toBeNull()
    expect((screen.getByLabelText('Mailbox for Financial Aid') as HTMLInputElement).disabled).toBe(
      true,
    )

    release()
    await waitFor(() => screen.getByText(/Saved\. 1 office has a mailbox/))
    expect(
      (screen.getByRole('button', { name: 'Save the office contacts' }) as HTMLButtonElement)
        .disabled,
    ).toBe(false)
  })

  it('says what failed and what to do when the API refuses the save', async () => {
    stubApi({ decisionOffices: ['Financial Aid'], putStatus: 422 })
    renderInstitution()
    await waitFor(() => screen.getByLabelText('Mailbox for Financial Aid'))

    fireEvent.change(screen.getByLabelText('Mailbox for Financial Aid'), {
      target: { value: 'financial-aid@example.edu' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save the office contacts' }))

    await waitFor(() => screen.getByText(/The office contacts were not saved\./))
    // The server's own lines are folded under "Technical detail".
    const line = screen.getByText("office 'Bursar' appears twice")
    expect(line.closest('details')!.querySelector('summary')!.textContent).toBe(
      'Technical detail',
    )
    expect(screen.queryByText(/failed validation/)).toBeNull()
    expect(screen.getByText(/The address book is unchanged; check the entries/)).toBeTruthy()
    // The entry is kept so the admin can correct it and try again.
    expect(
      (screen.getByLabelText('Mailbox for Financial Aid') as HTMLInputElement).value,
    ).toBe('financial-aid@example.edu')
  })
})

const INACTIVE_DATASET = {
  id: 7,
  name: 'Fall export',
  uploaded_by: 'admin@example.edu',
  uploaded_at: '2026-09-25T12:00:00+00:00',
  sha256: 'abc',
  row_counts: { students: 185, prior_year_students: 135 },
  is_active: false,
}

describe('Institution — layout', () => {
  it('opens with a short section list and four section cards', async () => {
    stubApi()
    renderInstitution()
    await waitFor(() => screen.getByText('staff@example.edu'))

    const nav = screen.getByRole('navigation', { name: 'Institution settings sections' })
    expect(within(nav).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'Users',
      'Offices',
      'Counseling',
      'Data',
    ])
    for (const link of within(nav).getAllByRole('link')) {
      const target = document.getElementById(link.getAttribute('href')!.slice(1))
      expect(target).not.toBeNull()
      expect(target!.classList.contains('inst-card')).toBe(true)
    }
    // Following a link moves focus to that section's heading.
    fireEvent.click(within(nav).getByRole('link', { name: 'Data' }))
    expect(document.activeElement).toBe(screen.getByRole('heading', { name: 'Data' }))
  })

  it('stacks every table on phones: each cell carries its column label', async () => {
    stubApi({
      offices: [{ office: 'Bursar', email: 'bursar@example.edu' }],
      datasets: [INACTIVE_DATASET],
    })
    renderInstitution()
    await waitFor(() => screen.getByText('Fall export'))
    await waitFor(() => screen.getByLabelText('Mailbox for Bursar'))

    const tables = document.querySelectorAll('table')
    expect(tables.length).toBe(3)
    for (const table of tables) {
      expect(table.classList.contains('stack-table')).toBe(true)
      for (const cell of table.querySelectorAll('tbody td')) {
        expect(cell.hasAttribute('data-label')).toBe(true)
      }
    }
    expect(screen.getByRole('columnheader', { name: 'Records' })).toBeTruthy()
  })
})

describe('Institution Users — add a user, validation and failures', () => {
  it('keeps Add the user enabled and puts an empty-email error under the field', async () => {
    const { calls } = stubApi()
    renderInstitution()
    await waitFor(() => screen.getByText('staff@example.edu'))

    const button = screen.getByRole('button', { name: 'Add the user' }) as HTMLButtonElement
    expect(button.disabled).toBe(false)
    const input = screen.getByLabelText('Email') as HTMLInputElement
    expect(input.placeholder).toBe('e.g. name@example.edu')
    fireEvent.click(button)

    const message = screen.getByText('Enter the email address of the person to add.')
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(input.getAttribute('aria-describedby')).toBe(message.id)
    expect(calls.some((c) => c.url === '/api/admin/users' && c.method === 'POST')).toBe(false)

    fireEvent.change(input, { target: { value: 'not-an-address' } })
    expect(screen.queryByText('Enter the email address of the person to add.')).toBeNull()
    fireEvent.click(button)
    expect(screen.getByText('Enter an email address like name@example.edu.')).toBeTruthy()
    expect(calls.some((c) => c.url === '/api/admin/users' && c.method === 'POST')).toBe(false)
  })

  it('puts a duplicate email under the field, in plain words', async () => {
    stubApi({
      refuse: {
        'POST /api/admin/users': [409, "a user with email 'staff@example.edu' already exists"],
      },
    })
    renderInstitution()
    await waitFor(() => screen.getByText('staff@example.edu'))

    const input = screen.getByLabelText('Email')
    fireEvent.change(input, { target: { value: 'staff@example.edu' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add the user' }))

    const message = await waitFor(() =>
      screen.getByText('Someone with that email can already sign in.'),
    )
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(input.getAttribute('aria-describedby')).toBe(message.id)
    expect(document.body.textContent).not.toContain('already exists')
  })

  it('a network failure clears Adding… and says so under the button', async () => {
    stubApi({ reject: ['POST /api/admin/users'] })
    renderInstitution()
    await waitFor(() => screen.getByText('staff@example.edu'))

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'new@example.edu' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add the user' }))

    await waitFor(() => screen.getByText('Friendly: The new user'))
    const button = screen.getByRole('button', { name: 'Add the user' }) as HTMLButtonElement
    expect(button.disabled).toBe(false)
    expect((screen.getByLabelText('Email') as HTMLInputElement).disabled).toBe(false)
    expect(screen.getByLabelText('Email').getAttribute('aria-invalid')).toBe('false')
    expect(document.body.textContent).not.toContain('Failed to fetch')
  })
})

describe('Institution Users — confirmations and failed changes', () => {
  it('a failed disable unlocks the row and shows a friendly line', async () => {
    stubApi({ reject: ['POST /api/admin/users/2/disable'] })
    renderInstitution()
    await waitFor(() => screen.getByText('staff@example.edu'))

    fireEvent.click(screen.getByRole('button', { name: 'Disable' }))
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Disable' }))

    await waitFor(() => screen.getByText('Friendly: The change'))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect((screen.getByRole('button', { name: 'Disable' }) as HTMLButtonElement).disabled).toBe(
      false,
    )
    expect(
      (screen.getByLabelText('Role for staff@example.edu') as HTMLSelectElement).disabled,
    ).toBe(false)
  })

  it('words the last-administrator refusal plainly', async () => {
    stubApi({
      refuse: {
        'PATCH /api/admin/users/2': [
          409,
          "the institution's last enabled administrator cannot be demoted",
        ],
      },
    })
    renderInstitution()
    await waitFor(() => screen.getByText('staff@example.edu'))

    fireEvent.change(screen.getByLabelText('Role for staff@example.edu'), {
      target: { value: 'reviewer' },
    })
    fireEvent.click(
      within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Change the role' }),
    )
    await waitFor(() =>
      screen.getByText(
        'This is the last active administrator. Make someone else an administrator first.',
      ),
    )
  })

  it('the role confirmation takes focus, Escape cancels it, focus returns to the role', async () => {
    const { calls } = stubApi()
    renderInstitution()
    await waitFor(() => screen.getByText('staff@example.edu'))

    const select = screen.getByLabelText('Role for staff@example.edu') as HTMLSelectElement
    select.focus()
    fireEvent.change(select, { target: { value: 'reviewer' } })
    const dialog = screen.getByRole('alertdialog')
    expect(dialog.textContent).toContain('Change staff@example.edu from Staff to Reviewer?')
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Cancel' }))

    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(document.activeElement).toBe(select)
    expect(select.value).toBe('staff')
    expect(calls.some((c) => c.method === 'PATCH')).toBe(false)
  })
})

describe('Institution Offices — focus after Remove', () => {
  it('moves focus to the next Remove, then to Add an office, never the page', async () => {
    stubApi({
      offices: [
        { office: 'Bursar', email: 'bursar@example.edu' },
        { office: 'Registrar', email: 'registrar@example.edu' },
      ],
    })
    renderInstitution()
    await waitFor(() => screen.getByLabelText('Mailbox for Registrar'))

    fireEvent.click(screen.getByRole('button', { name: 'Remove the mailbox for Bursar' }))
    expect(document.activeElement).toBe(
      screen.getByRole('button', { name: 'Remove the mailbox for Registrar' }),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Remove the mailbox for Registrar' }))
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Add an office' }))
  })

  it('a failed save clears Saving… and says so in plain words', async () => {
    stubApi({ decisionOffices: ['Financial Aid'], putStatus: 429 })
    renderInstitution()
    await waitFor(() => screen.getByLabelText('Mailbox for Financial Aid'))

    fireEvent.change(screen.getByLabelText('Mailbox for Financial Aid'), {
      target: { value: 'financial-aid@example.edu' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save the office contacts' }))
    await waitFor(() => screen.getByText('Friendly: The address book'))
    expect(
      (screen.getByRole('button', { name: 'Save the office contacts' }) as HTMLButtonElement)
        .disabled,
    ).toBe(false)
  })
})

describe('Institution Data — upload, activate, failures', () => {
  function pick(name: string, text: string) {
    const input = screen.getByLabelText('Data file (.json)') as HTMLInputElement
    const picked = new File([text], name, { type: 'application/json' })
    fireEvent.change(input, { target: { files: [picked] } })
  }

  it('uses plain words and a styled file button', async () => {
    stubApi()
    renderInstitution()
    await waitFor(() => screen.getByText(/Nothing has been uploaded yet/))

    expect(
      screen.getByText(
        "Upload your student-records export (up to 20 MB). We check it before saving and list anything we can’t accept.",
      ),
    ).toBeTruthy()
    const input = screen.getByLabelText('Data file (.json)') as HTMLInputElement
    expect(input.type).toBe('file')
    expect(input.classList.contains('visually-hidden')).toBe(true)
    expect(input.closest('label')!.textContent).toContain('Choose a file')
    expect(document.body.textContent).not.toMatch(/JSON|SCHEMA\.md|one time password/)
  })

  it('asks for a file first instead of a greyed button, and Upload turns main once one is chosen', async () => {
    stubApi()
    renderInstitution()
    await waitFor(() => screen.getByText(/Nothing has been uploaded yet/))

    const upload = screen.getByRole('button', { name: 'Upload' }) as HTMLButtonElement
    expect(upload.disabled).toBe(false)
    expect(upload.className).toBe('btn-secondary')
    fireEvent.click(upload)
    expect(screen.getByText('Choose a file first.')).toBeTruthy()

    pick('export.json', '{}')
    await waitFor(() => screen.getByText(/export\.json/))
    expect(upload.className).toBe('btn-primary')
  })

  it('refuses a file that is not the export, under the field', async () => {
    stubApi()
    renderInstitution()
    await waitFor(() => screen.getByText(/Nothing has been uploaded yet/))
    pick('export.csv', 'a,b')
    expect(
      screen.getByText('That file can’t be used. Choose the student-records export, a .json file.'),
    ).toBeTruthy()
  })

  it('folds a refused file’s problems under Technical detail', async () => {
    const { calls } = stubApi({
      uploadErrors: ["$.students[3].profile.student_id: 'Jane Doe' is not a pseudonymous id"],
    })
    renderInstitution()
    await waitFor(() => screen.getByText(/Nothing has been uploaded yet/))
    pick('export.json', '{}')
    await waitFor(() => screen.getByText(/export\.json/))
    fireEvent.click(screen.getByRole('button', { name: 'Upload' }))

    await waitFor(() => screen.getByText('We couldn’t accept this file. Nothing was saved.'))
    const line = screen.getByText(/is not a pseudonymous id/)
    expect(line.closest('details')!.querySelector('summary')!.textContent).toBe(
      'Technical detail (1 problem to fix in the export)',
    )
    expect(calls.some((c) => c.method === 'POST')).toBe(true)
  })

  it('a network failure during upload clears Uploading… and says so', async () => {
    stubApi({ reject: ['POST /api/admin/datasets'] })
    renderInstitution()
    await waitFor(() => screen.getByText(/Nothing has been uploaded yet/))
    pick('export.json', '{}')
    await waitFor(() => screen.getByText(/export\.json/))
    fireEvent.click(screen.getByRole('button', { name: 'Upload' }))

    await waitFor(() => screen.getByText('Friendly: The upload'))
    expect((screen.getByRole('button', { name: 'Upload' }) as HTMLButtonElement).disabled).toBe(
      false,
    )
  })

  it('a failed activation unlocks the row and shows a friendly line', async () => {
    stubApi({ datasets: [INACTIVE_DATASET], reject: ['POST /api/admin/datasets/7/activate'] })
    renderInstitution()
    await waitFor(() => screen.getByText('Fall export'))

    const activate = screen.getByRole('button', { name: 'Activate' })
    activate.focus()
    fireEvent.click(activate)
    const dialog = screen.getByRole('alertdialog')
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Cancel' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Activate' }))

    await waitFor(() => screen.getByText('Friendly: The activation'))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect((screen.getByRole('button', { name: 'Activate' }) as HTMLButtonElement).disabled).toBe(
      false,
    )
    // Focus went back to the button that opened the confirmation.
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Activate' }))
  })

  it('words the refusal to delete the active data plainly', async () => {
    stubApi({
      datasets: [INACTIVE_DATASET],
      refuse: {
        'DELETE /api/admin/datasets/7': [
          409,
          'the active dataset cannot be deleted; activate another dataset first',
        ],
      },
    })
    renderInstitution()
    await waitFor(() => screen.getByText('Fall export'))
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Delete' }))
    await waitFor(() =>
      screen.getByText("The active data can't be deleted. Activate another upload first."),
    )
  })

  it('a failed list load shows the friendly line and Retry, never the empty text', async () => {
    stubApi({ reject: ['GET /api/admin/datasets'] })
    renderInstitution()
    await waitFor(() => screen.getByText('We couldn’t load the uploads'))
    expect(screen.getByText('Friendly: The data list')).toBeTruthy()
    expect(screen.queryByText(/Nothing has been uploaded yet/)).toBeNull()
    expect(screen.queryByText('Loading the uploads…')).toBeNull()
    expect(screen.getAllByRole('button', { name: 'Retry' }).length).toBe(1)
  })
})
