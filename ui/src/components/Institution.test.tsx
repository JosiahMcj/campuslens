// @vitest-environment jsdom

// Institution Users section: the list rendering (emails, roles, the
// admin's own "you" row), the add flow with the one-time password shown
// exactly once, and the disable flow behind its inline confirmation. The
// API is a mocked fetch; the component talks to it through the real
// ui/src/users.ts client.

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Institution } from './Institution'

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
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** A fetch stub over the in-memory user list, recording every call. */
function stubApi() {
  const users = [{ ...ADMIN_ROW }, { ...STAFF_ROW }]
  const calls: MockCall[] = []
  const stub = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ url, method })
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
    if (url === '/api/admin/datasets' && method === 'GET') {
      return jsonResponse({ datasets: [] })
    }
    return jsonResponse({ detail: `unhandled ${method} ${url}` }, 500)
  })
  vi.stubGlobal('fetch', stub)
  return { calls }
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
