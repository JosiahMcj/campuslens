// Sample data for the chart template gallery (/dev/charts) and its tests.
import type { ChartModel, Mark } from './answerCard'

function m(label: string, value: number | null, withheld = false): Mark {
  return { key: label, label, value, withheld, claim: null }
}

const TERMS = ['Fall 2022', 'Spring 2023', 'Fall 2023', 'Spring 2024', 'Fall 2024', 'Spring 2025', 'Fall 2025', 'Spring 2026']

function line(values: (number | null)[], withheldAt: number[] = []) {
  return TERMS.map((term, i) => ({
    x: term,
    value: withheldAt.includes(i) ? null : values[i],
    status: withheldAt.includes(i) ? ('withheld' as const) : ('ok' as const),
  }))
}

/** Sample data only: one chart per template, every one with a withheld gap
 * where the template can show one. */
export const SAMPLE_CHARTS: ChartModel[] = [
  {
    template: 'ranking_bar',
    title: 'Dropout rate by major',
    kind: 'pct',
    bars: [m('Mechanical Engineering', 19.6), m('Nursing', 15.2), m('Biology', 13.9), m('Data Science', null, true), m('Marketing', 11.8)],
    reference: m('All students', 12.1),
  },
  {
    template: 'trend_line',
    title: 'Headcount by term',
    delta: { direction: 'down', change: 683, changePct: 4.7, since: 'Spring 2025' },
    data: {
      chart: 'sample-trend',
      title: 'Headcount by term',
      form: 'line',
      kind: 'count',
      value_label: 'Students',
      definition: '',
      x_label: 'Term',
      x: TERMS.map((t) => ({ key: t, label: t, year: '' })),
      split: null,
      series: [{ key: 'all', label: 'Students', slot: 0, points: line([15100, 13700, 16124, 14400, 15800, 14554, 15982, 13871], [3]) }],
      notes: [],
      minimum_cell_size: 10,
    },
  },
  {
    template: 'grouped_bars',
    title: 'Hold rate by college and class level',
    data: {
      chart: 'sample-grouped',
      title: 'Hold rate by college and class level',
      form: 'bar',
      kind: 'pct',
      value_label: 'Hold rate',
      definition: '',
      x_label: 'Group',
      x: ['Business', 'Nursing', 'Engineering'].map((t) => ({ key: t, label: t, year: '' })),
      split: null,
      series: [
        { key: 'fr', label: 'Freshmen', slot: 0, points: [{ x: 'Business', value: 19.1, status: 'ok' }, { x: 'Nursing', value: 21.6, status: 'ok' }, { x: 'Engineering', value: 18.2, status: 'ok' }] },
        { key: 'sr', label: 'Seniors', slot: 1, points: [{ x: 'Business', value: 12.4, status: 'ok' }, { x: 'Nursing', value: null, status: 'withheld' }, { x: 'Engineering', value: 10.4, status: 'ok' }] },
      ],
      notes: [],
      minimum_cell_size: 10,
    },
  },
  {
    template: 'kpi_number',
    title: 'Pell share',
    kind: 'pct',
    value: m('Pell share', 33.0),
    scope: 'Spring 2026',
    delta: { direction: 'down', change: 0.2, changePct: 0.6, since: 'Spring 2025' },
  },
  {
    template: 'share_bar',
    title: 'Headcount by residency',
    kind: 'count',
    parts: [m('In-state students', 9800), m('Out-of-state students', 3200), m('International students', 871)],
    total: 13871,
  },
  {
    template: 'before_after',
    title: 'Continuing students registered',
    kind: 'count',
    before: m('Spring 2025', 14224),
    after: m('Spring 2026', 13541),
    changePct: -4.8,
  },
  {
    template: 'funnel',
    title: 'Continuing students and spring registration',
    kind: 'count',
    stages: [m('Continuing students', 14224), m('Not registered yet', 683), m('With a hold', 241), m('Reached by an advisor', null, true)],
  },
  {
    template: 'small_multiples',
    title: 'Stop-out rate by term, for each class level',
    kind: 'pct',
    panels: [
      ['Freshmen', [9.1, 11.2, 8.8, 10.9, 8.4, 10.1, 9.9, 9.0]],
      ['Sophomores', [6.2, 7.9, 6.0, null, 5.8, 7.7, 6.9, 6.1]],
      ['Juniors', [4.1, 5.2, 4.0, 5.0, 3.9, 4.8, 4.6, 4.2]],
      ['Seniors', [3.0, 3.6, 2.9, 3.4, 2.8, 3.3, 3.1, 2.9]],
    ].map(([label, values]) => ({
      label: label as string,
      points: (values as (number | null)[]).map((value, i) =>
        m(TERMS[i], value, value === null),
      ),
    })),
  },
]

