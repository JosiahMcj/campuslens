import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { Session } from '../auth'
import { DataAccessPanel, ProfilePanel, SettingsPanel } from './AccountPanels'

describe('SettingsPanel', () => {
  it('offers Light and Dark only, no System', () => {
    const html = renderToStaticMarkup(<SettingsPanel isAdmin={false} onOpenInstitution={() => {}} />)
    expect(html).toContain('>Light</button>')
    expect(html).toContain('>Dark</button>')
    expect(html).not.toContain('System')
  })
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
    expect(html).toContain('Explains: Not yet registered, with a hold under $1,000;')
    expect(html).toContain('<li>Hold amount</li>')
    expect(html).not.toContain('Findings: M3')
    expect(html).toMatch(/Technical detail(?:(?!<\/details>).)*holds\.amount/s)
    expect(html).toMatch(/^<div class="account-panel"><p class="panel-intro">/)
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
  })
})
