import { describe, expect, it } from 'vitest'

import { FINDING_LABELS, findingLabel, findingList, linkClaimNumbers } from './findingLabels'

/** The links of a claim as "text->id" for compact assertions. */
function links(text: string, citations: { id: string; display: string }[]) {
  const { parts, unmatched } = linkClaimNumbers(text, citations)
  return {
    links: parts.flatMap((part) => (part.kind === 'link' ? [`${part.text}->${part.findingId}`] : [])),
    joined: parts.map((part) => part.text).join(''),
    unmatched,
  }
}

describe('finding labels', () => {
  it('has the COMMON.md display label for every figure, M1 to M9', () => {
    expect(Object.keys(FINDING_LABELS)).toEqual(['M1', 'M2', 'M3', 'M4', 'M5', 'M6', 'M7', 'M8', 'M9'])
    expect(findingLabel('M3')).toBe('Not yet registered, with a hold under $1,000')
    expect(findingLabel('M9')).toBe(
      'Students not yet registered who have had counseling contact (aggregate)',
    )
  })

  it('falls back to the backend title, then to plain words, never the code', () => {
    expect(findingLabel('M42', 'A new figure')).toBe('A new figure')
    expect(findingLabel('M42')).toBe('This figure')
  })

  it('joins labels that contain commas with semicolons', () => {
    expect(findingList(['M2', 'M3'])).toBe(
      'Continuing students not yet registered and Not yet registered, with a hold under $1,000',
    )
  })
})

// The claim strings below are verbatim from the recorded golden run.
describe('the number in a claim is its evidence link', () => {
  it('matches "4.8%" in the text to the display "−4.8 %"', () => {
    const result = links(
      'Spring registration among continuing students is down 4.8% versus the same point last year, when 125 had registered as of 2025-11-20',
      [{ id: 'M1', display: '−4.8 %' }],
    )
    expect(result.links).toEqual(['4.8%->M1'])
    expect(result.unmatched).toEqual([])
  })

  it('links two figures in one sentence', () => {
    const result = links(
      'Among them, 18 have an unresolved financial hold under $1,000 and 12 have had no advising appointment this term',
      [
        { id: 'M3', display: '18' },
        { id: 'M4', display: '12' },
      ],
    )
    expect(result.links).toEqual(['18->M3', '12->M4'])
  })

  it('matches a whole display phrase first, so two 28s go to the right figures', () => {
    const text =
      'Registration closes in 28 days, and the Bursar is responsible for 24 of the 28 unresolved holds across all current-term students'
    const result = links(text, [
      { id: 'M5', display: '28 unresolved holds' },
      { id: 'M6', display: '28' },
    ])
    expect(result.links).toEqual(['28->M6', '28 unresolved holds->M5'])
    expect(result.joined).toBe(text)
  })

  it('reports a cited figure with no number in the sentence as unmatched', () => {
    const result = links('All records are fictional demonstration data', [
      { id: 'M1', display: '−4.8 %' },
    ])
    expect(result.links).toEqual([])
    expect(result.unmatched).toEqual(['M1'])
  })

  it('never links a missing value', () => {
    expect(links('There are -- students', [{ id: 'M2', display: '--' }]).unmatched).toEqual(['M2'])
  })
})
