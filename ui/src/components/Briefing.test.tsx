import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { Decision, Finding, Findings } from '../api'
import type { ModelSection } from '../states'
import { BriefingSections, DecisionSection, EvidenceSources, Limitations } from './Briefing'

function finding(id: string, display: string, extra: Partial<Finding> = {}): Finding {
  return {
    id,
    title: `Backend title ${id}`,
    value: 0,
    display,
    reason: null,
    comparison: null,
    source_fields: ['enrollment.registration_status'],
    row_ids: [],
    definition: 'x',
    ...extra,
  }
}

const FINDINGS = {
  meta: { as_of: '2026-11-20', fixture: 'test', terms: {} },
  M1: finding('M1', '−4.8 %', {
    comparison: { prior_year_registered_continuing: 125, prior_year_equivalent_date: '2025-11-20' },
  }),
  M2: finding('M2', '42'),
  M3: finding('M3', '18', { comparison: { threshold_usd: 1000 } }),
  M4: finding('M4', '12'),
  M5: { ...finding('M5', '28 unresolved holds'), value: [{ office: 'Bursar', count: 24, hold_row_ids: [] }] },
  M6: finding('M6', '28'),
  M7: finding('M7', '−2.7 %', {
    comparison: { prior_year_registered_credit_hours: 1872, prior_year_equivalent_date: '2025-11-20' },
  }),
  M8: finding('M8', '22'),
} as unknown as Findings

const PROVENANCE = { source: 'chief', provider: 'chat', model_label: 'live model', recorded: true }

const CHIEF: ModelSection = {
  kind: 'available',
  text: 'raw [M1]',
  provenance: PROVENANCE,
  claims: [
    {
      text: 'Spring registration among continuing students is down 4.8% versus the same point last year',
      finding_ids: ['M1'],
    },
  ],
}

function full(onOpenStaffActions?: () => void): string {
  return renderToStaticMarkup(
    <BriefingSections
      findings={FINDINGS}
      fictional
      enrollment={CHIEF}
      studentSuccess={null}
      chiefSummary={CHIEF}
      onCheckAgain={null}
      onOpenEvidence={() => {}}
      onOpenStaffActions={onOpenStaffActions}
    />,
  )
}

describe('the written explanations, unavailable', () => {
  it('says "Written explanation unavailable", never "Model"', () => {
    const html = renderToStaticMarkup(
      <BriefingSections
        findings={FINDINGS}
        fictional
        enrollment={null}
        studentSuccess={{ kind: 'unavailable', reason: 'timed out' }}
        chiefSummary={{ kind: 'unavailable', reason: 'timed out' }}
        onCheckAgain={() => {}}
        onOpenEvidence={() => {}}
      />,
    )
    expect(html.split('<h3>Written explanation unavailable</h3>')).toHaveLength(3)
    // The words on screen (tags and class names aside) never say "model".
    expect(html.replace(/<[^>]*>/g, ' ')).not.toMatch(/model/i)
  })
})

describe('the Evidence page', () => {
  it('keeps section 4\'s opening sentence in the briefing, and leaves the page intro to the header', () => {
    const page = renderToStaticMarkup(
      <EvidenceSources findings={FINDINGS} fictional onOpenEvidence={() => {}} headingId={null} />,
    )
    expect(page).not.toContain('Every number in this briefing')
    expect(full()).toContain('Every number in this briefing')
  })
})

describe('the full briefing', () => {
  it('never shows a bracketed finding code; the number itself is the link', () => {
    const html = full()
    expect(html).not.toMatch(/\[M\d\]/)
    expect(html).toMatch(/<button[^>]*class="finding-link"[^>]*><span class="num">4\.8%<\/span>/)
  })

  it('names the writer in one line and moves the replay fact into "About this answer"', () => {
    const html = full()
    expect(html).toContain('>Written by the Chief of Staff</p>')
    expect(html).not.toContain('recorded live run')
    expect(html).toMatch(
      /About this answer(?:(?!<\/details>).)*earlier live run(?:(?!<\/details>).)*checked against the data/s,
    )
  })

  it('has one "About this answer" for the whole panel, however many sections a model wrote', () => {
    const html = full()
    expect(html.split('About this answer')).toHaveLength(2)
    // It closes section 1 (who wrote the briefing), never between sections.
    expect(html.indexOf('About this answer')).toBeGreaterThan(html.indexOf('id="s-summary"'))
    expect(html.indexOf('About this answer')).toBeLessThan(html.indexOf('id="s-measure"'))
  })

  it('section 3 links each figure once: no stray Evidence link, no linked caption', () => {
    const groups: ModelSection = {
      kind: 'available',
      text: 'raw',
      provenance: PROVENANCE,
      claims: [
        { text: 'The Bursar is responsible for 24 of the 28 unresolved holds.', finding_ids: ['M5'] },
        { text: 'The remaining offices hold far fewer: Library 1, Registrar 2.', finding_ids: ['M5'] },
      ],
    }
    const html = renderToStaticMarkup(
      <BriefingSections
        findings={FINDINGS}
        fictional
        enrollment={null}
        studentSuccess={groups}
        chiefSummary={null}
        onCheckAgain={null}
        onOpenEvidence={() => {}}
      />,
    )
    const s3 = html.slice(html.indexOf('id="s-groups"'), html.indexOf('id="s-evidence"'))
    expect(s3).toMatch(/<span class="num">28 unresolved holds<\/span>/)
    expect(s3).not.toContain('>Evidence<')
    expect(s3).not.toContain('Unresolved holds by office: ')
    expect(s3).toContain('<caption class="visually-hidden">Unresolved holds by office</caption>')
    expect(s3.split('finding-link').length - 1).toBe(1)
  })

  it('section 2 adds the comparison table and folds the analyst text', () => {
    const html = full()
    const s2 = html.slice(html.indexOf('id="s-measure"'), html.indexOf('id="s-groups"'))
    expect(s2).toContain('125 students (Nov 20, 2025)')
    expect(s2).toContain('1,872 credit hours (Nov 20, 2025)')
    // The server's "−4.8 %" is drawn in the house style, without the space.
    expect(s2).toContain('<span class="num">−4.8%</span>')
    expect(s2).not.toContain('4.8 %')
    expect(s2).toMatch(/<details[^>]*>.*Show the Enrollment Analyst&#x27;s explanation/s)
  })

  it('section 4 lists figures by display label and plain field names', () => {
    const html = full()
    const s4 = html.slice(html.indexOf('id="s-evidence"'))
    expect(s4).toContain('Not yet registered, with a hold under $1,000')
    expect(s4).toContain('Reads: Registration status')
    expect(s4).not.toContain('enrollment.registration_status')
    expect(s4).not.toContain('Backend title')
  })

  it('section 5 links to Staff actions instead of repeating them', () => {
    const linked = full(() => {})
    const s5 = linked.slice(linked.indexOf('id="s-actions"'))
    expect(s5).toContain('Open Staff actions')
    expect(s5).not.toContain('Bursar')
    expect(full()).toContain('They are listed under Staff actions in the sidebar.')
  })
})

describe('sections 6 and 7', () => {
  const decision: Decision = {
    id: 'D-1',
    title: 'Emergency-aid review',
    text: 'Authorize the review.',
    follow_up: { office: 'Financial Aid', description: 'x' },
    approved: false,
  }

  it('section 6 shows the decision title, its status, and one way to it', () => {
    const waiting = renderToStaticMarkup(
      <DecisionSection decisions={[decision]} onOpenDecision={() => {}} />,
    )
    expect(waiting).toContain('Emergency-aid review')
    expect(waiting).toContain('Waiting for leadership approval.')
    expect(waiting).toContain('Open the decision')
    expect(waiting).not.toContain('in the conversation')
    const approved = renderToStaticMarkup(
      <DecisionSection decisions={[{ ...decision, approved: true }]} onOpenDecision={() => {}} />,
    )
    expect(approved).toContain('Approved.')
  })

  it('section 7 is folded and names figures by label', () => {
    const html = renderToStaticMarkup(
      <Limitations findings={FINDINGS} fictional chiefLimitations={null} onOpenEvidence={() => {}} />,
    )
    expect(html).toMatch(/<details[^>]*>.*Show the known limitations/s)
    expect(html).toContain('Registered credit hours vs. last year is an optional measure.')
    expect(html).not.toMatch(/\bM7\b/)
  })
})
