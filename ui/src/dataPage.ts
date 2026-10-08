// The Data page: typed client for GET /api/data/dashboards and
// GET /api/data/series, the role table the page is gated by, the person's
// saved choices, and the number formats the charts use.
//
// Every figure is computed and withheld on the server (groups under 10
// students are withheld there, never here). The page only slices the years
// it shows and draws what it receives: a withheld point arrives as
// `value: null` with `status: 'withheld'` and is drawn as a gap.

import { apiFailure } from './adminErrors'
import { apiFetch, type Role } from './auth'

/** Which roles have a Data page. Mirrors ROLE_DASHBOARDS in
 * backend/src/cabinet/data_roles.py, which the API enforces; the catalog
 * the page reads names the dashboards themselves. */
export const DATA_PAGE_ROLES: readonly string[] = [
  'executive',
  'aid',
  'staff',
  'reviewer',
  // The department roles. Finance sees the university budget, Student Accounts
  // the student finances (mirrors data_roles.py).
  'finance',
  'studentaccounts',
  'registrar',
  'studentlife',
  'admissions',
  'advising',
  'provost',
  'ir',
  'careers',
  'advancement',
  'international',
  'athletics',
]

export function canSeeDataPage(role: Role | string | null): boolean {
  return role !== null && DATA_PAGE_ROLES.includes(role)
}

export interface ChartSummary {
  id: string
  title: string
  form: 'line' | 'bar'
}

export interface Dashboard {
  id: string
  title: string
  intro: string
  charts: ChartSummary[]
  /** False for a dashboard with no student data (the university budget):
   * the student filter and comparison do not apply to it. */
  students?: boolean
}

export interface FilterOption {
  value: string
  label: string
}

export interface FilterSpec {
  key: string
  label: string
  options: FilterOption[]
}

export interface DataCatalog {
  institution: string
  fictional: boolean
  dashboards: Dashboard[]
  filters: FilterSpec[]
  compare: { key: string; label: string }[]
  years: string[]
  minimum_cell_size: number
}

export type PointStatus = 'ok' | 'withheld' | 'none'

export interface Point {
  x: string
  value: number | null
  status: PointStatus
  students?: number
  numerator?: number
  denominator?: number
}

export interface Series {
  key: string
  label: string
  /** The colour slot (0-based), fixed per group; null for the "All students"
   * reference line. */
  slot: number | null
  points: Point[]
}

/** `pct_fit` is a percentage whose axis fits the data instead of starting
 * at zero (spending as a share of budget, around 100; the discount rate). */
export type ValueKind = 'count' | 'pct' | 'gpa' | 'dollars' | 'average' | 'years' | 'pct_fit'

export interface XValue {
  key: string
  label: string
  year: string
}

export interface ChartData {
  chart: string
  title: string
  form: 'line' | 'bar'
  kind: ValueKind
  value_label: string
  definition: string
  x_label: string
  x: XValue[]
  split: { key: string; label: string } | null
  series: Series[]
  notes: string[]
  minimum_cell_size: number
}

export interface NotApplicable {
  chart: string
  title: string
  not_applicable: { key: string; label: string }
}

export type SeriesResponse = ChartData | NotApplicable

export function isNotApplicable(response: SeriesResponse): response is NotApplicable {
  return 'not_applicable' in response
}

export async function fetchDataCatalog(): Promise<DataCatalog> {
  const response = await apiFetch('/data/dashboards')
  if (!response.ok) throw await apiFailure(response)
  return (await response.json()) as DataCatalog
}

/** The query string for one chart: the chart, the split, then each filter. */
export function seriesQuery(chart: string, compare: string, filters: Record<string, string>): string {
  const params = new URLSearchParams({ chart })
  if (compare !== '') params.set('compare', compare)
  for (const key of Object.keys(filters).sort()) {
    if (filters[key] !== '') params.set(key, filters[key])
  }
  return params.toString()
}

export async function fetchSeries(query: string): Promise<SeriesResponse> {
  const response = await apiFetch(`/data/series?${query}`)
  if (!response.ok) throw await apiFailure(response)
  return (await response.json()) as SeriesResponse
}

// --- the person's choices ---------------------------------------------------------

export interface DataChoices {
  dashboard: string
  /** Academic years, inclusive ("2020-2021"); empty means the first / last. */
  from: string
  to: string
  compare: string
  filters: Record<string, string>
}

export const NO_CHOICES: DataChoices = { dashboard: '', from: '', to: '', compare: '', filters: {} }

const STORAGE_PREFIX = 'campuslens.data-page.v1:'

/** The choices this person last made on this browser (per account). */
export function loadChoices(account: string): DataChoices {
  try {
    const raw = window.localStorage.getItem(STORAGE_PREFIX + account)
    if (raw === null) return NO_CHOICES
    const parsed = JSON.parse(raw) as Partial<DataChoices>
    const filters: Record<string, string> = {}
    if (typeof parsed.filters === 'object' && parsed.filters !== null) {
      for (const [key, value] of Object.entries(parsed.filters)) {
        if (typeof value === 'string' && value !== '') filters[key] = value
      }
    }
    const text = (value: unknown) => (typeof value === 'string' ? value : '')
    return {
      dashboard: text(parsed.dashboard),
      from: text(parsed.from),
      to: text(parsed.to),
      compare: text(parsed.compare),
      filters,
    }
  } catch {
    return NO_CHOICES
  }
}

export function saveChoices(account: string, choices: DataChoices): void {
  try {
    window.localStorage.setItem(STORAGE_PREFIX + account, JSON.stringify(choices))
  } catch {
    // Private windows and blocked storage: the choices last this visit only.
  }
}

/** Saved choices checked against the catalog: a value the catalog no longer
 * offers (another role, a changed list) is dropped, never sent. */
export function validChoices(choices: DataChoices, catalog: DataCatalog): DataChoices {
  const dashboards = catalog.dashboards.map((d) => d.id)
  // At most one group (a chart narrows to one group or compares groups,
  // never both; the API refuses anything more).
  const filters: Record<string, string> = {}
  for (const spec of catalog.filters) {
    const value = choices.filters[spec.key]
    if (Object.keys(filters).length === 0 && value !== undefined && spec.options.some((o) => o.value === value)) {
      filters[spec.key] = value
    }
  }
  const year = (value: string) => (catalog.years.includes(value) ? value : '')
  let from = year(choices.from)
  let to = year(choices.to)
  if (from !== '' && to !== '' && catalog.years.indexOf(from) > catalog.years.indexOf(to)) {
    ;[from, to] = [to, from]
  }
  return {
    dashboard: dashboards.includes(choices.dashboard) ? choices.dashboard : (dashboards[0] ?? ''),
    from,
    to,
    // A comparison by an attribute that is also narrowed to one value is no comparison.
    compare:
      catalog.compare.some((c) => c.key === choices.compare) && Object.keys(filters).length === 0
        ? choices.compare
        : '',
    filters,
  }
}

/** The x values (and each series' points) inside the chosen years. */
export function sliceYears(data: ChartData, years: string[], from: string, to: string): ChartData {
  const first = from === '' ? 0 : years.indexOf(from)
  const last = to === '' ? years.length - 1 : years.indexOf(to)
  const inRange = (year: string) => {
    const index = years.indexOf(year)
    return index >= first && index <= last
  }
  const keep = new Set(data.x.filter((x) => inRange(x.year)).map((x) => x.key))
  return {
    ...data,
    x: data.x.filter((x) => keep.has(x.key)),
    series: data.series.map((s) => ({ ...s, points: s.points.filter((p) => keep.has(p.x)) })),
  }
}

// --- numbers -----------------------------------------------------------------------

const GROUPED = new Intl.NumberFormat('en-US')
const GROUPED_CENTS = new Intl.NumberFormat('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })

/** A value as the tooltip and the table say it: 14,589 · 81.3% · 3.07 · $1,262,868. */
export function formatValue(value: number, kind: ValueKind): string {
  switch (kind) {
    case 'count':
      return GROUPED.format(value)
    case 'pct':
    case 'pct_fit':
      return `${value.toFixed(1)}%`
    case 'gpa':
      return value.toFixed(2)
    case 'dollars':
      return `$${GROUPED_CENTS.format(value)}`
    default:
      return value.toFixed(1)
  }
}

/** An axis tick: short (1.2k, $1.3M, 80%). */
export function formatTick(value: number, kind: ValueKind): string {
  const short = (n: number) => {
    const abs = Math.abs(n)
    if (abs >= 1_000_000) return `${trim(n / 1_000_000)}M`
    if (abs >= 10_000) return `${trim(n / 1_000)}k`
    return GROUPED.format(n)
  }
  switch (kind) {
    case 'pct':
    case 'pct_fit':
      return `${trim(value)}%`
    case 'gpa':
      return value.toFixed(2)
    case 'dollars':
      return `$${short(value)}`
    case 'count':
      return short(value)
    default:
      return trim(value)
  }
}

function trim(n: number): string {
  return Number.isInteger(n) ? String(n) : String(Number(n.toFixed(1)))
}

/** Up to about five round tick values covering [min, max]. */
export function niceTicks(min: number, max: number, count = 5): number[] {
  if (!(max > min)) return [min]
  const raw = (max - min) / (count - 1)
  const power = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= raw) ?? raw
  const start = Math.floor(min / step) * step
  const ticks: number[] = []
  for (let t = start; t <= max + step * 0.5; t += step) ticks.push(Number(t.toFixed(10)))
  return ticks
}

/** The value axis for the visible points: counts, money, rates and bars
 * start at zero; averages (a GPA) fit the data with a minimum span so a flat
 * line is not drawn as a steep one, and the chart marks the broken axis. */
export function valueDomain(values: number[], kind: ValueKind, form: 'line' | 'bar'): [number, number] {
  if (values.length === 0) return [0, 1]
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  if (form === 'bar' || kind === 'count' || kind === 'dollars' || kind === 'pct') {
    return [0, hi > 0 ? hi : 1]
  }
  const minimumSpan = kind === 'gpa' ? 0.5 : kind === 'pct_fit' ? 10 : Math.max(Math.abs(hi) * 0.1, 1)
  const span = Math.max(hi - lo, minimumSpan)
  const middle = (hi + lo) / 2
  let low = middle - span / 2
  let high = middle + span / 2
  const floor = 0
  const ceiling = kind === 'gpa' ? 4 : Infinity
  if (low < floor) {
    high += floor - low
    low = floor
  }
  if (high > ceiling) {
    low -= high - ceiling
    high = ceiling
  }
  return [Math.max(low, floor), high]
}

export const WITHHELD_TEXT = 'Withheld: fewer than 10 students, or would reveal a group that small'

/** One point in words, for the tooltip, the live region and the table:
 * the measure's value with how many students it covers, or why it is not shown. */
export function describePoint(point: Point | undefined, data: Pick<ChartData, 'kind'>): string {
  if (point === undefined || point.status === 'none') return 'No figure'
  if (point.status === 'withheld' || point.value === null) return WITHHELD_TEXT
  const value = formatValue(point.value, data.kind)
  if (point.numerator !== undefined && point.denominator !== undefined && data.kind === 'pct') {
    return `${value} (${formatValue(point.numerator, 'count')} of ${formatValue(point.denominator, 'count')})`
  }
  if (point.students !== undefined && data.kind !== 'count') {
    return `${value} (${formatValue(point.students, 'count')} students)`
  }
  return value
}

// --- asking about a chart, and sending it ----------------------------------------

/** One place on a chart: a term (an index into `x`) and a group, either or
 * both left open. `{index: null, seriesKey: null}` is the whole chart. */
export interface ChartFocus {
  index: number | null
  seriesKey: string | null
}

export const WHOLE_CHART: ChartFocus = { index: null, seriesKey: null }

/** The group a chart was narrowed to with the Students choice. */
export interface ChartGroup {
  label: string
  value: string
}

/** How a question names each chart's measure: [about it, how it changed]. */
const MEASURE_PHRASES: Record<string, [string, string]> = {
  headcount: ['students enrolled', 'enrollment'],
  headcount_by_college: ['students enrolled', 'enrollment'],
  new_students: ['new students', 'the number of new students'],
  retention: ['first-year retention', 'first-year retention'],
  retention_by_pell: ['first-year retention', 'first-year retention'],
  grad4: ['the 4-year graduation rate', 'the 4-year graduation rate'],
  grad4_by_pell: ['the 4-year graduation rate', 'the 4-year graduation rate'],
  grad6: ['the 6-year graduation rate', 'the 6-year graduation rate'],
  stop_out: ['the stop-out rate', 'the stop-out rate'],
  dropout: ['the dropout rate', 'the dropout rate'],
  avg_gpa: ['average GPA', 'average GPA'],
  financial_hold_students: [
    'students with a financial hold',
    'the number of students with a financial hold',
  ],
  financial_hold_rate: [
    'the share of students with a financial hold',
    'the share of students with a financial hold',
  ],
  financial_balance_total: [
    'the total balance on financial holds',
    'the total balance on financial holds',
  ],
  financial_balance_median: [
    'the median balance on financial holds',
    'the median balance on financial holds',
  ],
  pell_share: ['the share of Pell grant recipients', 'the share of Pell grant recipients'],
  on_campus: ['the share living on campus', 'the share living on campus'],
  athletes: ['the share of athletes', 'the share of athletes'],
  part_time: ['the share studying part-time', 'the share studying part-time'],
  dfw: ['the D, F or withdrawal rate', 'the D, F or withdrawal rate'],
  probation: ['the probation rate', 'the probation rate'],
  advising: ['the share who met an advisor', 'the share who met an advisor'],
}

function measurePhrases(data: Pick<ChartData, 'chart' | 'title'>): [string, string] {
  const known = MEASURE_PHRASES[data.chart]
  if (known !== undefined) return known
  const title = data.title.length > 1 && /^[A-Z][a-z]/.test(data.title)
    ? data.title[0].toLowerCase() + data.title.slice(1)
    : data.title
  return [title, title]
}

/** The series a focus names, or the only series when there is one. */
function focusSeries(data: ChartData, focus: ChartFocus): Series | null {
  if (focus.seriesKey !== null) return data.series.find((s) => s.key === focus.seriesKey) ?? null
  return data.series.length === 1 ? data.series[0] : null
}

/** The last term with a shown figure in any series (the "latest" a card
 * leads with), or null when nothing is shown. */
export function latestShownIndex(data: ChartData): number | null {
  for (let i = data.x.length - 1; i >= 0; i -= 1) {
    if (data.series.some((s) => s.points[i]?.status === 'ok')) return i
  }
  return null
}

/** Where a question is about: the group's name (a compared group, else the
 * Students choice; never the "All students" reference) and the term. */
function focusParts(data: ChartData, group: ChartGroup | null, focus: ChartFocus) {
  const series = focusSeries(data, focus)
  const groupName =
    series !== null && series.slot !== null && data.split !== null ? series.label : (group?.value ?? null)
  const index = focus.index ?? latestShownIndex(data)
  const term = index !== null ? (data.x[index]?.label ?? null) : null
  return { series, groupName, index, term }
}

/**
 * The question "Ask about this" starts a new chat with, built from the
 * chart's measure, the group and the term. A point that fell or rose from
 * the same term a year before (or the class before) asks why ("Why did first-year retention for College of
 * Engineering and Computing drop in Fall 2024?"); otherwise it asks for more
 * ("Tell me more about students enrolled in Spring 2026 by college."). It
 * never carries a figure, so a withheld point's question reveals nothing.
 */
export function chartQuestion(data: ChartData, group: ChartGroup | null, focus: ChartFocus): string {
  const [about, change] = measurePhrases(data)
  const { series, groupName, index, term } = focusParts(data, group, focus)
  const forGroup = groupName !== null ? ` for ${groupName}` : ''
  const inTerm = term !== null ? ` in ${term}` : ''
  const bySplit =
    series === null && data.split !== null && data.series.length > 1 ? ` by ${data.split.label.toLowerCase()}` : ''
  // Compared with the same term a year before on a term chart (a fall with
  // the fall before: spring enrollment is always lower), else the class before.
  const season = (i: number) => data.x[i]?.label.split(' ')[0]
  const back = data.x_label === 'Term' && index !== null && index >= 2 && season(index - 2) === season(index) ? 2 : 1
  if (focus.index !== null && series !== null && index !== null && index >= back) {
    const point = series.points[index]
    const before = series.points[index - back]
    if (
      point?.status === 'ok' &&
      before?.status === 'ok' &&
      point.value !== null &&
      before.value !== null &&
      point.value !== before.value
    ) {
      const direction = point.value < before.value ? 'drop' : 'rise'
      return `Why did ${change}${forGroup} ${direction}${inTerm}?`
    }
  }
  return `Tell me more about ${about}${forGroup}${inTerm}${bySplit}.`
}

/** The chip a chat started from a chart carries: "About: <chart> · <group> · <term>". */
export function chartAbout(data: ChartData, group: ChartGroup | null, focus: ChartFocus): string {
  const { groupName, term } = focusParts(data, group, focus)
  return ['About: ' + data.title, groupName, term].filter((part) => part !== null).join(' · ')
}

/** The reference an alert stores for a chart (the server re-reads the
 * series from it for whoever opens the alert): the series query, then the
 * term and the group when one point was chosen. */
export function chartRef(
  chart: string,
  compare: string,
  filters: Record<string, string>,
  data: Pick<ChartData, 'x'> | null,
  focus: ChartFocus,
): string {
  const params = new URLSearchParams(seriesQuery(chart, compare, filters))
  const at = focus.index !== null && data !== null ? data.x[focus.index]?.key : undefined
  if (at !== undefined) params.set('at', at)
  if (focus.seriesKey !== null) params.set('series', focus.seriesKey)
  return params.toString()
}

/** How many legend chips to show in at most `rows` wrapping rows of
 * `available` pixels: all of them when they fit, else as many as fit
 * together with the "+N more" chip (`moreWidth`) after them, at least one.
 * Lays the chips out as flex-wrap does, from their measured widths. */
export function chipsThatFit(
  widths: number[],
  available: number,
  gap: number,
  rows: number,
  moreWidth: number,
): number {
  const lines = (items: number[]) => {
    let count = 1
    let x = 0
    for (const raw of items) {
      const width = Math.min(raw, available)
      if (x > 0 && x + gap + width > available) {
        count += 1
        x = width
      } else {
        x = x === 0 ? width : x + gap + width
      }
    }
    return count
  }
  if (lines(widths) <= rows) return widths.length
  for (let k = widths.length - 1; k > 1; k -= 1) {
    if (lines([...widths.slice(0, k), moreWidth]) <= rows) return k
  }
  return 1
}

/** Legend labels without the words every group shares ("College of Arts
 * and Sciences" -> "Arts and Sciences" when every group is a "College of"),
 * so the chips stay short; the full name stays in each chip's accessible
 * name. Unchanged when fewer than two groups share a leading phrase. */
export function shortLabels(labels: string[]): string[] {
  if (labels.length < 2) return labels
  const words = labels.map((label) => label.split(' '))
  let shared = 0
  while (
    words.every((w) => w.length > shared + 1) &&
    words.every((w) => w[shared] === words[0][shared])
  ) {
    shared += 1
  }
  return shared === 0 ? labels : words.map((w) => w.slice(shared).join(' '))
}
