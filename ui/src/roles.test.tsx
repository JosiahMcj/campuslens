// @vitest-environment jsdom

// Department accounts and the inbox (docs/ROLES.md): each sign-in sees its
// own pages in the sidebar and its own greeting; IT gets a workspace that
// never asks for a figure; the inbox opens, marks read and marks reviewed;
// and Send alert posts what it points at, never a snapshot of its own.

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'
import { resetBriefingOnce } from './api'
import { clearSession } from './auth'
import { AuditLog } from './components/AuditLog'
import { InboxPage } from './components/InboxPage'
import { AccountsPage } from './components/ItPages'
import { SendAlertDialog } from './components/SendAlertDialog'

type Handler = (url: string, init?: RequestInit) => Promise<Response> | Response

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function session(role: string) {
  return {
    user: {
      id: 9,
      email: `${role}@demo.test`,
      role,
      institution_id: 1,
      institution: { slug: 'bootstrap', name: 'Demonstration University' },
    },
    csrf_token: 'token',
  }
}

const FINDINGS = {
  meta: {
    as_of: null,
    fixture: 'demo',
    terms: {},
    fictional: true,
    dataset: { id: 1, name: 'Demonstration (fictional)', sha256: 'x' },
  },
}

const MESSAGE = {
  id: 7,
  from: { id: 2, email: 'president@demo.test', role: 'executive' },
  to: { id: 9, email: 'finance@demo.test', role: 'finance' },
  note: 'Please look at the holds before the cabinet meeting.',
  review_by: '2026-10-15',
  source_kind: 'finding',
  source_ref: 'M5',
  snapshot: {
    id: 'M5',
    title: 'Students with a registration hold',
    display: '1,204',
    definition: 'Continuing students with at least one active hold.',
    reason: null,
    dataset: 'Demonstration (fictional)',
  },
  created_at: '2026-10-07T15:00:00+00:00',
  read_at: null as string | null,
  reviewed_at: null as string | null,
}

/** A healthy API for `role`; returns every URL requested. */
function mockApi(role: string, overrides: Record<string, Handler> = {}) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push(`${init?.method ?? 'GET'} ${url}`)
      const path = url.replace(/^\/api/, '').split('?')[0]
      if (overrides[path] !== undefined) return overrides[path](url, init)
      switch (path) {
        case '/auth/me':
          return json(session(role))
        case '/findings':
          return json(FINDINGS)
        case '/questions':
          return json([])
        case '/decisions':
          return json({ question_id: null, decisions: [] })
        case '/events':
          return json({ events: [] })
        case '/briefing':
          return json({ detail: 'none' }, 404)
        case '/explore/catalog':
          return json({ examples: ['Which majors have the highest average GPA?'] })
        case '/inbox':
          return json({ received: [], sent: [], unread: 0 })
        default:
          return json({ detail: 'not found' }, 404)
      }
    }),
  )
  return calls
}

function sidebarRows(): string[] {
  const nav = screen.getByRole('complementary', { name: 'CampusLens navigation' })
  return within(nav)
    .getAllByRole('button')
    .map((button) => button.getAttribute('title') ?? '')
    .filter((title) => title !== '')
}

beforeEach(() => {
  clearSession()
  window.sessionStorage.clear()
  resetBriefingOnce()
  window.history.replaceState(null, '', '/')
  Element.prototype.scrollIntoView = vi.fn()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('navigation by role', () => {
  it('shows Finance only its department pages, its greeting and its three questions', async () => {
    mockApi('finance')
    render(<App />)
    expect(await screen.findByText('What would you like to know?')).toBeTruthy()
    expect(screen.getAllByText('Finance — Student Accounts').length).toBeGreaterThan(0)
    expect(await screen.findByText('Which offices hold the most active holds?')).toBeTruthy()
    expect(screen.getByText('What is the 6-year graduation rate for Pell students by college?')).toBeTruthy()
    const rows = sidebarRows()
    expect(rows).toContain('Inbox')
    expect(rows).toContain('Department overview')
    for (const hidden of ['Full briefing', 'Decision', 'Audit log', 'Staff actions', 'Data access']) {
      expect(rows).not.toContain(hidden)
    }
  })

  it('shows the president every page plus the inbox, the overviews and sign-in activity', async () => {
    mockApi('executive')
    render(<App />)
    expect(await screen.findByText('What would you like to know?')).toBeTruthy()
    expect(document.querySelector('.persona-kicker')?.textContent).toBe('President')
    const rows = sidebarRows()
    for (const shown of [
      'Inbox',
      'Department overview',
      'Full briefing',
      'Decision',
      'Audit log',
      'Sign-in activity',
      'Financial Aid review',
    ]) {
      expect(rows).toContain(shown)
    }
  })

  it('gives IT its own workspace and never asks for a figure', async () => {
    const calls = mockApi('it')
    render(<App />)
    expect(await screen.findByText('What needs looking after?')).toBeTruthy()
    const rows = sidebarRows()
    for (const shown of ['Inbox', 'Accounts', 'Sign-in activity', 'Connections', 'Audit log']) {
      expect(rows).toContain(shown)
    }
    for (const hidden of ['Full briefing', 'Department overview', 'New question']) {
      expect(rows).not.toContain(hidden)
    }
    expect(screen.queryByLabelText('Ask CampusLens a question')).toBeNull()
    const touched = calls.filter((call) =>
      /\/api\/(findings|briefing|decisions|questions|explore)/.test(call),
    )
    expect(touched).toEqual([])
  })

  it('puts the unread count on the Inbox row', async () => {
    mockApi('registrar', { '/inbox': () => json({ received: [], sent: [], unread: 2 }) })
    render(<App />)
    expect(await screen.findByLabelText('2 unread')).toBeTruthy()
  })
})

describe('the inbox', () => {
  it('opens an alert (marked read), shows what it points at, and marks it reviewed', async () => {
    const posted: string[] = []
    let current = { ...MESSAGE }
    mockApi('finance', {
      '/inbox': () => json({ received: [current], sent: [], unread: current.read_at ? 0 : 1 }),
      '/inbox/7/read': (url) => {
        posted.push(url)
        current = { ...current, read_at: '2026-10-07T15:05:00+00:00' }
        return json({ message: current, changed: true })
      },
      '/inbox/7/reviewed': (url) => {
        posted.push(url)
        current = { ...current, reviewed_at: '2026-10-07T15:06:00+00:00' }
        return json({ message: current, changed: true })
      },
    })
    const changed = vi.fn()
    render(<InboxPage onChanged={changed} onAsk={null} />)
    const open = await screen.findByRole('button', { name: /From President/ })
    expect(screen.getByText('Received (1 new)')).toBeTruthy()
    fireEvent.click(open)
    expect(await screen.findByText('1,204')).toBeTruthy()
    expect(screen.getByText('Students with a registration hold')).toBeTruthy()
    expect(screen.getByText('Review by Oct 15, 2026')).toBeTruthy()
    await waitFor(() => expect(posted).toEqual(['/api/inbox/7/read']))
    fireEvent.click(screen.getByRole('button', { name: 'Mark reviewed' }))
    await waitFor(() => expect(posted).toEqual(['/api/inbox/7/read', '/api/inbox/7/reviewed']))
    expect(await screen.findByText('Reviewed')).toBeTruthy()
    expect(changed).toHaveBeenCalled()
  })

  it('shows the sender whether each alert was read and reviewed', async () => {
    mockApi('executive', {
      '/inbox': () =>
        json({
          received: [],
          sent: [{ ...MESSAGE, read_at: '2026-10-07T15:05:00+00:00' }],
          unread: 0,
        }),
    })
    render(<InboxPage onChanged={() => undefined} onAsk={null} />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Sent' }))
    expect(await screen.findByText(/To Finance — Student Accounts/)).toBeTruthy()
    expect(screen.getByText('Read')).toBeTruthy()
  })

  it('says when the attached figure is no longer available, and labels quotes', async () => {
    mockApi('finance', {
      '/inbox': () =>
        json({
          received: [
            { ...MESSAGE, snapshot: null, attachment_available: false },
            {
              ...MESSAGE,
              id: 8,
              source_kind: 'explore',
              source_ref: null,
              attachment_available: true,
              snapshot: {
                question: 'Which offices hold the most active holds?',
                answer: ['Student Accounts holds the most.'],
                answer_withheld: false,
                quoted_by_sender: true,
              },
            },
          ],
          sent: [],
          unread: 2,
        }),
      '/inbox/7/read': () => json({ message: { ...MESSAGE, snapshot: null, attachment_available: false }, changed: true }),
      '/inbox/8/read': () => json({ message: MESSAGE, changed: true }),
    })
    render(<InboxPage onChanged={() => undefined} onAsk={null} />)
    const opens = await screen.findAllByRole('button', { name: /From President/ })
    fireEvent.click(opens[0])
    expect(await screen.findByText(/no longer available/)).toBeTruthy()
    fireEvent.click(opens[1])
    expect(await screen.findByText(/quoted by the sender/)).toBeTruthy()
  })

  it('says so when there is nothing yet', async () => {
    mockApi('aid')
    render(<InboxPage onChanged={() => undefined} onAsk={null} />)
    expect(await screen.findByText(/Nothing here yet/)).toBeTruthy()
  })
})

describe('Send alert', () => {
  it('sends the note, the date and what it points at, and confirms who got it', async () => {
    let body: Record<string, unknown> | null = null
    let asked = ''
    mockApi('executive', {
      '/inbox/recipients': (url) => {
        asked = url
        return (
        json([
          { id: 9, email: 'finance@demo.test', role: 'finance' },
          { id: 10, email: 'registrar@demo.test', role: 'registrar' },
        ])
        )
      },
      '/inbox': (_url, init) => {
        body = JSON.parse(String(init?.body)) as Record<string, unknown>
        return json({ ...MESSAGE, id: 8 }, 201)
      },
    })
    render(
      <SendAlertDialog
        source={{ kind: 'finding', ref: 'M5', label: 'Students with a registration hold: 1,204' }}
        onClose={() => undefined}
      />,
    )
    expect(screen.getByText('Attached: Students with a registration hold: 1,204')).toBeTruthy()
    const who = (await screen.findByLabelText('Send to')) as HTMLSelectElement
    await waitFor(() => expect(who.options.length).toBe(3))
    // Nothing chosen yet: the dialog says so instead of sending.
    fireEvent.click(screen.getByRole('button', { name: 'Send alert' }))
    expect(screen.getByText('Choose who should see this.')).toBeTruthy()
    fireEvent.change(who, { target: { value: '9' } })
    fireEvent.change(screen.getByLabelText('Note'), { target: { value: 'Before Thursday, please.' } })
    fireEvent.change(screen.getByLabelText('Review by (optional)'), {
      target: { value: '2026-10-15' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send alert' }))
    expect(await screen.findByText(/Sent to Finance — Student Accounts/)).toBeTruthy()
    // The picker asked only for the people allowed to read this figure.
    expect(asked).toBe('/api/inbox/recipients?kind=finding&ref=M5')
    expect(body).toEqual({
      recipient_id: 9,
      note: 'Before Thursday, please.',
      review_by: '2026-10-15',
      source: { kind: 'finding', ref: 'M5' },
    })
  })
})

describe('the audit log', () => {
  it('says in words who sent, opened and reviewed an alert, never the note', () => {
    const ts = '2026-10-07T15:00:00+00:00'
    render(
      <AuditLog
        events={[
          {
            id: 1,
            ts,
            type: 'inbox.sent',
            actor: 'president@demo.test',
            payload: { message_id: 7, recipient_id: 9, recipient_role: 'finance', source_kind: 'finding', source_ref: 'M5', has_review_by: true },
          },
          { id: 2, ts, type: 'inbox.read', actor: 'finance@demo.test', payload: { message_id: 7, sender_id: 2 } },
          { id: 3, ts, type: 'inbox.reviewed', actor: 'finance@demo.test', payload: { message_id: 7, sender_id: 2 } },
        ]}
        readOnly
        onRefresh={() => undefined}
        deniedRequest={{ kind: 'idle' }}
        onShowDeniedRequest={() => undefined}
        viewerEmail="it@demo.test"
      />,
    )
    expect(
      screen.getByText(/sent an alert to Finance — Student Accounts about a briefing figure\./),
    ).toBeTruthy()
    expect(screen.getByText(/opened an alert\./)).toBeTruthy()
    expect(screen.getByText(/marked an alert reviewed\./)).toBeTruthy()
    expect(screen.queryByText(/recorded an entry/)).toBeNull()
  })
})

describe('IT accounts', () => {
  it('offers only the department and staff roles, and never shows a password', async () => {
    mockApi('it', {
      '/admin/users': (_url, init) =>
        init?.method === 'POST'
          ? json(
              {
                id: 30,
                email: 'bursar@demo.test',
                role: 'finance',
                one_time_password: null,
                password_issued_by_admin: true,
              },
              201,
            )
          : json([{ id: 9, email: 'it@demo.test', role: 'it', disabled: false, created_at: '' }]),
    })
    render(<AccountsPage role="it" currentUserEmail="it@demo.test" />)
    const role = (await screen.findByLabelText('Role')) as HTMLSelectElement
    expect([...role.options].map((option) => option.value)).toEqual([
      'finance',
      'registrar',
      'studentlife',
      'staff',
    ])
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'bursar@demo.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add the account' }))
    expect(await screen.findByText(/An administrator issues their first password/)).toBeTruthy()
    expect(document.querySelector('.password-value')).toBeNull()
  })
})
