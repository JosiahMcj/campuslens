// The answer card under every Explore answer: its key points, the chart
// template the server chose (a fixed registry; nothing is drawn by a model),
// the follow-up questions and the proposed plan. The server computes every
// figure and claim (backend/src/cabinet/explore/card.py); this module only
// turns the chosen template and the table cells into what a renderer draws.
// A withheld cell is never a number here: it is a gap.

import type { ChartData, Series, ValueKind } from './dataPage'
import {
  formatCell,
  SUPPRESSED,
  type ExploreCell,
  type ExploreClaim,
  type ExploreResponse,
  type ExploreSentence,
  type ExploreStep,
} from './explore'

/** The fixed chart templates (CHART_TEMPLATES in card.py). */
export const TEMPLATE_IDS = [
  'ranking_bar',
  'trend_line',
  'grouped_bars',
  'kpi_number',
  'share_bar',
  'before_after',
  'funnel',
  'small_multiples',
] as const
export type TemplateId = (typeof TEMPLATE_IDS)[number]

/** Each template's name above its chart. */
export const TEMPLATE_TITLES: Record<TemplateId, string> = {
  ranking_bar: 'Ranking',
  trend_line: 'Trend',
  grouped_bars: 'Comparison',
  kpi_number: 'Key figure',
  share_bar: 'Share of the whole',
  before_after: 'Before and after',
  funnel: 'Funnel',
  small_multiples: 'Trend for each group',
}

export function isTemplateId(value: unknown): value is TemplateId {
  return typeof value === 'string' && (TEMPLATE_IDS as readonly string[]).includes(value)
}

export interface ChartSpec {
  template: TemplateId
  form: 'bar' | 'line' | 'grouped' | 'number'
  table: number
  value: string
  value_label: string
  kind: string
  label: string[]
  series: string | null
  reference_row: number | null
  trend?: {
    table: number
    now_row: number
    then_row: number | null
    change_table: number | null
    change_row: number
    direction: 'up' | 'down' | 'unchanged' | null
    then_label?: string
  }
  before_after?: {
    before_label: string
    before: string
    after_label: string
    after: string
    change: string
  }
}

export interface Breakdown {
  grouping: string
  label: string
  question: string
}

export interface CardFollowups {
  trend: string | null
  breakdowns: Breakdown[]
  topic: 'registration' | null
}

export interface AnswerCard {
  key_points: ExploreSentence[]
  chart: ChartSpec | null
  /** Tables the card added (the trend read, Key figures); a claim's table
   * index counts the answer's steps first, then these. */
  extra_steps: ExploreStep[]
  plan: ExploreSentence[]
  followups: CardFollowups
}

/** The answer's steps and the card's own tables, in claim order. */
export function cardSteps(response: ExploreResponse & { card?: AnswerCard }): ExploreStep[] {
  return [...response.steps, ...(response.card?.extra_steps ?? [])]
}

// --- chart models --------------------------------------------------------------

export interface Mark {
  key: string
  label: string
  /** null when withheld (or not recorded): drawn as a gap, never a zero. */
  value: number | null
  withheld: boolean
  claim: ExploreClaim | null
}

export interface Delta {
  direction: 'up' | 'down' | 'unchanged'
  change: number | null
  changePct: number | null
  since: string
}

export type ChartModel =
  | { template: 'ranking_bar'; title: string; kind: string; bars: Mark[]; reference: Mark | null }
  | { template: 'trend_line'; title: string; data: ChartData; delta: Delta | null }
  | { template: 'grouped_bars'; title: string; data: ChartData }
  | {
      template: 'small_multiples'
      title: string
      kind: string
      panels: { label: string; points: Mark[] }[]
    }
  | {
      template: 'kpi_number'
      title: string
      kind: string
      value: Mark
      scope: string | null
      delta: Delta | null
    }
  | { template: 'share_bar'; title: string; kind: string; parts: Mark[]; total: number }
  | {
      template: 'before_after'
      title: string
      kind: string
      before: Mark
      after: Mark
      changePct: number | null
    }
  | { template: 'funnel'; title: string; kind: string; stages: Mark[] }

/** The value kind a DataChart axis understands. */
export function axisKind(kind: string): ValueKind {
  if (kind === 'pct' || kind === 'gpa' || kind === 'count') return kind
  if (kind === 'money') return 'dollars'
  if (kind === 'hours') return 'count'
  return 'average'
}

export function formatMark(mark: Mark, kind: string): string {
  if (mark.withheld) return `withheld (${SUPPRESSED} students)`
  if (mark.value === null) return 'not recorded'
  const text = formatCell(mark.value, kind)
  if (kind === 'pct') return `${text}%`
  if (kind === 'money') return `$${text}`
  return text
}

function columnIndex(step: ExploreStep, key: string): number {
  return step.table.columns.findIndex((column) => column.key === key)
}

function cell(step: ExploreStep, row: number, key: string): ExploreCell {
  const index = columnIndex(step, key)
  return index < 0 ? null : (step.table.rows[row]?.[index] ?? null)
}

function mark(step: ExploreStep, table: number, row: number, valueKey: string, label: string): Mark {
  const value = cell(step, row, valueKey)
  const withheld = value === SUPPRESSED
  return {
    key: `${row}`,
    label,
    value: typeof value === 'number' ? value : null,
    withheld,
    claim: typeof value === 'number' || withheld ? { table, row, column: valueKey } : null,
  }
}

function rowLabel(step: ExploreStep, row: number, keys: readonly string[]): string {
  return keys
    .map((key) => cell(step, row, key))
    .filter((value): value is string | number => value !== null && value !== '')
    .map(String)
    .join(', ')
}

function deltaFrom(
  tables: ExploreStep[],
  spec: ChartSpec,
): Delta | null {
  const trend = spec.trend
  if (trend === undefined || trend.change_table === null || trend.then_row === null) return null
  const key = tables[trend.change_table]
  const source = tables[trend.table]
  if (key === undefined || source === undefined) return null
  const change = cell(key, trend.change_row, 'change')
  const pct = cell(key, trend.change_row, 'change_pct')
  return {
    direction: trend.direction ?? 'unchanged',
    change: typeof change === 'number' ? change : null,
    changePct: typeof pct === 'number' ? pct : null,
    since: trend.then_label ?? String(cell(source, trend.then_row, 'term_name') ?? 'a year earlier'),
  }
}

/** The figure each row is about, excluding the whole (the reference). */
function bodyRows(step: ExploreStep, spec: ChartSpec): number[] {
  return step.table.rows.map((_, row) => row).filter((row) => row !== spec.reference_row)
}

function toChartData(
  title: string,
  form: 'line' | 'bar',
  step: ExploreStep,
  spec: ChartSpec,
): ChartData {
  const rows = bodyRows(step, spec)
  const xKeys = spec.label.filter((key) => key !== spec.series)
  const xs: { key: string; label: string; year: string }[] = []
  for (const row of rows) {
    const label = rowLabel(step, row, xKeys)
    if (!xs.some((x) => x.key === label)) xs.push({ key: label, label, year: '' })
  }
  const seriesNames: string[] = []
  if (spec.series !== null) {
    for (const row of rows) {
      const name = String(cell(step, row, spec.series) ?? '')
      if (!seriesNames.includes(name)) seriesNames.push(name)
    }
  } else {
    seriesNames.push(spec.value_label)
  }
  const series: Series[] = seriesNames.map((name, slot) => ({
    key: name,
    label: name,
    slot,
    points: xs.map((x) => {
      const row = rows.find(
        (r) =>
          rowLabel(step, r, xKeys) === x.key &&
          (spec.series === null || String(cell(step, r, spec.series)) === name),
      )
      if (row === undefined) return { x: x.key, value: null, status: 'none' as const }
      const value = cell(step, row, spec.value)
      if (value === SUPPRESSED) return { x: x.key, value: null, status: 'withheld' as const }
      return typeof value === 'number'
        ? { x: x.key, value, status: 'ok' as const }
        : { x: x.key, value: null, status: 'none' as const }
    }),
  }))
  return {
    chart: `answer-${spec.table}`,
    title,
    form,
    kind: axisKind(spec.kind),
    value_label: spec.value_label,
    definition: '',
    x_label: xKeys.length > 0 ? 'Group' : 'Term',
    x: xs,
    split: null,
    series,
    notes: [],
    minimum_cell_size: 10,
  }
}

/** What the chosen template draws, from the answer's tables. Null when the
 * answer has no chart or the spec does not match its tables. */
export function chartModel(
  response: ExploreResponse & { card?: AnswerCard },
): ChartModel | null {
  const spec = response.card?.chart ?? null
  if (spec === null || !isTemplateId(spec.template)) return null
  const tables = cardSteps(response)
  const step = tables[spec.table]
  if (step === undefined || columnIndex(step, spec.value) < 0) return null
  const title = spec.value_label.replace(/ \(%\)$/, '')
  const rows = bodyRows(step, spec)
  const reference =
    spec.reference_row !== null ? mark(step, spec.table, spec.reference_row, spec.value, 'All students') : null
  switch (spec.template) {
    case 'ranking_bar': {
      const bars = rows
        .map((row) => mark(step, spec.table, row, spec.value, rowLabel(step, row, spec.label)))
        .filter((bar) => bar.value !== null || bar.withheld)
      bars.sort((a, b) => (b.value ?? -Infinity) - (a.value ?? -Infinity))
      return { template: 'ranking_bar', title, kind: spec.kind, bars, reference }
    }
    case 'trend_line':
      return {
        template: 'trend_line',
        title,
        data: toChartData(title, 'line', step, spec),
        delta: deltaFrom(tables, spec),
      }
    case 'grouped_bars':
      return { template: 'grouped_bars', title, data: toChartData(title, 'bar', step, spec) }
    case 'small_multiples': {
      const data = toChartData(title, 'line', step, spec)
      return {
        template: 'small_multiples',
        title,
        kind: spec.kind,
        panels: data.series.slice(0, 6).map((series) => ({
          label: series.label,
          points: series.points.map((point, index) => ({
            key: point.x,
            label: data.x[index]?.label ?? point.x,
            value: point.status === 'ok' ? point.value : null,
            withheld: point.status === 'withheld',
            claim: null,
          })),
        })),
      }
    }
    case 'kpi_number': {
      const scope = cell(step, 0, 'scope')
      return {
        template: 'kpi_number',
        title,
        kind: spec.kind,
        value: mark(step, spec.table, 0, spec.value, title),
        scope: typeof scope === 'string' ? scope : null,
        delta: deltaFrom(tables, spec),
      }
    }
    case 'share_bar': {
      const parts = rows.map((row) =>
        mark(step, spec.table, row, spec.value, rowLabel(step, row, spec.label)),
      )
      const total = reference?.value ?? parts.reduce((sum, part) => sum + (part.value ?? 0), 0)
      return { template: 'share_bar', title, kind: spec.kind, parts, total }
    }
    case 'before_after': {
      const pair = spec.before_after
      if (pair === undefined) return null
      const change = cell(step, 0, pair.change)
      return {
        template: 'before_after',
        title,
        kind: 'count',
        before: mark(step, spec.table, 0, pair.before, String(cell(step, 0, pair.before_label) ?? 'Before')),
        after: mark(step, spec.table, 0, pair.after, String(cell(step, 0, pair.after_label) ?? 'After')),
        changePct: typeof change === 'number' ? change : null,
      }
    }
    case 'funnel':
      return {
        template: 'funnel',
        title,
        kind: spec.kind,
        stages: rows.map((row) =>
          mark(step, spec.table, row, spec.value, rowLabel(step, row, spec.label)),
        ),
      }
  }
}

/** The quote an alert carries: the answer's sentences, then its key points
 * (the inbox keeps at most six). */
export function alertQuote(response: ExploreResponse & { card?: AnswerCard }): string[] {
  const lines = [
    ...response.answer.map((sentence) => sentence.text),
    ...(response.card?.key_points ?? []).map((point) => point.text),
  ]
  return lines.slice(0, 6)
}

/** "Make a plan": the briefing's follow-up question when the answer is about
 * registration and the role asks briefing questions; otherwise the card's
 * own proposed plan is shown. */
export function planQuestion(card: AnswerCard | undefined, asksBriefing: boolean): string | null {
  return asksBriefing && card?.followups.topic === 'registration'
    ? 'Create a seven-day action plan for Enrollment and Student Success.'
    : null
}

export interface AnswerActionsAllowed {
  send: boolean
  trend: boolean
  breakdown: boolean
  plan: boolean
  evidence: boolean
}

/** Which buttons a role sees under an answer. Explore roles may re-ask; the
 * reviewer audits and does not plan; a role that may not send sees no Send. */
export function answerActions(
  role: string,
  card: AnswerCard | undefined,
  canSend: boolean,
): AnswerActionsAllowed {
  return {
    send: canSend,
    trend: card?.followups.trend != null,
    breakdown: (card?.followups.breakdowns.length ?? 0) > 0,
    plan: role !== 'reviewer' && role !== 'it' && card !== undefined && (card.plan.length > 0 || card.followups.topic === 'registration'),
    evidence: true,
  }
}
