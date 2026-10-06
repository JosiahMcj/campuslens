// Explore: specific questions over Demonstration University, answered from
// tables that reviewed code computed (docs/EXPLORE.md). This module holds the
// response types, the role gate, and the pure helpers the answer view uses:
// linking each number in a sentence to the table cell it came from, plain
// table cells, and the session's question list. Nothing here computes a
// number; every figure on screen is a cell the API returned.

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
  return role === 'admin' || role === 'executive' || role === 'staff' || role === 'reviewer'
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
        .map((column) => ({ key: String(column.key ?? ''), label: String(column.label ?? '') }))
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

/** The honest source line, without the "(no model)" aside. */
export function sourceLabel(source: string | null): string | null {
  if (source === null) return null
  return source.replace(/\s*\(no model\)\s*$/i, '').trim()
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

/** The quiet line under the answer: the API's notes, plus the instructor
 * note when a step withheld instructor rows from this role. */
export function answerNotes(response: ExploreResponse): string[] {
  const notes = (response.notes ?? []).map(displayText)
  if (response.steps.some((step) => step.instructor_rows_withheld === true)) {
    notes.push(INSTRUCTOR_NOTE)
  }
  return [...new Set(notes)]
}

// --- Tables -----------------------------------------------------------------

/** The cell a sentence's suppressed figures read as. */
export const SUPPRESSED = 'fewer than 10'

/** The columns shown, by index: an id column is hidden when the row also
 * carries a name (instructors). */
export function visibleColumns(step: ExploreStep): number[] {
  const keys = step.table.columns.map((column) => column.key)
  return keys.flatMap((key, index) =>
    key === 'instructor' && keys.includes('name') ? [] : [index],
  )
}

/** The column a claim points at, moved off a hidden id column onto the
 * row's name. */
export function claimColumn(step: ExploreStep, column: string): string {
  const keys = step.table.columns.map((c) => c.key)
  return column === 'instructor' && keys.includes('name') ? 'name' : column
}

/** A table cell as text: whole numbers with thousands separators, negative
 * numbers with a true minus sign, an empty cell as a dash. */
export function formatCell(value: ExploreCell): string {
  if (value === null) return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') {
    const text = Number.isInteger(value)
      ? Math.abs(value).toLocaleString('en-US')
      : String(Math.abs(value))
    return value < 0 ? `−${text}` : text
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
    forms.add(magnitude.toFixed(digits))
    forms.add(
      magnitude.toLocaleString('en-US', {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      }),
    )
  }
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
