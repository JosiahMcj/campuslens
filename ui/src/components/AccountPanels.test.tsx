// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it } from 'vitest'

import type { Session } from '../auth'
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
    const html = renderToStaticMarkup(<DataAccessPanel grants={grants} />)
    expect(html).toContain('Latest run explained: Not yet registered, with a hold under $1,000;')
    expect(html).toContain('<li>Hold amount</li>')
    expect(html).not.toContain('Findings: M3')
    expect(html).toMatch(/Technical detail(?:(?!<\/details>).)*holds\.amount/s)
    // The page intro comes from the page header (SidePanel), not the panel.
    expect(html).toMatch(/^<div class="account-panel"><h3 class="panel-subhead">AI employees/)
    expect(html).not.toMatch(/model/i)
  })

  it('gives each AI employee a one-line job, before and after a run', () => {
    for (const html of [
      renderToStaticMarkup(<DataAccessPanel grants={null} />),
      renderToStaticMarkup(<DataAccessPanel grants={grants} />),
    ]) {
      for (const name of ['Enrollment Analyst', 'Student Success Analyst', 'Chief of Staff']) {
        expect(html).toContain(name)
      }
      expect(html.match(/class="grant-job"/g)).toHaveLength(3)
      expect(html).toContain('Explains the registration figures')
      expect(html).toContain('holds, missing advising appointments and support indicators')
      expect(html).toContain('writes the summary and its limits')
    }
    expect(renderToStaticMarkup(<DataAccessPanel grants={null} />)).toContain(
      'Ask a question to see exactly what each employee was given.',
    )
  })

  it('says in the People table who may ask questions and who sees instructor names', () => {
    const html = renderToStaticMarkup(<DataAccessPanel grants={null} />)
    const ask = 'Ask any question about students, courses and majors (totals only)'
    const rows = html.match(/<tr><th scope="row">[^<]+<\/th><td>[^<]*<\/td><\/tr>/g) ?? []
    expect(rows).toHaveLength(5)
    for (const row of rows) {
      // Every role that can ask has the line; Financial Aid does not.
      expect(row.includes(ask)).toBe(!row.includes('Financial Aid review queue, with a status'))
    }
    expect(html).toContain('Instructor names are shown to the executive and admin only.')
    // The empty note sits directly above the cards, which keep their own gap.
    expect(html).toMatch(/class="panel-text state-empty">[^<]+<\/p><div class="grant-list">/)
  })

  it('shows a failed load with Retry, not the empty text', () => {
    const html = renderToStaticMarkup(
      <DataAccessPanel grants={null} error="Check your connection and try again." onRetry={() => {}} />,
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
