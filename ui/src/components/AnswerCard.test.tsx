// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DispatchInfo } from '../api'
import { answerActions, chartModel, TEMPLATE_IDS } from '../answerCard'
import { SAMPLE_CHARTS } from '../chartSamples'
import { exploreResponseFrom, SUPPRESSED, type ExploreResponse } from '../explore'
import FIXTURES from './answerCard.fixture.json'
import { ChartGallery } from './ChartGallery'
import { DecisionPanel } from './DecisionPanel'
import { ExploreAnswer, type AnswerActionProps } from './ExploreAnswer'

// Recorded from the real code path (rule planner, template writer, card.py)
// over the full-scale demonstration school.
type Name = keyof typeof FIXTURES
const response = (name: Name): ExploreResponse => exploreResponseFrom(FIXTURES[name].response)

afterEach(() => cleanup())

function show(name: Name, actions: AnswerActionProps | null, onAsk = vi.fn()) {
  render(
    <ExploreAnswer
      answerKey={name}
      response={response(name)}
      fallbackSuggestions={[]}
      onAsk={onAsk}
      busy={false}
      onSeeAuditLog={null}
      actions={actions}
    />,
  )
  return onAsk
}

describe('chart template per result shape', () => {
  it('a ranking is a sorted bar chart with the whole as a reference', () => {
    const model = chartModel(response('ranking'))
    expect(model?.template).toBe('ranking_bar')
    if (model?.template !== 'ranking_bar') return
    const values = model.bars.map((bar) => bar.value ?? -1)
    expect(values).toEqual([...values].sort((a, b) => b - a))
    expect(model.reference?.label).toBe('All students')
    expect(model.bars.some((bar) => bar.label === 'All students')).toBe(false)
  })

  it('a by-term answer is a trend line with its change', () => {
    const model = chartModel(response('trend'))
    expect(model?.template).toBe('trend_line')
    if (model?.template !== 'trend_line') return
    expect(model.data.series).toHaveLength(1)
    expect(model.delta?.since).toBe('Spring 2025')
  })

  it('two groupings are grouped bars, one series per second group', () => {
    const model = chartModel(response('grouped'))
    expect(model?.template).toBe('grouped_bars')
    if (model?.template !== 'grouped_bars') return
    expect(model.data.series.map((s) => s.label)).toContain('Freshmen')
  })

  it('a single figure is a key figure with the change since a year earlier', () => {
    const model = chartModel(response('number'))
    expect(model?.template).toBe('kpi_number')
    if (model?.template !== 'kpi_number') return
    expect(model.value.value).toBeTypeOf('number')
    expect(model.delta?.since).toBe('Spring 2025')
  })

  it('a registration change is a before-and-after', () => {
    expect(chartModel(response('registration'))?.template).toBe('before_after')
  })

  it('an unknown template draws no chart', () => {
    const raw = structuredClone(FIXTURES.ranking.response) as Record<string, unknown>
    ;(raw.card as { chart: { template: string } }).chart.template = 'pie_from_a_model'
    expect(chartModel(exploreResponseFrom(raw))).toBeNull()
  })

  it('a withheld cell is a gap, never a zero', () => {
    const raw = structuredClone(FIXTURES.ranking.response) as {
      steps: { table: { rows: unknown[][] } }[]
    }
    raw.steps[0].table.rows[1][raw.steps[0].table.rows[1].length - 1] = SUPPRESSED
    const model = chartModel(exploreResponseFrom(raw))
    if (model?.template !== 'ranking_bar') throw new Error('expected a ranking')
    const withheld = model.bars.filter((bar) => bar.withheld)
    expect(withheld).toHaveLength(1)
    expect(withheld[0].value).toBeNull()
  })

  it('the gallery draws every template with sample data', () => {
    const html = renderToStaticMarkup(<ChartGallery />)
    for (const id of TEMPLATE_IDS) expect(html).toContain(`data-template="${id}"`)
    expect(SAMPLE_CHARTS).toHaveLength(TEMPLATE_IDS.length)
  })
})

describe('the answer card', () => {
  it('shows key points with linked numbers, then the chart, then the evidence', () => {
    show('ranking', null)
    const points = screen.getByRole('region', { name: 'Key points' }) ?? null
    expect(within(points).getByText(/^Highest:/)).toBeTruthy()
    expect(within(points).getAllByRole('button').length).toBeGreaterThan(1)
    expect(document.querySelector('[data-template="ranking_bar"]')).not.toBeNull()
    expect(screen.getByText('How this was answered')).toBeTruthy()
  })

  it('the trend read and the key figures are in the evidence', () => {
    show('number', null)
    fireEvent.click(screen.getByText('How this was answered'))
    expect(screen.getByText('The same figure by term, for the trend')).toBeTruthy()
    expect(screen.getByText('Key figures worked out from the tables above')).toBeTruthy()
  })
})

describe('buttons per role', () => {
  const president: AnswerActionProps = { role: 'executive', onSend: vi.fn(), asksBriefing: true }

  it('the president sees every button', () => {
    show('ranking', president)
    const bar = screen.getByRole('list', { name: 'What to do with this answer' })
    const names = within(bar)
      .getAllByRole('button')
      .map((b) => b.textContent)
    expect(names).toEqual(['Send to department', 'Show trend', 'Break it down', 'Make a plan', 'See evidence'])
  })

  it('the reviewer does not plan; a role that may not send sees no Send', () => {
    const card = response('ranking').card
    expect(answerActions('reviewer', card, true).plan).toBe(false)
    expect(answerActions('registrar', card, false).send).toBe(false)
    expect(answerActions('registrar', card, true).plan).toBe(true)
  })

  it('no actions, no buttons', () => {
    show('ranking', null)
    expect(screen.queryByText('Send to department')).toBeNull()
  })

  it('Send attaches the answer and its key points', () => {
    const onSend = vi.fn()
    show('ranking', { ...president, onSend })
    fireEvent.click(screen.getByText('Send to department'))
    const quote = onSend.mock.calls[0][0] as string[]
    expect(quote.length).toBeLessThanOrEqual(6)
    expect(quote.some((line) => line.startsWith('Highest:'))).toBe(true)
  })

  it('Show trend and Break it down re-ask checked questions', () => {
    const onAsk = show('ranking', president)
    fireEvent.click(screen.getByText('Show trend'))
    expect(onAsk).toHaveBeenLastCalledWith('Dropout rate by entry cohort')
    fireEvent.click(screen.getByText('Break it down'))
    const choices = screen.getByRole('region', { name: 'Break it down' })
    const buttons = within(choices).getAllByRole('button')
    expect(buttons).toHaveLength(3)
    fireEvent.click(buttons[0])
    expect(onAsk).toHaveBeenLastCalledWith('Dropout rate by major and class level')
  })

  it('Make a plan asks the seven-day plan about registration, else shows a proposed list', () => {
    const onAsk = show('registration', president)
    fireEvent.click(screen.getByText('Make a plan'))
    expect(onAsk).toHaveBeenLastCalledWith(
      'Create a seven-day action plan for Enrollment and Student Success.',
    )
    cleanup()
    const other = show('ranking', { role: 'registrar', onSend: vi.fn(), asksBriefing: false })
    fireEvent.click(screen.getByText('Make a plan'))
    expect(other).not.toHaveBeenCalled()
    const plan = screen.getByRole('region', { name: 'Proposed plan' })
    expect(within(plan).getByText('Proposed, not approved')).toBeTruthy()
    expect(within(plan).getAllByRole('listitem').length).toBeGreaterThan(0)
  })
})

describe('decision status from the department inbox', () => {
  const decision = {
    id: 'D-spring-registration-1',
    title: 'Emergency-aid eligibility review',
    text: 'Decide whether to authorize the review.',
    follow_up: { office: 'Financial Aid', description: 'Conduct the review.' },
    approved: true,
  }
  const base: DispatchInfo = {
    decision_id: decision.id,
    task_id: `TASK-${decision.id}`,
    office: 'Financial Aid',
    office_contact: null,
    approved: true,
    dispatch: null,
    department_roles: ['aid'],
    department_inbox: [],
  }
  const panel = (info: DispatchInfo) =>
    renderToStaticMarkup(
      <DecisionPanel
        decisions={[decision]}
        events={[]}
        canApprove
        role="executive"
        userEmail="president@demo.test"
        approving={false}
        approveError={null}
        approvedTasks={{}}
        dispatches={{ [decision.id]: { info, busy: null, error: null } }}
        onApprove={() => {}}
        onPrepareDispatch={() => {}}
        onSendDispatch={() => {}}
      />,
    )

  it('shows delivered, opened and acknowledged', () => {
    const row = {
      message_id: 1,
      to: { id: 4, email: 'aid@demo.test', role: 'aid' },
      created_at: '2026-10-07T12:00:00+00:00',
      read_at: null,
      acknowledged_at: null,
    }
    expect(panel({ ...base, department_inbox: [row] })).toContain('not opened yet')
    expect(
      panel({ ...base, department_inbox: [{ ...row, read_at: row.created_at }] }),
    ).toContain('not acknowledged yet')
    const done = panel({
      ...base,
      department_inbox: [{ ...row, read_at: row.created_at, acknowledged_at: row.created_at }],
    })
    expect(done).toContain('In the Financial Aid inbox')
    expect(done).toContain('Acknowledged')
    expect(done).toContain('Nothing was emailed')
  })

  it('says so when the department has no account', () => {
    expect(panel(base)).toContain('No Financial Aid account exists yet')
  })
})
