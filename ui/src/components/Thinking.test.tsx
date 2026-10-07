// @vitest-environment jsdom
// The live trace while an Explore question is answered: the streaming client
// (POST /explore/stream with the fallback to POST /explore), the trace
// helpers, the growing list under "Thinking", and the folded "Thought for"
// line above the answer.

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { askExplore } from '../api'
import {
  numberParts,
  traceEventFrom,
  traceLines,
  traceSummary,
  type ExploreResponse,
  type ExploreTraceEvent,
} from '../explore'
import { parseFlags } from '../states'
import { ExploreAnswer, ExploreWorking } from './ExploreAnswer'
import { Thinking } from './Thinking'

const flags = parseFlags('')

const EVENTS: ExploreTraceEvent[] = [
  { type: 'planning', text: 'Matching the question to the approved analyses' },
  { type: 'understood', text: 'Dropout rate by major' },
  { type: 'plan', steps: ['Dropout rate by major'], planner: 'rules' },
  {
    type: 'reading',
    text: 'Reading six years of student records (fictional data): 6,225 students, Fall 2020 to Spring 2026',
  },
  { type: 'step', index: 0, total: 1, title: 'Dropout rate by major' },
  { type: 'writing', text: 'Writing the answer from the tables' },
  { type: 'verifying', checked: 5, matched: 5, text: 'Checked 5 numbers against the tables' },
]

const RESPONSE: ExploreResponse = {
  refused: false,
  answer: [{ text: 'Public Health has the highest dropout rate: 29.9%.', claims: [] }],
  steps: [],
  source: 'Calculated directly from the records',
}

function ndjson(lines: unknown[], chunkAt?: number): Response {
  const text = lines.map((line) => JSON.stringify(line)).join('\n') + '\n'
  const cut = chunkAt ?? Math.floor(text.length / 2)
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      // Split mid-line on purpose: the client must join partial lines.
      controller.enqueue(encoder.encode(text.slice(0, cut)))
      controller.enqueue(encoder.encode(text.slice(cut)))
      controller.close()
    },
  })
  return new Response(body, { status: 200, headers: { 'Content-Type': 'application/x-ndjson' } })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('the trace helpers', () => {
  it('reads only the events it understands', () => {
    expect(traceEventFrom({ type: 'step', index: 1, total: 3, title: 'X' })).toEqual({
      type: 'step',
      index: 1,
      total: 3,
      title: 'X',
    })
    expect(traceEventFrom({ type: 'students', rows: [] })).toBeNull()
    expect(traceEventFrom('nope')).toBeNull()
    expect(traceEventFrom({ type: 'reading' })).toBeNull()
  })

  it('turns events into short lines and announces only step titles', () => {
    const lines = traceLines(EVENTS)
    expect(lines.map((l) => l.text)).toEqual([
      'Reading your question',
      'Understood: Dropout rate by major',
      'Chose 1 approved analysis',
      'Reading six years of student records (fictional data): 6,225 students, Fall 2020 to Spring 2026',
      'Computing: Dropout rate by major',
      'Writing the answer from the tables',
      'Checked 5 numbers against the tables',
    ])
    expect(lines.filter((l) => l.announce).map((l) => l.text)).toEqual([
      'Computing: Dropout rate by major',
    ])
    expect(
      traceLines([{ type: 'step', index: 1, total: 3, title: 'Hardest course' }])[0].text,
    ).toBe('Step 2 of 3: Hardest course')
  })

  it('summarises the finished trace in one line', () => {
    expect(traceSummary(6200, 4)).toBe('Thought for 6 s · 4 steps')
    expect(traceSummary(200, 1)).toBe('Thought for 1 s · 1 step')
  })

  it('finds the figures in a line, leaving years alone', () => {
    const parts = numberParts('6,225 students, Fall 2020 to Spring 2026')
    expect(parts.filter((p) => p.value !== null).map((p) => p.value)).toEqual([6225])
    expect(parts.map((p) => p.text).join('')).toBe('6,225 students, Fall 2020 to Spring 2026')
  })
})

describe('askExplore', () => {
  it('reports each event in order and resolves with the final answer', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(ndjson([...EVENTS, { type: 'done', response: RESPONSE }]))
    vi.stubGlobal('fetch', fetchMock)
    const seen: string[] = []
    const response = await askExplore('q', flags, (event) => seen.push(event.type))
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/explore/stream')
    expect(seen).toEqual(EVENTS.map((e) => e.type))
    expect(response.answer[0].text).toContain('Public Health')
  })

  it('resolves with a refusal', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        ndjson([{ type: 'refused', response: { ...RESPONSE, refused: true, answer: [] } }]),
      ),
    )
    const response = await askExplore('q', flags, () => undefined)
    expect(response.refused).toBe(true)
  })

  it('falls back to POST /explore when the server has no stream', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('{"detail":"Not Found"}', { status: 404 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify(RESPONSE), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    vi.stubGlobal('fetch', fetchMock)
    const response = await askExplore('q', flags, () => undefined)
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      '/api/explore/stream',
      '/api/explore',
    ])
    expect(response.answer).toHaveLength(1)
  })

  it('stops with an error, never a guess, when the stream reports one', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        ndjson([EVENTS[0], { type: 'error', message: 'CampusLens could not finish this answer.' }]),
      ),
    )
    await expect(askExplore('q', flags, () => undefined)).rejects.toThrow(
      'CampusLens could not finish this answer.',
    )
  })
})

describe('the live trace on screen', () => {
  it('shows the timed steps until the first event arrives', () => {
    render(<ExploreWorking trace={[]} />)
    expect(screen.getByText('Reading your question')).toBeTruthy()
    expect(document.querySelector('.trace')).toBeNull()
  })

  it('lists each line, the newest working and the rest checked off', () => {
    const { rerender } = render(<ExploreWorking trace={EVENTS.slice(0, 2)} />)
    let items = document.querySelectorAll('.trace-line')
    expect(items).toHaveLength(2)
    expect(items[0].getAttribute('data-state')).toBe('done')
    expect(items[1].getAttribute('data-state')).toBe('active')
    rerender(<ExploreWorking trace={EVENTS.slice(0, 5)} />)
    items = document.querySelectorAll('.trace-line')
    expect(items).toHaveLength(5)
    expect([...items].map((li) => li.getAttribute('data-state'))).toEqual([
      'done',
      'done',
      'done',
      'done',
      'active',
    ])
    // The list itself is decorative; screen readers hear the step title.
    expect(document.querySelector('.trace')?.getAttribute('aria-hidden')).toBe('true')
    const status = screen.getByRole('status')
    expect(status.textContent).toContain('Computing: Dropout rate by major')
  })

  it('holds still under reduced motion', () => {
    document.documentElement.classList.add('reduce-motion')
    try {
      render(<Thinking trace={traceLines(EVENTS)} />)
      expect(document.querySelectorAll('.trace-line.is-entering')).toHaveLength(0)
      // Figures show their final value at once.
      expect(screen.getByText('6,225')).toBeTruthy()
    } finally {
      document.documentElement.classList.remove('reduce-motion')
    }
  })
})

describe('the folded trace above the answer', () => {
  it('reads "Thought for 6 s · 7 steps" and reopens the full trace', () => {
    render(
      <ExploreAnswer
        answerKey="1"
        response={RESPONSE}
        fallbackSuggestions={[]}
        onAsk={() => undefined}
        busy={false}
        onSeeAuditLog={null}
        trace={EVENTS}
        elapsedMs={6200}
      />,
    )
    const toggle = screen.getByRole('button', { name: /Thought for 6 s · 7 steps/ })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    const body = document.getElementById(toggle.getAttribute('aria-controls') ?? '')
    expect(body?.getAttribute('data-open')).toBe('true')
    expect(body?.querySelectorAll('.trace-line[data-state="done"]')).toHaveLength(7)
  })

  it('folds shut on its own right after a live answer arrives', async () => {
    vi.stubGlobal('matchMedia', () => ({ matches: false }))
    render(
      <ExploreAnswer
        answerKey="2"
        response={RESPONSE}
        fallbackSuggestions={[]}
        onAsk={() => undefined}
        busy={false}
        onSeeAuditLog={null}
        trace={EVENTS}
        elapsedMs={3000}
        live
      />,
    )
    const toggle = screen.getByRole('button', { name: /Thought for 3 s/ })
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 60))
    })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(document.querySelector('.explore-answer.is-rising')).toBeTruthy()
  })

  it('shows no toggle without a trace (an older server)', () => {
    render(
      <ExploreAnswer
        answerKey="3"
        response={RESPONSE}
        fallbackSuggestions={[]}
        onAsk={() => undefined}
        busy={false}
        onSeeAuditLog={null}
      />,
    )
    expect(screen.queryByRole('button', { name: /Thought for/ })).toBeNull()
  })
})
