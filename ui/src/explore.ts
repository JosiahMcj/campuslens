// Explore: specific questions over Demonstration University, answered from
// tables that reviewed code computed (docs/EXPLORE.md). This module holds the
// response types, the role gate, and the pure helpers the answer view uses:
// linking each number in a sentence to the table cell it came from, plain
// table cells, and the session's question list. Nothing here computes a
// number; every figure on screen is a cell the API returned.

import { isTemplateId, type AnswerCard, type ChartSpec } from './answerCard'
import type { Role } from './auth'

/** One number (or name) in a sentence and the cell it came from. */
export interface ExploreClaim {
  table: number
  row: number
  column: string
}

export interface ExploreSentence {
  text: string
  claims: ExploreClaim[]
}

export interface ExploreColumn {
  key: string
  label: string
  /** count, gpa, pct, points, money, hours, average or text (the API's
   * column kind); absent from older answers. */
  kind?: string
}

export type ExploreCell = string | number | boolean | null

export interface ExploreStep {
  analysis_id: string
  title: string
  params_plain: string[]
  fields_read: string[]
  table: { columns: ExploreColumn[]; rows: ExploreCell[][] }
  notes: string[]
  instructor_rows_withheld?: boolean
  error?: string
}

/** POST /explore. `refused` is a refusal before planning; `message` without
 * an answer is a question no analysis answers (with `suggestions`) or an
 * answer that could not be finished. */
export interface ExploreResponse {
  refused: boolean
  /** The answer card: key points, chart template, follow-ups (answerCard.ts). */
  card?: AnswerCard
  message?: string
  answer: ExploreSentence[]
  steps: ExploreStep[]
  source: string | null
  planner?: string
  notes?: string[]
  fallbacks?: string[]
  suggestions?: string[]
}

/** GET /explore/catalog, the parts the screen uses. */
export interface ExploreCatalog {
  examples: string[]
}

/** Every role but the Financial Aid office may ask Explore questions
 * (the API answers the aid role 403). Matches ROUTE_ROLES in the API. */
export function canExplore(role: Role): boolean {
  return (
    role === 'admin' ||
    role === 'executive' ||
    role === 'staff' ||
    role === 'reviewer' ||
    role === 'finance' ||
    role === 'registrar' ||
    role === 'studentlife'
  )
}

/** The owner's example: always one of the three "Try" questions. */
export const OWNER_EXAMPLE_START = 'Which major has the lowest GPA'

/** The three example questions under the approved cards: the owner's, then
 * two short ones, in the catalog's order. */
export function pickExamples(examples: readonly string[]): string[] {
  const owner = examples.find((text) => text.startsWith(OWNER_EXAMPLE_START))
  const rest = examples.filter((text) => text !== owner)
  const short = rest.filter((text) => text.length <= 60)
  const picked = [...(owner !== undefined ? [owner] : []), ...short, ...rest]
  return [...new Set(picked)].slice(0, 3)
}

// --- Reading the response safely -------------------------------------------

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []

function cellOf(value: unknown): ExploreCell {
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return value
  }
  return null
}

function stepOf(value: unknown): ExploreStep | null {
  if (!isObject(value) || typeof value.title !== 'string') return null
  const table = isObject(value.table) ? value.table : {}
  const columns = Array.isArray(table.columns)
    ? table.columns
        .filter(isObject)
        .map((column) => ({
          key: String(column.key ?? ''),
          label: String(column.label ?? ''),
          ...(typeof column.kind === 'string' ? { kind: column.kind } : {}),
        }))
    : []
  const rows = Array.isArray(table.rows)
    ? table.rows.filter(Array.isArray).map((row) => (row as unknown[]).map(cellOf))
    : []
  return {
    analysis_id: String(value.analysis_id ?? ''),
    title: value.title,
    params_plain: strings(value.params_plain),
    fields_read: strings(value.fields_read),
    table: { columns, rows },
    notes: strings(value.notes),
    ...(value.instructor_rows_withheld === true ? { instructor_rows_withheld: true } : {}),
    ...(typeof value.error === 'string' ? { error: value.error } : {}),
  }
}

function sentenceOf(value: unknown): ExploreSentence | null {
  if (!isObject(value) || typeof value.text !== 'string') return null
  const claims = Array.isArray(value.claims)
    ? value.claims.filter(isObject).flatMap((claim) =>
        typeof claim.table === 'number' &&
        typeof claim.row === 'number' &&
        typeof claim.column === 'string'
          ? [{ table: claim.table, row: claim.row, column: claim.column }]
          : [],
      )
    : []
  return { text: value.text, claims }
}

/** The POST /explore body as typed data; anything malformed reads as empty. */
export function exploreResponseFrom(raw: unknown): ExploreResponse {
  const body = isObject(raw) ? raw : {}
  return {
    refused: body.refused === true,
    ...(typeof body.message === 'string' ? { message: body.message } : {}),
    answer: Array.isArray(body.answer)
      ? body.answer.map(sentenceOf).filter((s): s is ExploreSentence => s !== null)
      : [],
    steps: Array.isArray(body.steps)
      ? body.steps.map(stepOf).filter((s): s is ExploreStep => s !== null)
      : [],
    source: typeof body.source === 'string' ? body.source : null,
    ...(typeof body.planner === 'string' ? { planner: body.planner } : {}),
    notes: strings(body.notes),
    fallbacks: strings(body.fallbacks),
    ...(Array.isArray(body.suggestions) ? { suggestions: strings(body.suggestions) } : {}),
    ...(isObject(body.card) ? { card: cardOf(body.card) } : {}),
  }
}

/** The answer card, read defensively: an unknown chart template draws no
 * chart, never a guess. */
function cardOf(raw: Record<string, unknown>): AnswerCard {
  const sentences = (value: unknown) =>
    Array.isArray(value)
      ? value.map(sentenceOf).filter((s): s is ExploreSentence => s !== null)
      : []
  const chart = isObject(raw.chart) && isTemplateId(raw.chart.template) ? (raw.chart as unknown as ChartSpec) : null
  const follow = isObject(raw.followups) ? raw.followups : {}
  const breakdowns = Array.isArray(follow.breakdowns)
    ? follow.breakdowns.filter(
        (b): b is { grouping: string; label: string; question: string } =>
          isObject(b) &&
          typeof b.grouping === 'string' &&
          typeof b.label === 'string' &&
          typeof b.question === 'string',
      )
    : []
  return {
    key_points: sentences(raw.key_points),
    chart,
    extra_steps: Array.isArray(raw.extra_steps)
      ? raw.extra_steps.map(stepOf).filter((s): s is ExploreStep => s !== null)
      : [],
    plan: sentences(raw.plan),
    followups: {
      trend: typeof follow.trend === 'string' ? follow.trend : null,
      breakdowns,
      topic: follow.topic === 'registration' ? 'registration' : null,
    },
  }
}

// --- Words on screen --------------------------------------------------------

/** Internal ids that mean nothing to a reader: instructor ids (the names
 * stay, with their "(fictional)" mark) and, defensively, any student id. */
const INSTRUCTOR_ID = /\bI-\d{3,}\s+/g
/** A student id in any prefixed form: S-1234, S 1234, S1234, STU-12, PRI-12. */
const STUDENT_ID = /\b(?:STU|PRI)-\d+\b|\bS\s?[-_‐-―]?\s?\d{3,}\b/gi
/** In a typed question, also any run of five or more digits that is not a
 * term code (202620), as the API redacts it (explore/privacy.py). */
const TYPED_NUMBER = /\b(?!20\d\d[123]0\b)\d{5,}\b/g

/** Text as the screen shows it: no instructor ids, never a student id. */
export function displayText(text: string): string {
  return text.replace(INSTRUCTOR_ID, '').replace(STUDENT_ID, 'a student')
}

/** A typed question, safe to keep in the session's list. */
export function redactQuestion(question: string): string {
  return question
    .replace(STUDENT_ID, 'a student')
    .replace(TYPED_NUMBER, 'a number')
    .replace(/\s+/g, ' ')
    .trim()
}

/** The honest source line: "Calculated directly from the records", or
 * "Written by the Chief of Staff from the records" when the model reworded
 * the computed answer. Older wordings read the same way. */
export function sourceLabel(source: string | null): string | null {
  if (source === null) return null
  const text = source.replace(/\s*\(no model\)\s*$/i, '').trim()
  if (/^Written from computed tables$/i.test(text)) return 'Calculated directly from the records'
  return text.replace(/\bfrom computed tables$/i, 'from the records')
}

/** How the plan was made, for the "About this answer" detail. */
export function plannerLabel(planner: string | undefined): string | null {
  switch (planner) {
    case 'rule':
      return 'Planned by fixed rules that match the question to the approved analyses.'
    case 'model':
      return 'Planned by the Chief of Staff, then checked against the approved analyses.'
    case 'recorded':
      return 'Planned from a recorded run of the Chief of Staff, checked against the approved analyses.'
    default:
      return null
  }
}

export const INSTRUCTOR_NOTE = 'Instructor results are shown to the executive and admin only.'

/** True when a sentence of the answer already says who sees instructors. */
function saysInstructorRule(response: ExploreResponse): boolean {
  return response.answer.some((sentence) => /executive and admin/i.test(sentence.text))
}

/** The quiet line under the answer: the API's notes, plus the instructor
 * note when a step withheld instructor rows from this role and no sentence
 * says so already (the rule is said once). */
export function answerNotes(response: ExploreResponse): string[] {
  const notes = (response.notes ?? []).map(displayText)
  if (
    response.steps.some((step) => step.instructor_rows_withheld === true) &&
    !saysInstructorRule(response)
  ) {
    notes.push(INSTRUCTOR_NOTE)
  }
  return [...new Set(notes)]
}

/**
 * One label per thing read: a first and a last name read as one "name"
 * ("Instructor name (fictional)"), and "X name" beside "X" ("Major name"
 * beside "Major") is the same thing named twice. Every other field read
 * keeps its own label ("Major each term", "Term of each section").
 */
export function dedupeLabels(labels: readonly string[]): string[] {
  const merged = labels.map((label) => label.replace(/\b(?:first|last) name\b/i, 'name'))
  const unique = [...new Set(merged)]
  const lower = new Set(unique.map((label) => label.toLowerCase()))
  return unique.filter((label) => {
    const named = /^(.+) name$/i.exec(label)
    return named === null || !lower.has(named[1].toLowerCase())
  })
}

// --- Tables -----------------------------------------------------------------

/** The cell a sentence's suppressed figures read as. */
export const SUPPRESSED = 'fewer than 10'

/** Code columns and the name column a reader sees instead: an instructor
 * id, a term code (202120), a major or college code. Each is hidden when its
 * name is in the same table. */
const CODE_COLUMNS: Record<string, string> = {
  instructor: 'name',
  term: 'term_name',
  prior_term: 'prior_term_name',
  major: 'major_name',
  college: 'college_name',
}

function nameTwin(keys: readonly string[], key: string): string | null {
  const twin = CODE_COLUMNS[key]
  return twin !== undefined && keys.includes(twin) ? twin : null
}

/** The columns shown, by index: a code column is hidden when the row also
 * carries its name (instructors, terms, majors, colleges), and the column
 * that tells the rows apart (the first text column whose cells differ: the
 * term in a course's trend, not the course repeated on every row) comes
 * first, so it can stay in view while a narrow table scrolls sideways. */
export function visibleColumns(step: ExploreStep): number[] {
  const keys = step.table.columns.map((column) => column.key)
  const shown = keys.flatMap((key, index) => (nameTwin(keys, key) !== null ? [] : [index]))
  const rows = step.table.rows
  const label = shown.find((index) => {
    const cells = rows.map((row) => row[index] ?? null)
    return (
      cells.every((cell) => typeof cell === 'string') &&
      (rows.length < 2 || new Set(cells).size > 1)
    )
  })
  return label === undefined ? shown : [label, ...shown.filter((index) => index !== label)]
}

/** The column a claim points at, moved off a hidden code column onto the
 * row's name. */
export function claimColumn(step: ExploreStep, column: string): string {
  const keys = step.table.columns.map((c) => c.key)
  return nameTwin(keys, column) ?? column
}

/** The columns a table shows beside an answer: the row label first, then
 * the columns the answer quotes, in the order it quotes them. The rest wait
 * behind "Show all columns" (`hidden`). A step the answer does not quote,
 * or quotes only by its row label, shows every column. */
export function answerColumns(
  step: ExploreStep,
  quoted: readonly string[],
  showAll: boolean,
): { columns: number[]; hidden: number } {
  const all = visibleColumns(step)
  if (all.length === 0) return { columns: all, hidden: 0 }
  const keys = step.table.columns.map((column) => column.key)
  const [label, ...rest] = all
  const picked: number[] = []
  for (const key of quoted) {
    const index = keys.indexOf(claimColumn(step, key))
    if (index >= 0 && index !== label && rest.includes(index) && !picked.includes(index)) {
      picked.push(index)
    }
  }
  if (picked.length === 0) return { columns: all, hidden: 0 }
  const others = rest.filter((index) => !picked.includes(index))
  return showAll || others.length === 0
    ? { columns: [label, ...picked, ...others], hidden: 0 }
    : { columns: [label, ...picked], hidden: others.length }
}

/** The column keys a step's answer quotes, in the order the sentences
 * quote them. */
export function quotedColumns(response: ExploreResponse, table: number): string[] {
  const keys: string[] = []
  for (const sentence of response.answer) {
    for (const claim of sentence.claims) {
      if (claim.table === table && !keys.includes(claim.column)) keys.push(claim.column)
    }
  }
  return keys
}

/** Decimals a number of each column kind is shown with: a GPA with two, a
 * rate or a share with one, a count with none. Other kinds (averages, hours,
 * years, money) keep the API's own decimals, which the sentences quote. */
const KIND_DIGITS: Record<string, number> = {
  gpa: 2,
  pct: 1,
  points: 1,
  count: 0,
}

/** A table cell as text: thousands separators, a GPA with two decimals and
 * a rate with one (by the column's kind), negative numbers with a true minus
 * sign, an empty cell as a dash. */
export function formatCell(value: ExploreCell, kind?: string): string {
  if (value === null) return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') {
    const magnitude = Math.abs(value)
    const digits = kind !== undefined ? KIND_DIGITS[kind] : undefined
    let text: string
    if (digits !== undefined && !(kind === 'count' && !Number.isInteger(value))) {
      const rounded = roundHalfUp(magnitude, digits)
      const [whole, fraction] = rounded.split('.')
      text = Number(whole).toLocaleString('en-US') + (fraction !== undefined ? `.${fraction}` : '')
    } else {
      text = magnitude.toLocaleString('en-US', {
        maximumFractionDigits: kind === undefined ? 3 : 2,
      })
    }
    return value < 0 && /[1-9]/.test(text) ? `−${text}` : text
  }
  return displayText(value)
}

/** The DOM id of one table cell, so a number in a sentence can find it. */
export function cellDomId(answerKey: string, table: number, row: number, column: string): string {
  return `explore-${answerKey}-t${table}-r${row}-${column.replace(/[^A-Za-z0-9_-]/g, '')}`
}

// --- Linking numbers in a sentence to their cells ---------------------------

export type SentencePart =
  | { text: string }
  | { text: string; claim: ExploreClaim }

/**
 * A non-negative number rounded half up to `digits` decimals on its decimal
 * text, as the API rounds it for a sentence (explore/answer.py
 * reader_number): 2.615 -> "2.62", where `toFixed` gives the binary float's
 * "2.61".
 */
export function roundHalfUp(magnitude: number, digits: number): string {
  const text = String(magnitude)
  if (!/^\d+(\.\d+)?$/.test(text)) return magnitude.toFixed(digits)
  const [whole, fraction = ''] = text.split('.')
  if (fraction.length <= digits) {
    return digits === 0 ? whole : `${whole}.${fraction.padEnd(digits, '0')}`
  }
  // Integer arithmetic on the kept digits, plus one when the next digit is 5+.
  const kept = BigInt(whole + fraction.slice(0, digits)) + (Number(fraction[digits]) >= 5 ? 1n : 0n)
  const padded = kept.toString().padStart(digits + 1, '0')
  return digits === 0
    ? padded
    : `${padded.slice(0, padded.length - digits)}.${padded.slice(padded.length - digits)}`
}

/** The ways the API writes one cell into a sentence, longest first. */
function candidates(value: ExploreCell): string[] {
  if (value === null || typeof value === 'boolean') return []
  if (typeof value === 'string') {
    const shown = displayText(value).trim()
    return shown.length > 0 ? [shown] : []
  }
  const magnitude = Math.abs(value)
  const forms = new Set<string>([String(magnitude), magnitude.toLocaleString('en-US')])
  for (const digits of [1, 2, 3]) {
    forms.add(roundHalfUp(magnitude, digits))
    forms.add(
      magnitude.toLocaleString('en-US', {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      }),
    )
  }
  // A percentage of 100 or more reads as a whole number ("grew 111%").
  if (magnitude >= 100 && !Number.isInteger(magnitude)) forms.add(roundHalfUp(magnitude, 0))
  const out: string[] = []
  for (const form of forms) {
    const signed = value < 0 ? [`−${form}`, `-${form}`] : [form]
    for (const text of signed) {
      out.push(`$${text}`, `${text} %`, `${text}%`, `${text} points`, text)
    }
  }
  return [...new Set(out)].sort((a, b) => b.length - a.length)
}

/** True when `text` at [start, end) is not part of a longer number. */
function bounded(text: string, start: number, end: number): boolean {
  // "1.5" inside "41.5" or "2,500": a digit (or a digit and a separator)
  // on either side means the match is part of a longer number.
  const before = text.charAt(start - 1)
  const after = text.charAt(end)
  if (/[0-9]/.test(before)) return false
  // A positive value never matches the digits of a negative number.
  if (/[−-]/.test(before) && !/^[−-]/.test(text.slice(start))) return false
  if (/[.,]/.test(before) && /[0-9]/.test(text.charAt(start - 2))) return false
  if (/[0-9]/.test(after)) return false
  if (/[.,]/.test(after) && /[0-9]/.test(text.charAt(end + 1))) return false
  return true
}

/** The cell value a claim points at, or undefined when it is out of range. */
export function claimValue(steps: readonly ExploreStep[], claim: ExploreClaim): ExploreCell | undefined {
  const step = steps[claim.table]
  if (step === undefined) return undefined
  const index = step.table.columns.findIndex((column) => column.key === claim.column)
  const row = step.table.rows[claim.row]
  if (index < 0 || row === undefined) return undefined
  return row[index]
}

/**
 * The sentence split into plain text and links: each claim's cell is found
 * in the text (the first place not already taken) and becomes a link to
 * that cell. A claim that cannot be found leaves the text as it is; the
 * sentence is never dropped or changed.
 */
export function linkSentence(
  sentence: ExploreSentence,
  steps: readonly ExploreStep[],
): SentencePart[] {
  const text = displayText(sentence.text)
  const taken: { start: number; end: number; claim: ExploreClaim }[] = []
  const free = (start: number, end: number) =>
    taken.every((range) => end <= range.start || start >= range.end)
  for (const claim of sentence.claims) {
    const step = steps[claim.table]
    if (step === undefined) continue
    // A claim on a hidden id column is found (and opened) by the row's name.
    const target = { ...claim, column: claimColumn(step, claim.column) }
    const value = claimValue(steps, target)
    if (value === undefined) continue
    const forms =
      value === SUPPRESSED ? [`withheld (${SUPPRESSED} students)`, SUPPRESSED] : candidates(value)
    let found: { start: number; end: number } | null = null
    for (const form of forms) {
      let from = 0
      while (found === null) {
        const start = text.indexOf(form, from)
        if (start < 0) break
        const end = start + form.length
        const fits =
          typeof value === 'number'
            ? bounded(text, start, end)
            : !/[A-Za-z0-9]/.test(text.charAt(start - 1)) && !/[A-Za-z0-9]/.test(text.charAt(end))
        if (free(start, end) && fits) {
          found = { start, end }
        }
        from = start + 1
      }
      if (found !== null) break
    }
    if (found !== null) taken.push({ ...found, claim: target })
  }
  taken.sort((a, b) => a.start - b.start)
  const parts: SentencePart[] = []
  let cursor = 0
  for (const range of taken) {
    if (range.start > cursor) parts.push({ text: text.slice(cursor, range.start) })
    parts.push({ text: text.slice(range.start, range.end), claim: range.claim })
    cursor = range.end
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor) })
  return parts
}

// --- The session's Explore questions -----------------------------------------

/** Answers stay in memory; the questions survive a reload in this tab, so
 * the sidebar still lists them (opening one asks it again). */
const HISTORY_LIMIT = 20

function historyKey(userId: number): string {
  return `cabinet.explore.questions.${userId}`
}

export function loadExploreHistory(userId: number): string[] {
  try {
    const raw = window.sessionStorage.getItem(historyKey(userId))
    if (raw === null) return []
    return strings(JSON.parse(raw)).slice(-HISTORY_LIMIT)
  } catch {
    return []
  }
}

export function saveExploreHistory(userId: number, questions: readonly string[]): void {
  try {
    window.sessionStorage.setItem(
      historyKey(userId),
      JSON.stringify(questions.slice(-HISTORY_LIMIT)),
    )
  } catch {
    // Storage blocked (a private window): the list lasts for this page only.
  }
}

/** Add a question to the list (moved to the end when asked again). */
export function withQuestion(questions: readonly string[], question: string): string[] {
  const safe = redactQuestion(question)
  return [...questions.filter((item) => item !== safe), safe].slice(-HISTORY_LIMIT)
}

// --- The live trace (POST /explore/stream) ----------------------------------
//
// While a question is answered the API reports each stage as it really
// happens. Events carry plain words and figures already computed and
// suppressed; never a student row, an id, or the question's text.

export type ExploreTraceEvent =
  | { type: 'planning'; text: string }
  | { type: 'understood'; text: string }
  | { type: 'plan'; steps: string[]; planner: 'rules' | 'model' }
  | { type: 'reading'; text: string }
  | { type: 'step'; index: number; total: number; title: string }
  | { type: 'suppression'; count: number; text: string }
  | { type: 'writing'; text: string }
  | { type: 'verifying'; checked: number; matched: number; text: string }

/** One line of the trace as the screen shows it. */
export interface TraceLine {
  /** Stable per line (its position): the screen animates only new lines. */
  key: string
  text: string
  /** True for a step line, the only lines a screen reader hears. */
  announce: boolean
}

const TRACE_TYPES = new Set([
  'planning',
  'understood',
  'plan',
  'reading',
  'step',
  'suppression',
  'writing',
  'verifying',
])

/** A stream event as a trace event, or null for anything else (an unknown
 * type, a malformed field): the trace shows only what it understands. */
export function traceEventFrom(raw: unknown): ExploreTraceEvent | null {
  if (typeof raw !== 'object' || raw === null) return null
  const event = raw as Record<string, unknown>
  const type = event.type
  if (typeof type !== 'string' || !TRACE_TYPES.has(type)) return null
  const text = typeof event.text === 'string' ? event.text : ''
  switch (type) {
    case 'plan': {
      const steps = Array.isArray(event.steps)
        ? event.steps.filter((s): s is string => typeof s === 'string')
        : []
      return { type, steps, planner: event.planner === 'model' ? 'model' : 'rules' }
    }
    case 'step': {
      const index = typeof event.index === 'number' ? event.index : 0
      const total = typeof event.total === 'number' ? event.total : 1
      const title = typeof event.title === 'string' ? event.title : ''
      return { type, index, total, title }
    }
    case 'suppression':
      return { type, count: typeof event.count === 'number' ? event.count : 0, text }
    case 'verifying':
      return {
        type,
        checked: typeof event.checked === 'number' ? event.checked : 0,
        matched: typeof event.matched === 'number' ? event.matched : 0,
        text,
      }
    default:
      return text ? ({ type, text } as ExploreTraceEvent) : null
  }
}

/** The trace as short lines, in the order the events arrived. */
export function traceLines(events: readonly ExploreTraceEvent[]): TraceLine[] {
  const lines: TraceLine[] = []
  for (const event of events) {
    let text: string
    let announce = false
    switch (event.type) {
      case 'planning':
        text = 'Reading your question'
        break
      case 'understood':
        text = `Understood: ${event.text}`
        break
      case 'plan':
        text =
          event.steps.length === 1
            ? 'Chose 1 approved analysis'
            : `Chose ${event.steps.length} approved analyses, one after another`
        break
      case 'step':
        text =
          event.total > 1
            ? `Step ${event.index + 1} of ${event.total}: ${event.title}`
            : `Computing: ${event.title}`
        announce = true
        break
      case 'verifying':
        text =
          event.checked === event.matched
            ? event.text
            : `Checked ${event.checked} numbers; ${event.matched} matched the tables`
        break
      default:
        text = event.text
    }
    lines.push({ key: String(lines.length), text, announce })
  }
  return lines
}

/** "Thought for 6 s · 4 steps": the collapsed trace above an answer. */
export function traceSummary(elapsedMs: number, lineCount: number): string {
  const seconds = Math.max(1, Math.round(elapsedMs / 1000))
  return `Thought for ${seconds} s · ${lineCount} ${lineCount === 1 ? 'step' : 'steps'}`
}

/** A line's text split into words and numbers ("38,374 students"), so a
 * number can count up the first time it appears. */
export function numberParts(text: string): Array<{ text: string; value: number | null }> {
  const parts: Array<{ text: string; value: number | null }> = []
  const pattern = /\d{1,3}(?:,\d{3})+|\d+/g
  let last = 0
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0
    // A year or a term code is a name, not a figure to count.
    const isYear = /^(?:19|20)\d\d$/.test(match[0])
    if (start > last) parts.push({ text: text.slice(last, start), value: null })
    parts.push({ text: match[0], value: isYear ? null : Number(match[0].replace(/,/g, '')) })
    last = start + match[0].length
  }
  if (last < text.length) parts.push({ text: text.slice(last), value: null })
  return parts
}

/** Lines of the trace that arrive together enter this far apart. */
export const TRACE_STAGGER_MS = 60

/** Motion is off under the system setting and the app's own (html.reduce-motion). */
export function motionAllowed(): boolean {
  if (typeof window === 'undefined') return false
  if (document.documentElement.classList.contains('reduce-motion')) return false
  return !(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false)
}
