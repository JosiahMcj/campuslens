// @vitest-environment jsdom

// The Data page's actions and layout: every card has the same four rows
// with its actions in the footer; legends are chips that stop at two rows
// with "+N more"; clicking a point (or Enter on the chart) opens a popover
// with its exact value and Ask about this / Send to department; a withheld
// point never reveals a value; Ask builds the question and starts a new
// chat (with an About chip) through Explore; Send opens Send alert with a
// chart attachment; and a chart someone sent is redrawn in the inbox.

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../App'
import { resetBriefingOnce } from '../api'
import { clearSession } from '../auth'
import {
  chartAbout,
  chartQuestion,
  chartRef,
  chipsThatFit,
  shortLabels,
  WHOLE_CHART,
  type ChartData,
  type DataCatalog,
} from '../dataPage'
import type { AlertSource, InboxMessage } from '../inbox'
import { DataPage, type ChartAsk } from './DataPage'
import { InboxPage } from './InboxPage'

const X = [
  { key: '202310', label: 'Fall 2022', year: '2022-2023' },
  { key: '202320', label: 'Spring 2023', year: '2022-2023' },
  { key: '202410', label: 'Fall 2023', year: '2023-2024' },
  { key: '202420', label: 'Spring 2024', year: '2023-2024' },
  { key: '202510', label: 'Fall 2024', year: '2024-2025' },
]

const COLLEGES = [
  'College of Arts and Sciences',
  'College of Engineering and Computing',
  'College of Nursing and Health Sciences',
  'College of Business',
  'College of Education',
  'College of Social and Behavioral Sciences',
  'College of Theology, Ministry, and the Arts',
]

function headcount(): ChartData {
  return {
    chart: 'headcount',
    title: 'Students enrolled',
    form: 'line',
    kind: 'count',
    value_label: 'Students',
    definition: 'Students enrolled in the term.',
    x_label: 'Term',
    x: X,
    split: null,
    series: [
      {
        key: 'all',
        label: 'All students',
        slot: 0,
        points: X.map((x, i) => ({ x: x.key, value: 1000 + i * 10, status: 'ok' as const })),
      },
    ],
    notes: [],
    minimum_cell_size: 10,
  }
}

/** By college: Engineering fell from Fall 2023 to Fall 2024; Education's
 * Fall 2024 point is withheld. */
function byCollege(): ChartData {
  const values = [300, 280, 320, 290, 250]
  return {
    ...headcount(),
    chart: 'headcount_by_college',
    title: 'Students enrolled by college',
    split: { key: 'college', label: 'College' },
    series: COLLEGES.map((label, s) => ({
      key: `C${s}`,
      label,
      slot: s,
      points: X.map((x, i) =>
        label === 'College of Education' && i === 4
          ? { x: x.key, value: null, status: 'withheld' as const }
          : { x: x.key, value: values[i] + s, status: 'ok' as const },
      ),
    })),
  }
}

const CATALOG: DataCatalog = {
  institution: 'Demonstration University',
  fictional: true,
  dashboards: [
    {
      id: 'students',
      title: 'Students',
      intro: 'Enrollment over time.',
      charts: [
        { id: 'headcount', title: 'Students enrolled', form: 'line' },
        { id: 'headcount_by_college', title: 'Students enrolled by college', form: 'line' },
      ],
    },
  ],
  filters: [],
  compare: [],
  years: ['2022-2023', '2023-2024', '2024-2025'],
  minimum_cell_size: 10,
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** The API for the Data page (and, for the App, the rest of a sign-in). */
function mockApi(role = 'executive', extra: Record<string, (url: string, init?: RequestInit) => Response> = {}) {
  const calls: { url: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url: `${init?.method ?? 'GET'} ${url}`, body: init?.body ? JSON.parse(String(init.body)) : null })
      const path = url.replace(/^\/api/, '').split('?')[0]
      if (extra[path] !== undefined) return extra[path](url, init)
      switch (path) {
        case '/auth/me':
          return json({
            user: {
              id: 9,
              email: `${role}@demo.test`,
              role,
              institution_id: 1,
              institution: { slug: 'bootstrap', name: 'Demonstration University' },
            },
            csrf_token: 'token',
          })
        case '/findings':
          return json({ meta: { as_of: null, fixture: 'demo', terms: {}, fictional: true, dataset: { id: 1, name: 'Demo', sha256: 'x' } } })
        case '/questions':
          return json([])
        case '/decisions':
          return json({ question_id: null, decisions: [] })
        case '/events':
          return json({ events: [] })
        case '/briefing':
          return json({ detail: 'none' }, 404)
        case '/explore/catalog':
          return json({ examples: [] })
        case '/inbox':
          return json({ received: [], sent: [], unread: 0 })
        case '/data/dashboards':
          return json(CATALOG)
        case '/data/series':
          return json(url.includes('headcount_by_college') ? byCollege() : headcount())
        default:
          return json({ detail: 'not found' }, 404)
      }
    }),
  )
  return calls
}

function card(title: string): HTMLElement {
  return screen.getByRole('heading', { name: title }).closest('figure') as HTMLElement
}

function clickPoint(cardTitle: string, seriesKey: string, index: number) {
  const hit = card(cardTitle).querySelector(`circle.chart-dot-hit[data-series="${seriesKey}"][data-index="${index}"]`)
  expect(hit).not.toBeNull()
  fireEvent.click(hit as Element)
}

beforeEach(() => {
  window.localStorage.clear()
  window.sessionStorage.clear()
  Element.prototype.scrollIntoView = vi.fn()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('the question a chart asks', () => {
  it('asks why a point fell or rose from the same term a year before', () => {
    const data = byCollege()
    const engineering = { index: 4, seriesKey: 'C1' }
    expect(chartQuestion(data, null, engineering)).toBe(
      'Why did enrollment for College of Engineering and Computing drop in Fall 2024?',
    )
    expect(chartAbout(data, null, engineering)).toBe(
      'About: Students enrolled by college · College of Engineering and Computing · Fall 2024',
    )
    // Spring 2024 vs Spring 2023: a rise.
    expect(chartQuestion(data, null, { index: 3, seriesKey: 'C1' })).toBe(
      'Why did enrollment for College of Engineering and Computing rise in Spring 2024?',
    )
  })

  it('asks for more about the whole chart, a term, or a withheld point, never with a figure', () => {
    const data = byCollege()
    expect(chartQuestion(data, null, WHOLE_CHART)).toBe(
      'Tell me more about students enrolled in Fall 2024 by college.',
    )
    expect(chartQuestion(data, null, { index: 2, seriesKey: null })).toBe(
      'Tell me more about students enrolled in Fall 2023 by college.',
    )
    const withheld = chartQuestion(data, null, { index: 4, seriesKey: 'C4' })
    expect(withheld).toBe('Tell me more about students enrolled for College of Education in Fall 2024.')
    expect(withheld.replace(/\b20\d\d\b/g, '')).not.toMatch(/\d/)
    expect(chartQuestion(headcount(), { label: 'Gender', value: 'Women' }, WHOLE_CHART)).toBe(
      'Tell me more about students enrolled for Women in Fall 2024.',
    )
  })

  it('writes the reference an alert stores', () => {
    expect(chartRef('headcount_by_college', '', {}, byCollege(), { index: 4, seriesKey: 'C1' })).toBe(
      'chart=headcount_by_college&at=202510&series=C1',
    )
    expect(chartRef('retention', '', { college: 'ENG' }, null, WHOLE_CHART)).toBe('chart=retention&college=ENG')
  })

  it('fits legend chips in two rows with room for "+N more", and drops a shared "College of"', () => {
    expect(chipsThatFit([100, 100, 100], 400, 0, 2, 60)).toBe(3)
    // Two 200 px chips per 420 px row: three chips and "+N more" fill two rows.
    expect(chipsThatFit([200, 200, 200, 200, 200, 200, 200], 420, 0, 2, 200)).toBe(3)
    expect(chipsThatFit([500, 500, 500], 300, 4, 2, 60)).toBe(1)
    expect(shortLabels(COLLEGES)[0]).toBe('Arts and Sciences')
    expect(shortLabels(['Pell recipients', 'Students without Pell'])).toEqual([
      'Pell recipients',
      'Students without Pell',
    ])
  })
})

describe('Data page cards', () => {
  it('gives every card the same rows, with the actions in the footer', async () => {
    mockApi()
    render(<DataPage account="p@demo.test" onAsk={() => undefined} onSend={() => undefined} />)
    await screen.findAllByRole('img', { name: /Students enrolled/ })
    await screen.findByRole('img', { name: /by college/ })
    for (const title of ['Students enrolled', 'Students enrolled by college']) {
      const figure = card(title)
      expect([...figure.children].map((c) => c.className.split(' ')[0])).toEqual([
        'data-card-head',
        'data-card-plot',
        'data-card-extras',
        'data-card-foot',
      ])
      const foot = within(figure.querySelector('.data-card-foot') as HTMLElement)
      expect(foot.getAllByRole('button').map((b) => b.textContent)).toEqual([
        'Show the figures',
        'Ask about this',
        'Send to department',
      ])
    }
    // The figures open under the actions.
    const toggle = within(card('Students enrolled')).getByRole('button', { name: 'Show the figures' })
    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(within(card('Students enrolled')).getByRole('table')).toBeTruthy()
  })

  it('offers no Ask for a role that cannot ask (Financial Aid)', async () => {
    mockApi('aid')
    render(<DataPage account="aid@demo.test" onAsk={null} onSend={() => undefined} />)
    await screen.findByRole('img', { name: /by college/ })
    expect(within(card('Students enrolled')).queryByRole('button', { name: 'Ask about this' })).toBeNull()
    expect(within(card('Students enrolled')).getByRole('button', { name: 'Send to department' })).toBeTruthy()
  })

  it('shows legend chips in two rows and the rest behind "+N more"', async () => {
    // Lay chips out at 200 px in a 420 px list.
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const width = this.tagName === 'LI' ? 200 : this.tagName === 'UL' ? 420 : 640
      return { width, height: 20, top: 0, left: 0, right: width, bottom: 20, x: 0, y: 0, toJSON: () => ({}) }
    })
    mockApi()
    render(<DataPage account="p@demo.test" onAsk={null} onSend={null} />)
    await screen.findByRole('img', { name: /by college/ })
    const legend = within(card('Students enrolled by college')).getByRole('list', { name: 'Groups in this chart' })
    const chips = () => within(legend).getAllByRole('button').map((b) => b.textContent)
    await waitFor(() => expect(chips()).toEqual(['Arts and Sciences', 'Engineering and Computing', 'Nursing and Health Sciences', '+4 more']))
    // Each chip still names the whole group for a screen reader.
    expect(within(legend).getByRole('button', { name: /^College of Arts and Sciences: show only/ })).toBeTruthy()
    fireEvent.click(within(legend).getByRole('button', { name: '+4 more' }))
    expect(chips()).toHaveLength(8)
    expect(chips()[7]).toBe('Show fewer')
  }, 10000)
})

describe('a point on a chart', () => {
  it('opens a popover with the exact value, and asks or sends about that point', async () => {
    mockApi()
    const asks: ChartAsk[] = []
    const sends: AlertSource[] = []
    render(<DataPage account="p@demo.test" onAsk={(a) => asks.push(a)} onSend={(s) => sends.push(s)} />)
    await screen.findByRole('img', { name: /by college/ })
    clickPoint('Students enrolled by college', 'C1', 4)
    const pop = await screen.findByRole('dialog', { name: 'Fall 2024: Students enrolled by college' })
    expect(within(pop).getByText('College of Engineering and Computing')).toBeTruthy()
    expect(within(pop).getByText('251')).toBeTruthy()
    fireEvent.click(within(pop).getByRole('button', { name: 'Ask about this' }))
    expect(asks).toEqual([
      {
        question: 'Why did enrollment for College of Engineering and Computing drop in Fall 2024?',
        about: 'About: Students enrolled by college · College of Engineering and Computing · Fall 2024',
      },
    ])
    expect(screen.queryByRole('dialog')).toBeNull()
    clickPoint('Students enrolled by college', 'C1', 4)
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Send to department' }))
    expect(sends).toEqual([
      {
        kind: 'chart',
        ref: 'chart=headcount_by_college&at=202510&series=C1',
        label: 'Students enrolled by college · College of Engineering and Computing · Fall 2024',
      },
    ])
  })

  it('opens a term with Enter, lists every group, and Escape returns to the chart', async () => {
    mockApi()
    render(<DataPage account="p@demo.test" onAsk={() => undefined} onSend={() => undefined} />)
    const svg = await screen.findByRole('img', { name: /by college/ })
    fireEvent.focus(svg)
    fireEvent.keyDown(svg, { key: 'Enter' })
    const pop = await screen.findByRole('dialog', { name: 'Fall 2024: Students enrolled by college' })
    const rows = within(pop).getAllByRole('button', { name: /Choose this group$/ })
    expect(rows).toHaveLength(7)
    expect(document.activeElement).toBe(rows[0])
    // Choosing a group scopes the popover to it.
    fireEvent.click(rows[1])
    expect(within(pop).queryAllByRole('button', { name: /Choose this group$/ })).toHaveLength(0)
    expect(within(pop).getByRole('button', { name: 'Every group in Fall 2024' })).toBeTruthy()
    fireEvent.keyDown(pop, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(svg)
  })

  it('says a withheld point is withheld and its actions carry no value', async () => {
    mockApi()
    const asks: ChartAsk[] = []
    const sends: AlertSource[] = []
    render(<DataPage account="p@demo.test" onAsk={(a) => asks.push(a)} onSend={(s) => sends.push(s)} />)
    const svg = await screen.findByRole('img', { name: /by college/ })
    fireEvent.focus(svg)
    fireEvent.keyDown(svg, { key: 'Enter' })
    const pop = await screen.findByRole('dialog')
    fireEvent.click(within(pop).getByRole('button', { name: /^College of Education: Withheld/ }))
    expect(within(pop).getByText(/^Withheld: fewer than 10 students/)).toBeTruthy()
    fireEvent.click(within(pop).getByRole('button', { name: 'Ask about this' }))
    expect(asks[0].question).toBe('Tell me more about students enrolled for College of Education in Fall 2024.')
    fireEvent.focus(svg)
    fireEvent.keyDown(svg, { key: 'Enter' })
    const again = await screen.findByRole('dialog')
    fireEvent.click(within(again).getByRole('button', { name: /^College of Education: Withheld/ }))
    fireEvent.click(within(again).getByRole('button', { name: 'Send to department' }))
    expect(sends[0]).toMatchObject({ kind: 'chart', ref: 'chart=headcount_by_college&at=202510&series=C4' })
  })
})

describe('Ask and Send in the app', () => {
  beforeEach(() => {
    clearSession()
    resetBriefingOnce()
  })

  it('starts a new chat about the chart through Explore, with an About chip', async () => {
    const calls = mockApi('executive', {
      '/explore/stream': () => json({ detail: 'down for the test' }, 503),
      '/explore': () => json({ detail: 'down for the test' }, 503),
    })
    window.history.replaceState(null, '', '/view/data')
    render(<App />)
    await screen.findByRole('img', { name: /by college/ })
    clickPoint('Students enrolled by college', 'C1', 4)
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Ask about this' }))
    const question = 'Why did enrollment for College of Engineering and Computing drop in Fall 2024?'
    expect(await screen.findByText(question, { selector: '.msg-user p' })).toBeTruthy()
    expect(
      screen.getByText('About: Students enrolled by college · College of Engineering and Computing · Fall 2024'),
    ).toBeTruthy()
    // Asked of Explore directly, never routed through the briefing's follow-ups.
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('/explore') && JSON.stringify(c.body).includes(question))).toBe(true),
    )
    expect(calls.some((c) => c.url.includes('/briefing/follow-up'))).toBe(false)
  })

  it('opens Send alert with the chart attached, offering only people who may read it', async () => {
    let asked = ''
    mockApi('executive', {
      '/inbox/recipients': (url) => {
        asked = url
        return json([{ id: 10, email: 'registrar@demo.test', role: 'registrar' }])
      },
    })
    window.history.replaceState(null, '', '/view/data')
    render(<App />)
    await screen.findByRole('img', { name: /by college/ })
    fireEvent.click(within(card('Students enrolled')).getByRole('button', { name: 'Send to department' }))
    const dialog = await screen.findByRole('dialog', { name: 'Send an alert' })
    expect(within(dialog).getByText('Attached: Students enrolled · Fall 2024')).toBeTruthy()
    await waitFor(() => expect(asked).toBe('/api/inbox/recipients?kind=chart&ref=chart%3Dheadcount'))
  })
})

describe('a chart in the inbox', () => {
  const message = (snapshot: unknown, available = true): InboxMessage => ({
    id: 3,
    from: { id: 2, email: 'president@demo.test', role: 'executive' },
    to: { id: 9, email: 'registrar@demo.test', role: 'registrar' },
    note: 'Engineering dipped.',
    review_by: null,
    source_kind: 'chart',
    source_ref: 'chart=headcount_by_college&at=202510&series=C1',
    snapshot: snapshot as InboxMessage['snapshot'],
    attachment_available: available,
    created_at: '2026-10-07T15:00:00+00:00',
    read_at: '2026-10-07T15:01:00+00:00',
    reviewed_at: null,
  })

  it('redraws the chart with the chosen point, and asks about it in a new chat', async () => {
    const snapshot = { ...byCollege(), ref: 'x', group: null, at: '202510', focus_series: 'C1' }
    vi.stubGlobal('fetch', vi.fn(async () => json({ received: [message(snapshot)], sent: [], unread: 0 })))
    const onAsk = vi.fn()
    render(<InboxPage onChanged={() => undefined} onAsk={onAsk} />)
    fireEvent.click(await screen.findByRole('button', { name: /From President/ }))
    expect(await screen.findByText('Fall 2024 · College of Engineering and Computing')).toBeTruthy()
    expect(screen.getByText('251')).toBeTruthy()
    expect(screen.getByRole('img', { name: /by college/ })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Ask about this' }))
    expect(onAsk).toHaveBeenCalledWith(
      'Why did enrollment for College of Engineering and Computing drop in Fall 2024?',
      'About: Students enrolled by college · College of Engineering and Computing · Fall 2024',
    )
  })

  it('says the chart is not available when the reader may not see it', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ received: [message(null, false)], sent: [], unread: 0 })))
    render(<InboxPage onChanged={() => undefined} onAsk={null} />)
    fireEvent.click(await screen.findByRole('button', { name: /From President/ }))
    expect(await screen.findByText(/The chart this alert points at is not available to you/)).toBeTruthy()
  })
})
