import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { Finding } from '../api'
import { EvidenceDrawer } from './EvidenceDrawer'

const M1: Finding = {
  id: 'M1',
  title: 'Spring registration vs. same point last year',
  value: -0.048,
  display: '−4.8 %',
  reason: null,
  comparison: { prior_year_registered_continuing: 125, prior_year_equivalent_date: '2025-11-20' },
  source_fields: ['enrollment.registration_status', 'profile.continuing'],
  definition: 'registered_continuing(as_of) / registered_continuing(prior_year_equivalent_date) − 1',
  row_ids: {
    numerator: Array.from({ length: 119 }, (_, index) => `STU-N${index}`),
    denominator: Array.from({ length: 125 }, (_, index) => `STU-D${index}`),
  },
} as Finding

describe('the evidence for M1', () => {
  const html = renderToStaticMarkup(<EvidenceDrawer finding={M1} fictional onClose={() => {}} />)

  it('shows the count registered now beside last year’s', () => {
    expect(html).toMatch(/<dt>Registered now<\/dt><dd>119<\/dd>/)
    expect(html).toMatch(/<dt>Registered by the same date last year<\/dt><dd>125<\/dd>/)
    // Now comes first, then the comparison.
    expect(html.indexOf('Registered now')).toBeLessThan(html.indexOf('Registered by the same date'))
  })

  it('names the records fold by who is in it, and splits the list the same way', () => {
    expect(html).toContain('<summary>All 244 continuing students</summary>')
    expect(html).not.toContain('Show the records')
    expect(html).toMatch(/<h3>Registered now \(119\)<\/h3>/)
    expect(html).toMatch(/<h3>Registered by the same date last year \(125\)<\/h3>/)
    expect(html).not.toContain('Counted now')
  })

  it('keeps the plain records fold for a figure without that comparison', () => {
    const m2 = {
      ...M1,
      id: 'M2',
      title: 'Continuing students not yet registered',
      value: 1,
      display: '1',
      comparison: {},
      row_ids: ['STU-0120'],
    } as Finding
    const other = renderToStaticMarkup(<EvidenceDrawer finding={m2} fictional onClose={() => {}} />)
    expect(other).toContain('Show the records (1)')
    expect(other).not.toContain('Registered now')
  })
})
