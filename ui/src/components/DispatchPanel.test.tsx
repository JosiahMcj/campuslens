import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { AuditEvent } from '../api'
import { DispatchPanel } from './DispatchPanel'

function event(id: number, type: string, actor: string, payload: Record<string, unknown>): AuditEvent {
  return { id, ts: '2026-10-05T12:00:00+00:00', type, actor, payload }
}

const RUN: AuditEvent[] = [
  event(1, 'question.asked', 'executive', { question: 'Q' }),
  event(2, 'task.assigned', 'chief_of_staff', {
    task_id: 'task-enrollment_analyst-1',
    role: 'enrollment_analyst',
    fields: ['enrollment.registration_status', 'holds.amount'],
    findings: ['M1', 'M7'],
  }),
  event(3, 'data.granted', 'enrollment_analyst', {
    task_id: 'task-enrollment_analyst-1',
    granted_fields: ['enrollment.registration_status', 'holds.amount'],
  }),
  event(4, 'finding.produced', 'enrollment_analyst', {
    task_id: 'task-enrollment_analyst-1',
    findings: ['M1', 'M7'],
  }),
]

function panel(props: Partial<Parameters<typeof DispatchPanel>[0]> = {}): string {
  return renderToStaticMarkup(
    <DispatchPanel events={RUN} minEventId={0} inFlight={false} stillWorking={false} {...props} />,
  )
}

describe('DispatchPanel', () => {
  it('shows each AI employee plainly: finished time, figures by label, fields by name', () => {
    const html = panel()
    expect(html).toContain('Enrollment Analyst')
    // "Finished 10:38 PM": the time of day, no date, seconds or zone code.
    expect(html).toMatch(/Finished \d{1,2}:\d{2} (AM|PM)</)
    expect(html).not.toContain('Finished at')
    expect(html).toContain('class="panel-intro"')
    expect(html).toContain('>Done<')
    expect(html).toContain('Explains: Spring registration vs. same point last year; Registered credit hours vs. last year')
    expect(html).toContain('<li>Registration status</li>')
    expect(html).toContain('<li>Hold amount</li>')
    expect(html).not.toContain('task-enrollment_analyst-1')
    expect(html).not.toContain('Findings: M1')
    // Raw names only inside the Technical detail fold.
    expect(html).toMatch(/Technical detail(?:(?!<\/details>).)*holds\.amount/s)
  })

  it('says what to do before any question, instead of rendering nothing', () => {
    expect(panel({ events: [] })).toContain('Ask an approved question')
  })

  it('shows a failed load with Retry', () => {
    const html = panel({ events: [], error: 'Check your connection and try again.', onRetry: () => {} })
    expect(html).toContain('Couldn&#x27;t load the AI employees&#x27; work.')
    expect(html).toContain('>Retry</button>')
  })
})
