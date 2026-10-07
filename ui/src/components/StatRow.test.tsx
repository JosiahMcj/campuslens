import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { Finding, Findings } from '../api'
import { StatRow } from './StatRow'

function finding(id: string, title: string, display: string): Finding {
  return {
    id,
    title,
    value: 0,
    display,
    reason: null,
    comparison: null,
    source_fields: ['test.field'],
    row_ids: [],
    definition: 'test finding',
  }
}

const findings = {
  meta: { as_of: '2025-11-20', fixture: 'test', terms: {} },
  M1: finding('M1', 'Registered continuing students vs prior year', '−4.8 %'),
  M2: finding('M2', 'Continuing students not yet registered', '42'),
  M3: finding('M3', 'Unresolved financial holds under the threshold', '18'),
  M4: finding('M4', 'No advising appointment this term', '12'),
  M8: finding('M8', 'Students with one or more support indicators', '22'),
} as unknown as Findings

describe('StatRow', () => {
  const html = renderToStaticMarkup(
    <StatRow findings={findings} onOpenEvidence={() => {}} />,
  )

  it('renders one figure per headline measure, each a finding link', () => {
    expect(html.match(/stat-figure/g)?.length).toBe(5)
    // A screen reader hears which figure opens, by its label, never the code.
    expect(html.match(/open the evidence: /g)?.length).toBe(5)
    expect(html).not.toMatch(/\bM[1-9]\b/)
  })

  it("shows each finding's display string, in the house number style, and nothing else", () => {
    const displays = [...html.matchAll(/<span class="stat-display">([^<]*)<\/span>/g)].map(
      (match) => match[1],
    )
    expect(displays).toEqual(['−4.8%', '42', '18', '12', '22'])
  })

  it('never makes M9 a card, even when the findings carry it', () => {
    const withM9 = {
      ...findings,
      M9: finding('M9', 'Counseling contact', '7'),
    } as unknown as Findings
    const out = renderToStaticMarkup(<StatRow findings={withM9} onOpenEvidence={() => {}} />)
    expect(out.match(/stat-figure/g)?.length).toBe(5)
    expect(out).not.toContain('counseling')
  })

  it('shows the display labels beneath the numbers, not the backend titles', () => {
    const titles = [...html.matchAll(/<span class="stat-title">([^<]*)<\/span>/g)].map(
      (match) => match[1],
    )
    expect(titles).toEqual([
      'Spring registration vs. same point last year',
      'Continuing students not yet registered',
      'Not yet registered, with a hold under $1,000',
      'Not yet registered, no advising appointment this term',
      'Students with one or more support indicators',
    ])
  })
})
