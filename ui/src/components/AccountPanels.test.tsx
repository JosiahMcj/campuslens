// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it } from 'vitest'

import type { Session } from '../auth'
import type { StaffEmployee } from '../staff'
import { DataAccessPanel, ProfilePanel, SettingsPanel } from './AccountPanels'

describe('SettingsPanel', () => {
  it('offers Light and Dark only, no System', () => {
    const html = renderToStaticMarkup(<SettingsPanel isAdmin={false} onOpenInstitution={() => {}} />)
    expect(html).toContain('>Light</button>')
    expect(html).toContain('>Dark</button>')
    expect(html).not.toContain('System')
  })

  it('moves the choice with the arrow keys, one Tab stop per group', () => {
    render(<SettingsPanel isAdmin={false} onOpenInstitution={() => {}} />)
    const group = screen.getByRole('radiogroup', { name: 'Motion' })
    const full = screen.getByRole('radio', { name: 'Full' })
    const reduced = screen.getByRole('radio', { name: 'Reduced' })
    const chosen = full.getAttribute('aria-checked') === 'true' ? full : reduced
    const other = chosen === full ? reduced : full
    expect(chosen.tabIndex).toBe(0)
    expect(other.tabIndex).toBe(-1)
    chosen.focus()
    fireEvent.keyDown(group, { key: 'ArrowRight' })
    expect(other.getAttribute('aria-checked')).toBe('true')
    expect(other.tabIndex).toBe(0)
    expect(document.activeElement).toBe(other)
    fireEvent.keyDown(group, { key: 'ArrowLeft' })
    expect(chosen.getAttribute('aria-checked')).toBe('true')
    expect(document.activeElement).toBe(chosen)
  })
})

afterEach(() => {
  cleanup()
})

const employee = (overrides: Partial<StaffEmployee>): StaffEmployee => ({
  role: 'registrar_analyst',
  title: 'Registrar Analyst',
  job: 'Reports registration, academic standing, credit hours and course sections.',
  office: 'Office of the Registrar',
  may_read: ['Majors, colleges, class levels and terms', 'Academic standing (probation and suspension)'],
  never_reads: ['Student names', 'Counseling and chaplain notes', 'Free-text notes', "One student's record"],
  outside_scope: ['Pell status', 'Holds and balances owed, by office'],
  findings: ['M1'],
  no_data: false,
  yours: false,
  requests_today: 0,
  ...overrides,
})

const STAFF: StaffEmployee[] = [
  employee({
    role: 'student_accounts_analyst',
    title: 'Student Accounts Analyst',
    job: 'Reports account holds and balances owed, by office.',
    office: 'Finance — Student Accounts (Bursar)',
    may_read: ['Majors, colleges, class levels and terms', 'Holds and balances owed, by office'],
    outside_scope: ['Pell status', 'Advising appointments'],
    yours: true,
    requests_today: 3,
  }),
  employee({
    role: 'student_success_analyst',
    title: 'Student Success Analyst',
    job: 'Explains what stands in students’ way: holds, missing advising appointments, stop-outs and support indicators.',
    office: 'Student Success',
    requests_today: 1,
  }),
  employee({}),
  employee({
    role: 'advancement_analyst',
    title: 'Advancement Analyst',
    job: 'Will report giving and alumni engagement once that data is connected.',
    office: 'Advancement',
    may_read: [],
    never_reads: ['Any student record', 'Counseling and chaplain notes', 'Free-text notes', "One student's record"],
    outside_scope: [],
    no_data: true,
  }),
]

describe('DataAccessPanel', () => {
  const grants = [
    {
      role: 'student_success_analyst',
      fields: ['holds.amount', 'enrollment.registration_status'],
      findings: ['M3', 'M4'],
      aggregate: false,
    },
  ]

  it('names figures and fields plainly, raw names folded', () => {
    const html = renderToStaticMarkup(<DataAccessPanel staff={STAFF} grants={grants} />)
    expect(html).toContain('Latest run explained: Not yet registered, with a hold under $1,000;')
    expect(html).toContain('<li>Hold amount</li>')
    expect(html).not.toContain('Findings: M3')
    expect(html).toMatch(/Technical detail(?:(?!<\/details>).)*holds\.amount/s)
    // The page intro comes from the page header (SidePanel), not the panel.
    expect(html).toMatch(/^<div class="account-panel"><h3 class="panel-subhead">AI employees/)
    expect(html).not.toMatch(/model/i)
  })

  it('shows every employee as a card: title, office, job, scope, never-reads and today', () => {
    render(<DataAccessPanel staff={STAFF} grants={null} />)
    const cards = screen.getAllByRole('article')
    expect(cards.map((card) => card.getAttribute('aria-label'))).toEqual([
      'Student Accounts Analyst',
      'Student Success Analyst',
      'Registrar Analyst',
      'Advancement Analyst',
    ])
    const own = within(cards[0])
    expect(own.getByText('Your department')).toBeTruthy()
    expect(own.getByText('Serves Finance — Student Accounts (Bursar)')).toBeTruthy()
    expect(own.getByText('Reports account holds and balances owed, by office.')).toBeTruthy()
    expect(own.getByText('May read, as totals')).toBeTruthy()
    expect(own.getByText(/Holds and balances owed, by office$/)).toBeTruthy()
    expect(own.getByText('Never reads')).toBeTruthy()
    expect(own.getByText(/^Student names; Counseling and chaplain notes/)).toBeTruthy()
    expect(own.getByText('Handled 3 requests today')).toBeTruthy()
    expect(own.getByText('Outside its job (2)')).toBeTruthy()
    expect(within(cards[1]).getByText('Handled 1 request today')).toBeTruthy()
    expect(within(cards[2]).getByText('No requests today')).toBeTruthy()
    expect(within(cards[2]).queryByText('Your department')).toBeNull()
    const none = within(cards[3])
    expect(none.getByText('No data connected yet')).toBeTruthy()
    expect(none.getByText('Nothing yet: no data is connected for this office.')).toBeTruthy()
    expect(none.queryByText(/Outside its job/)).toBeNull()
    expect(screen.getByText('Ask a question to see exactly what each employee was given.')).toBeTruthy()
  })

  it('leaves out the "ask a question" note for roles without briefing runs', () => {
    const html = renderToStaticMarkup(<DataAccessPanel staff={STAFF} />)
    expect(html).not.toContain('Ask a question to see')
    expect(html.match(/class="grant-job"/g)).toHaveLength(4)
  })

  it('says it is loading, then offers Retry when the employees cannot be loaded', () => {
    expect(renderToStaticMarkup(<DataAccessPanel staff={null} />)).toContain('Loading the AI employees…')
    const failed = renderToStaticMarkup(
      <DataAccessPanel staff={null} staffError="Check your connection and try again." onRetryStaff={() => {}} />,
    )
    expect(failed).toContain('Couldn&#x27;t load the AI employees. Check your connection and try again.')
    expect(failed).toContain('>Retry</button>')
    expect(failed).not.toContain('Loading the AI employees')
  })

  it('says in the People table who may ask questions and who sees instructor names', () => {
    const html = renderToStaticMarkup(<DataAccessPanel staff={STAFF} grants={null} />)
    const ask = 'Ask any question about students, courses and majors (totals only)'
    const rows = html.match(/<tr><th scope="row">[^<]+<\/th><td>[^<]*<\/td><\/tr>/g) ?? []
    expect(rows).toHaveLength(9)
    for (const row of rows) {
      // Every role that can ask has the line; Financial Aid and IT do not.
      const cannotAsk =
        row.includes('Financial Aid review queue, with a status') ||
        row.includes('Manage the department and staff accounts')
      expect(row.includes(ask)).toBe(!cannotAsk)
    }
    expect(html).toContain('Instructor names are shown to the executive and admin only.')
    // The empty note sits directly above the cards, which keep their own gap.
    expect(html).toMatch(/class="panel-text state-empty">[^<]+<\/p><div class="grant-list">/)
  })

  it('shows a failed load with Retry, not the empty text', () => {
    const html = renderToStaticMarkup(
      <DataAccessPanel
        staff={STAFF}
        grants={null}
        error="Check your connection and try again."
        onRetry={() => {}}
      />,
    )
    expect(html).toContain('>Retry</button>')
    expect(html).not.toContain('Ask a question to see')
  })
})

describe('ProfilePanel', () => {
  it('says how to get access and when the session ends, in two plain lines', () => {
    const session = {
      user: { id: 1, email: 'president@demo.test', role: 'executive', institution_id: 1, institution: null },
      csrfToken: 'x',
    } as Session
    const html = renderToStaticMarkup(
      <ProfilePanel session={session} datasetName="Demonstration data" onSignOut={() => {}} />,
    )
    expect(html).toContain('Need access? Ask your administrator.')
    expect(html).toContain('You are signed out after 12 hours.')
    expect(html).not.toContain('by default')
    // Signing out loses nothing: a secondary button, never the red one.
    expect(html).toMatch(/<button type="button" class="btn-secondary[^"]*">Sign out<\/button>/)
  })
})
