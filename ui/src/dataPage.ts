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
export const DATA_PAGE_ROLES: readonly string[] = ['executive', 'aid', 'staff', 'reviewer']

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

export type ValueKind = 'count' | 'pct' | 'gpa' | 'dollars' | 'average' | 'years'

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
  const filters: Record<string, string> = {}
  for (const spec of catalog.filters) {
    const value = choices.filters[spec.key]
    if (value !== undefined && spec.options.some((o) => o.value === value)) filters[spec.key] = value
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
      catalog.compare.some((c) => c.key === choices.compare) && filters[choices.compare] === undefined
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

/** The value axis for the visible points: counts, money and bars start at
 * zero; rates and averages fit the data with a minimum span so a flat line
 * is not drawn as a steep one. */
export function valueDomain(values: number[], kind: ValueKind, form: 'line' | 'bar'): [number, number] {
  if (values.length === 0) return [0, 1]
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  if (form === 'bar' || kind === 'count' || kind === 'dollars') {
    return [0, hi > 0 ? hi : 1]
  }
  const minimumSpan = kind === 'pct' ? 10 : kind === 'gpa' ? 0.5 : Math.max(Math.abs(hi) * 0.1, 1)
  const span = Math.max(hi - lo, minimumSpan)
  const middle = (hi + lo) / 2
  let low = middle - span / 2
  let high = middle + span / 2
  const floor = 0
  const ceiling = kind === 'pct' ? 100 : kind === 'gpa' ? 4 : Infinity
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
