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
} as unknown as Findings

describe('StatRow', () => {
  const html = renderToStaticMarkup(
    <StatRow findings={findings} onOpenEvidence={() => {}} />,
  )

  it('renders one figure per headline measure, each a finding link', () => {
    expect(html.match(/stat-figure/g)?.length).toBe(4)
    for (const id of ['M1', 'M2', 'M3', 'M4']) {
      expect(html).toContain(`Open the evidence for finding ${id}`)
    }
  })

  it("shows each finding's display string verbatim and nothing else", () => {
    const displays = [...html.matchAll(/<span class="stat-display">([^<]*)<\/span>/g)].map(
      (match) => match[1],
    )
    expect(displays).toEqual(['−4.8 %', '42', '18', '12'])
  })

  it('shows the finding titles beneath the numbers', () => {
    const titles = [...html.matchAll(/<span class="stat-title">([^<]*)<\/span>/g)].map(
      (match) => match[1],
    )
    expect(titles).toEqual([
      'Registered continuing students vs prior year',
      'Continuing students not yet registered',
      'Unresolved financial holds under the threshold',
      'No advising appointment this term',
    ])
  })
})
