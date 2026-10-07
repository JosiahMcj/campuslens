// @vitest-environment jsdom

// The Data page: the filter bar (years, compare by, narrowing, the chips),
// a chart that draws a withheld point as a gap (never a zero) with the
// reason in its tooltip, clicking a group to narrow to it, and the choices
// remembered per account. The API is mocked at the dataPage client.

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ChartData, DataCatalog, SeriesResponse } from '../dataPage'

const fetchDataCatalog = vi.fn<() => Promise<DataCatalog>>()
const fetchSeries = vi.fn<(query: string) => Promise<SeriesResponse>>()
vi.mock('../dataPage', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../dataPage')>()
  return {
    ...actual,
    fetchDataCatalog: () => fetchDataCatalog(),
    fetchSeries: (query: string) => fetchSeries(query),
  }
})

import { DataPage } from './DataPage'

const YEARS = ['2020-2021', '2021-2022', '2022-2023']

const CATALOG: DataCatalog = {
  institution: 'Demonstration University',
  fictional: true,
  dashboards: [
    {
      id: 'students',
      title: 'Students',
      intro: 'Enrollment over time.',
      charts: [{ id: 'headcount', title: 'Students enrolled', form: 'line' }],
    },
    {
      id: 'finances',
      title: 'Student finances',
      intro: 'Holds and balances.',
      charts: [{ id: 'financial_hold_rate', title: 'Share with a financial hold', form: 'line' }],
    },
  ],
  filters: [
    {
      key: 'gender',
      label: 'Gender',
      options: [
        { value: 'female', label: 'Women' },
        { value: 'male', label: 'Men' },
      ],
    },
    {
      key: 'pell',
      label: 'Pell grant',
      options: [
        { value: 'pell', label: 'Pell recipients' },
        { value: 'no_pell', label: 'Students without Pell' },
      ],
    },
  ],
  compare: [
    { key: 'gender', label: 'Gender' },
    { key: 'pell', label: 'Pell grant' },
  ],
  years: YEARS,
  minimum_cell_size: 10,
}

const X = [
  { key: '202110', label: 'Fall 2020', year: '2020-2021' },
  { key: '202210', label: 'Fall 2021', year: '2021-2022' },
  { key: '202310', label: 'Fall 2022', year: '2022-2023' },
]

function chart(overrides: Partial<ChartData> = {}): ChartData {
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
        points: [
          { x: '202110', value: 120, status: 'ok', students: 120 },
          { x: '202210', value: null, status: 'withheld' },
          { x: '202310', value: 140, status: 'ok', students: 140 },
        ],
      },
    ],
    notes: [],
    minimum_cell_size: 10,
    ...overrides,
  }
}

const SPLIT = chart({
  split: { key: 'gender', label: 'Gender' },
  series: [
    {
      key: 'female',
      label: 'Women',
      slot: 0,
      points: X.map((x, i) => ({ x: x.key, value: 60 + i, status: 'ok' as const, students: 60 + i })),
    },
    {
      key: 'male',
      label: 'Men',
      slot: 1,
      points: X.map((x, i) => ({ x: x.key, value: 50 + i, status: 'ok' as const, students: 50 + i })),
    },
  ],
})

beforeEach(() => {
  window.localStorage.clear()
  fetchDataCatalog.mockReset()
  fetchSeries.mockReset()
  fetchDataCatalog.mockResolvedValue(CATALOG)
  fetchSeries.mockImplementation(async (query) => (query.includes('compare=gender') ? SPLIT : chart()))
})
afterEach(cleanup)

async function ready() {
  render(<DataPage account="president@demo.test" />)
  await screen.findByRole('img', { name: /Students enrolled/ })
}

describe('DataPage filter bar', () => {
  it('lists the dashboards, the years and the compare choices', async () => {
    await ready()
    expect(screen.getByRole('button', { name: 'Students', pressed: true })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Student finances', pressed: false })).toBeTruthy()
    const from = screen.getByLabelText('From') as HTMLSelectElement
    expect([...from.options].map((o) => o.textContent)).toEqual(['2020–2021', '2021–2022', '2022–2023'])
    const compare = screen.getByLabelText('Compare by') as HTMLSelectElement
    expect([...compare.options].map((o) => o.textContent)).toEqual(['No comparison', 'Gender', 'Pell grant'])
    expect(screen.getByText('Fictional data')).toBeTruthy()
    expect(fetchSeries).toHaveBeenCalledWith('chart=headcount')
  })

  it('narrows every chart to one group, shows it as a chip, and clears it', async () => {
    await ready()
    const students = screen.getByLabelText('Students') as HTMLSelectElement
    expect(students.querySelectorAll('optgroup')).toHaveLength(2)
    fireEvent.change(students, { target: { value: 'gender=female' } })
    await waitFor(() => expect(fetchSeries).toHaveBeenCalledWith('chart=headcount&gender=female'))
    const chip = screen.getByRole('button', { name: 'Remove Gender: Women' })
    expect(chip.textContent).toContain('Women')
    // One group at a time: choosing another replaces it.
    fireEvent.change(students, { target: { value: 'pell=pell' } })
    await waitFor(() => expect(fetchSeries).toHaveBeenCalledWith('chart=headcount&pell=pell'))
    expect(screen.queryByRole('button', { name: /Remove Gender/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Show all students' }))
    expect(screen.queryByRole('button', { name: /Remove Pell/ })).toBeNull()
    expect(students.value).toBe('')
  })

  it('never narrows and compares at once: choosing one clears the other', async () => {
    await ready()
    fireEvent.change(screen.getByLabelText('Students'), { target: { value: 'gender=male' } })
    fireEvent.change(screen.getByLabelText('Compare by'), { target: { value: 'pell' } })
    await waitFor(() => expect(fetchSeries).toHaveBeenCalledWith('chart=headcount&compare=pell'))
    expect((screen.getByLabelText('Students') as HTMLSelectElement).value).toBe('')
    fireEvent.change(screen.getByLabelText('Students'), { target: { value: 'gender=female' } })
    expect((screen.getByLabelText('Compare by') as HTMLSelectElement).value).toBe('')
    for (const [query] of fetchSeries.mock.calls) {
      expect(query.includes('compare=') && /&(gender|pell)=/.test(query)).toBe(false)
    }
    expect(screen.getByText(/Not both at once/)).toBeTruthy()
  })

  it('splits a chart by the comparison, and a group click narrows to it', async () => {
    await ready()
    fireEvent.change(screen.getByLabelText('Compare by'), { target: { value: 'gender' } })
    await waitFor(() => expect(fetchSeries).toHaveBeenCalledWith('chart=headcount&compare=gender'))
    const legend = await screen.findByRole('list', { name: 'Groups in this chart' })
    fireEvent.click(within(legend).getByRole('button', { name: /^Men:/ }))
    await waitFor(() => expect(fetchSeries).toHaveBeenCalledWith('chart=headcount&gender=male'))
    expect((screen.getByLabelText('Compare by') as HTMLSelectElement).value).toBe('')
    expect(screen.getByRole('button', { name: 'Remove Gender: Men' })).toBeTruthy()
  })

  it('slices the years without asking the server again', async () => {
    await ready()
    const calls = fetchSeries.mock.calls.length
    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2022-2023' } })
    fireEvent.click(await screen.findByText('Show the figures'))
    const table = screen.getByRole('table')
    expect(within(table).getAllByRole('row').map((r) => r.firstChild?.textContent)).toEqual(['Term', 'Fall 2022'])
    expect(fetchSeries.mock.calls.length).toBe(calls)
  })

  it('remembers the choices per account and drops ones the catalog does not offer', async () => {
    window.localStorage.setItem(
      'campuslens.data-page.v1:president@demo.test',
      JSON.stringify({
        dashboard: 'finances',
        from: '',
        to: '',
        compare: 'gender',
        filters: { pell: 'pell', gender: 'male', gpa: 'x' },
      }),
    )
    render(<DataPage account="president@demo.test" />)
    // Saved from an older page: one group survives, the comparison does not.
    await waitFor(() => expect(fetchSeries).toHaveBeenCalledWith('chart=financial_hold_rate&gender=male'))
    expect(screen.getByRole('button', { name: 'Student finances', pressed: true })).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Students'), { target: { value: 'pell=no_pell' } })
    const saved = JSON.parse(window.localStorage.getItem('campuslens.data-page.v1:president@demo.test') ?? '{}')
    expect(saved.filters).toEqual({ pell: 'no_pell' })
    expect(saved.compare).toBe('')
    expect(window.localStorage.getItem('campuslens.data-page.v1:aid@demo.test')).toBeNull()
  })

  it('works when storage is blocked', async () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    await ready()
    spy.mockRestore()
  })

  it('says when a chart cannot take the selection instead of ignoring it', async () => {
    fetchSeries.mockResolvedValue({
      chart: 'headcount',
      title: 'Students enrolled',
      not_applicable: { key: 'class_level', label: 'Class level' },
    })
    render(<DataPage account="president@demo.test" />)
    expect(await screen.findByText(/can't be narrowed or split by class level/)).toBeTruthy()
  })

  it('offers Retry when the dashboards cannot load', async () => {
    fetchDataCatalog.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    render(<DataPage account="president@demo.test" />)
    const retry = await screen.findByRole('button', { name: 'Retry' })
    fireEvent.click(retry)
    await screen.findByRole('img', { name: /Students enrolled/ })
  })
})

describe('DataChart gaps', () => {
  it('draws a withheld point as a gap with its reason, never as a zero', async () => {
    await ready()
    const svg = screen.getByRole('img', { name: /Students enrolled/ })
    // Two separate line runs (the gap breaks the line) and a dashed guide.
    expect(svg.querySelectorAll('path.chart-line')).toHaveLength(2)
    expect(svg.querySelectorAll('circle.chart-dot')).toHaveLength(2)
    expect(svg.querySelectorAll('line.chart-withheld')).toHaveLength(1)
    // Keyboard: focus lands on the latest term, Left reaches the withheld one.
    fireEvent.focus(svg)
    fireEvent.keyDown(svg, { key: 'ArrowLeft' })
    const tip = screen.getByRole('status')
    expect(tip.textContent).toContain('Fall 2021 · Students')
    expect(tip.textContent).toContain('All students')
    expect(tip.textContent).toContain('Withheld: fewer than 10 students')
    expect(tip.textContent).not.toMatch(/\b0\b/)
    fireEvent.keyDown(svg, { key: 'Home' })
    expect(screen.getByRole('status').textContent).toContain('Fall 2020 · Students')
    expect(screen.getByRole('status').textContent).toContain('120')
    // The table names the withheld point too.
    fireEvent.click(screen.getByText('Show the figures'))
    const cell = within(screen.getByRole('table')).getByText('Withheld')
    expect(cell.getAttribute('title')).toContain('fewer than 10 students')
  })

  it('says so when every point in a chart is withheld', async () => {
    fetchSeries.mockResolvedValue(
      chart({
        series: [
          {
            key: 'all',
            label: 'Selected students',
            slot: 0,
            points: X.map((x) => ({ x: x.key, value: null, status: 'withheld' as const })),
          },
        ],
      }),
    )
    render(<DataPage account="president@demo.test" />)
    expect(await screen.findByText(/Every figure here is withheld/)).toBeTruthy()
    expect(screen.queryByRole('img')).toBeNull()
  })

  it('marks a value axis that does not start at zero', async () => {
    fetchSeries.mockResolvedValue(
      chart({
        kind: 'gpa',
        value_label: 'Average cumulative GPA',
        series: [
          {
            key: 'all',
            label: 'All students',
            slot: 0,
            points: X.map((x, i) => ({ x: x.key, value: 3.04 + i * 0.01, status: 'ok' as const, students: 900 })),
          },
        ],
      }),
    )
    render(<DataPage account="president@demo.test" />)
    const svg = await screen.findByRole('img', { name: /not zero/ })
    expect(svg.querySelector('.chart-break')).not.toBeNull()
  })
})
