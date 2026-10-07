// M9, the authorized counseling count, where the executive sees it: section
// 3 of the briefing (only when the findings carry it, with the authorization
// as its source label and a withheld count said plainly), the evidence
// drawer (the authorization record, the fields read, and no rows), and the
// stat rail, which never gains a sixth figure for it.

import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { Finding, Findings } from '../api'
import { authorizationFrom, authorizedSourceLabel, suppressionNote } from '../counseling'
import { cabinetBriefingFrom } from '../states'
import { BriefingSections } from './Briefing'
import { EvidenceDrawer } from './EvidenceDrawer'
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

const AUTHORIZATION = {
  authorized_by: 'Dr. Example, Director of Counseling',
  document_reference: 'memo 2026-09-26',
  recorded_by: 'admin@example.edu',
  recorded_at: '2026-09-26T15:00:00+00:00',
}

function m9(suppressed: boolean): Finding {
  return {
    id: 'M9',
    title: 'Students in M2 with any counseling contact this term',
    value: suppressed ? null : 12,
    display: suppressed ? 'fewer than 10' : '12',
    reason: suppressed
      ? 'the count is withheld below the minimum group size of 10 so that no student can be identified'
      : null,
    comparison: null,
    source_fields: [
      'profile.continuing',
      'enrollment.registration_status',
      'counseling.counseling_notes',
      'counseling.chaplain_contact',
    ],
    row_ids: [],
    definition: 'count(row ∈ M2 ∧ counseling contact)',
    suppressed,
    minimum_cell_size: 10,
    aggregate_only: true,
    authorization: AUTHORIZATION,
  }
}

function findings(extra: Record<string, Finding> = {}): Findings {
  return {
    meta: { as_of: '2026-11-20', fixture: 'test', terms: {} },
    M1: finding('M1', 'Spring registration vs. same point last year', '−4.8 %'),
    M2: finding('M2', 'Continuing students not yet registered', '42'),
    M3: finding('M3', 'Of M2, financial hold under $1,000', '18'),
    M4: finding('M4', 'Of M2, no advising appointment this term', '12'),
    M5: { ...finding('M5', 'Unresolved holds by office', '28 unresolved holds'), value: [] },
    M6: finding('M6', 'Days until registration closes', '28'),
    M7: finding('M7', 'Registered credit hours vs. prior year', '−2.7 %'),
    M8: finding('M8', 'Students with one or more support indicators', '22'),
    ...extra,
  } as unknown as Findings
}

function briefing(data: Findings, counselingFigure: Finding | null = null): string {
  return renderToStaticMarkup(
    <BriefingSections
      findings={data}
      fictional
      enrollment={null}
      studentSuccess={null}
      chiefSummary={null}
      onCheckAgain={null}
      onOpenEvidence={() => {}}
      counselingFigure={counselingFigure}
    />,
  )
}

describe('M9 in briefing section 3', () => {
  it('is absent when the briefing does not carry it', () => {
    const html = briefing(findings())
    expect(html).not.toContain('counseling')
    expect(html).not.toContain('open the evidence: Students not yet registered who have had counseling contact (aggregate)')
  })

  it('never borrows the current findings: a Q2 or older briefing shows no M9', () => {
    // The authorization is on now (the current findings carry M9), but the
    // briefing on screen was not produced with it.
    const html = briefing(findings({ M9: m9(true) }), null)
    expect(html).not.toContain('counseling')
    expect(html).not.toContain('open the evidence: Students not yet registered who have had counseling contact (aggregate)')
  })

  it('says a withheld count plainly, with the authorization as its source', () => {
    const html = briefing(findings(), m9(true))
    const section3 = html.slice(html.indexOf('id="s-groups"'), html.indexOf('id="s-evidence"'))
    expect(section3).toContain(
      '>Aggregate, authorized by Dr. Example, Director of Counseling</p>',
    )
    expect(section3).toContain('open the evidence: Students not yet registered who have had counseling contact (aggregate)')
    expect(section3).toContain('fewer than 10')
    expect(section3).toContain('The count is withheld below 10 so no one can be identified.')
  })

  it('shows the number when it is at or above the minimum group size', () => {
    const html = briefing(findings(), m9(false))
    expect(html).toContain('open the evidence: Students not yet registered who have had counseling contact (aggregate)')
    expect(html).toMatch(/<span class="num">12<\/span>/)
    // The M9 line says "Of those students" style plain words, no code.
    expect(html).not.toMatch(/\bM9\b/)
    expect(html).not.toContain('withheld')
  })

  it('lists M9 in section 4 as an authorized aggregate with no rows', () => {
    const html = briefing(findings(), m9(true))
    const section4 = html.slice(html.indexOf('id="s-evidence"'), html.indexOf('5. Operational'))
    expect(section4).toContain(
      'Students not yet registered who have had counseling contact (aggregate)',
    )
    expect(section4).toContain(
      'Aggregate, authorized by Dr. Example, Director of Counseling. No list of students is shown for this figure.',
    )
    expect(section4).not.toMatch(/\bM[1-9]\b/)
    expect(section4).toContain('fewer than 10')
    expect(section4).not.toContain('STU-')
    // Without the briefing's copy, section 4 stays at M1 to M8.
    const without = briefing(findings({ M9: m9(true) }), null)
    expect(without.slice(without.indexOf('id="s-evidence"'))).not.toContain('counseling contact')
  })

  it('never adds M9 to the stat rail', () => {
    const data = findings({ M9: m9(false) })
    const rail = renderToStaticMarkup(<StatRow findings={data} onOpenEvidence={() => {}} />)
    expect(rail.match(/stat-figure/g)?.length).toBe(5)
    expect(rail).not.toContain('M9')
  })
})

describe('M9 in the evidence drawer', () => {
  it('shows the authorization, the fields read, and no rows', () => {
    const html = renderToStaticMarkup(
      <EvidenceDrawer finding={m9(true)} fictional onClose={() => {}} />,
    )
    expect(html).toContain('Fewer than 10 students')
    expect(html).toContain('The count is withheld below 10 so no one can be identified.')
    // The panel is titled with the display label, never the code.
    expect(html).toContain(
      '<h1>Students not yet registered who have had counseling contact (aggregate)</h1>',
    )
    expect(html).toContain('<dt>Authorized by</dt><dd>Dr. Example, Director of Counseling</dd>')
    expect(html).toContain('<dt>Document</dt><dd>memo 2026-09-26</dd>')
    expect(html).toContain('<dt>Recorded by</dt><dd>admin@example.edu')
    // Plain field names under "How it is computed"; raw names only in the
    // Technical detail fold inside it.
    expect(html).toContain('<li>Chaplain contact</li>')
    expect(html).toMatch(/Technical detail(?:(?!<\/details>).)*counseling\.chaplain_contact/s)
    expect(html).toContain('No records are shown for this figure.')
    expect(html).not.toContain('Show the records')
    expect(html).not.toContain('row-list')
    expect(html).not.toContain('STU-')
    expect(html).not.toContain('data/VERIFY.md lists them')
  })

  it('keeps the row list for every other finding', () => {
    const m2 = { ...finding('M2', 'Continuing students not yet registered', '42'), row_ids: ['STU-0120'] }
    const html = renderToStaticMarkup(<EvidenceDrawer finding={m2} fictional onClose={() => {}} />)
    expect(html).toMatch(/Show the records \(1\)(?:(?!<\/details>).)*STU-0120/s)
    expect(html).not.toContain('No records are shown for this figure.')
    expect(html).not.toContain('Authorized by')
    // Plain definition first; no set notation outside the Technical detail.
    expect(html).toContain('Students who are eligible to continue but have not registered')
    expect(html).not.toContain('data/VERIFY.md')
  })
})

describe('counseling helpers', () => {
  it('reads the route body, treating anything malformed as off', () => {
    expect(authorizationFrom(null)).toEqual({
      authorized: false,
      authorizedBy: null,
      documentReference: null,
      recordedBy: null,
      recordedAt: null,
    })
    expect(
      authorizationFrom({ authorized: true, ...AUTHORIZATION }).authorizedBy,
    ).toBe('Dr. Example, Director of Counseling')
    expect(authorizationFrom({ authorized: 'yes' }).authorized).toBe(false)
  })

  it('words the suppression note and the source label from the finding', () => {
    expect(suppressionNote({ suppressed: false, minimum_cell_size: 10 })).toBeNull()
    expect(suppressionNote({ suppressed: true, minimum_cell_size: 5 })).toBe(
      'The count is withheld below 5 so no one can be identified.',
    )
    expect(authorizedSourceLabel({ authorization: AUTHORIZATION })).toBe(
      'aggregate, authorized by Dr. Example, Director of Counseling',
    )
    expect(authorizedSourceLabel({})).toBe('aggregate, authorized')
  })
})

describe('the briefing keeps its own M9', () => {
  const base = {
    question_id: 'spring-registration',
    question: 'What should I know about spring registration?',
    question_event_id: 7,
    sections: {},
  }

  it('parses the stored aggregate, with no rows whatever the payload says', () => {
    const parsed = cabinetBriefingFrom({
      ...base,
      aggregates: { M9: { ...m9(true), row_ids: ['STU-0126'] } },
    })
    const figure = parsed?.aggregates.M9
    expect(figure?.display).toBe('fewer than 10')
    expect(figure?.row_ids).toEqual([])
    expect(figure?.authorization?.authorized_by).toBe('Dr. Example, Director of Counseling')
  })

  it('carries nothing when the briefing has no aggregates or a malformed one', () => {
    expect(cabinetBriefingFrom(base)?.aggregates).toEqual({})
    const notAggregate = { ...m9(true), aggregate_only: false }
    expect(cabinetBriefingFrom({ ...base, aggregates: { M9: notAggregate } })?.aggregates).toEqual(
      {},
    )
  })
})
