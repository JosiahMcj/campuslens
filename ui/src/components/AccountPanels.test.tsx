import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { DataAccessPanel, SettingsPanel } from './AccountPanels'

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
  })

  it('shows a failed load with Retry, not the empty text', () => {
    const html = renderToStaticMarkup(
      <DataAccessPanel grants={null} error="Check your connection and try again." onRetry={() => {}} />,
    )
    expect(html).toContain('>Retry</button>')
    expect(html).not.toContain('Ask a question to see')
  })
})
