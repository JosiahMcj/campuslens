// The display labels for the findings (COMMON.md). The backend titles stay
// unchanged, because the AI employees' recorded inputs depend on them; the
// screen shows these instead, and never the bare M-codes.

export const FINDING_LABELS: Record<string, string> = {
  M1: 'Spring registration vs. same point last year',
  M2: 'Continuing students not yet registered',
  M3: 'Not yet registered, with a hold under $1,000',
  M4: 'Not yet registered, no advising appointment this term',
  M5: 'Unresolved holds by office',
  M6: 'Days until registration closes',
  M7: 'Registered credit hours vs. last year',
  M8: 'Students with one or more support indicators',
  M9: 'Students not yet registered who have had counseling contact (aggregate)',
}

/** One plain sentence saying what each figure counts, for the evidence panel. */
export const FINDING_DEFINITIONS: Record<string, string> = {
  M1: 'How many continuing students have registered for spring so far, compared with how many had registered by the same date last year.',
  M2: 'Students who are eligible to continue but have not registered for spring yet.',
  M3: 'Of the students not yet registered, those with an unresolved financial hold under the threshold.',
  M4: 'Of the students not yet registered, those with no advising appointment since this term began.',
  M5: 'Every unresolved hold on a current student, counted by the office responsible for it.',
  M6: 'Whole calendar days from the data date until spring registration closes.',
  M7: 'Credit hours registered so far, compared with the same date last year.',
  M8: 'Students for whom at least one support indicator applies. Each indicator is shown separately and they are never added into a score.',
  M9: 'Students not yet registered who have had any counseling contact this term, as one count with no list of students behind it.',
}

/** The display label for a finding id, falling back to the backend title. */
export function findingLabel(id: string, fallbackTitle?: string): string {
  return FINDING_LABELS[id] ?? fallbackTitle ?? 'This figure'
}

export function findingDefinition(id: string): string | null {
  return FINDING_DEFINITIONS[id] ?? null
}

/** The figures as labels in a sentence: "A, B and C". */
export function findingList(ids: readonly string[]): string {
  const labels = ids.map((id) => findingLabel(id))
  if (labels.length <= 1) return labels.join('')
  return `${labels.slice(0, -1).join('; ')} and ${labels[labels.length - 1]}`
}

/** The comparable core of a number: "−4.8 %" and "4.8%" both give "4.8". */
function numericCore(text: string): string {
  return text.replace(/[−\-+$,%\s]/g, '')
}

const NUMBER_TOKEN = /\$?\d[\d,]*(?:\.\d+)?(?:\s?%)?/g

export type ClaimPart =
  | { kind: 'text'; text: string }
  | { kind: 'link'; text: string; findingId: string }

/**
 * Split a model-written sentence so each cited figure's NUMBER becomes its
 * evidence link. A finding whose whole display text appears (e.g. "28
 * unresolved holds") is matched first; otherwise the first number with the
 * same digits (so "4.8%" matches "−4.8 %"). Matches never overlap. Returns
 * the parts plus the ids that matched nothing in the text, which the caller
 * links separately.
 */
export function linkClaimNumbers(
  text: string,
  citations: readonly { id: string; display: string }[],
): { parts: ClaimPart[]; unmatched: string[] } {
  const taken: { start: number; end: number; id: string }[] = []
  const overlaps = (start: number, end: number) =>
    taken.some((span) => start < span.end && end > span.start)
  const pending: { id: string; display: string }[] = []
  // Pass 1: the whole display phrase, when it has words in it.
  for (const citation of citations) {
    const display = citation.display.trim()
    if (display === '' || display === '--' || !/[a-z]/i.test(display)) {
      pending.push(citation)
      continue
    }
    const at = text.indexOf(display)
    if (at >= 0 && !overlaps(at, at + display.length)) {
      taken.push({ start: at, end: at + display.length, id: citation.id })
    } else {
      pending.push(citation)
    }
  }
  // Pass 2: the first number with the same digits.
  const unmatched: string[] = []
  for (const citation of pending) {
    const core = numericCore(citation.display.replace(/[a-z].*$/i, ''))
    let found = false
    if (core !== '' && core !== '--') {
      for (const match of text.matchAll(NUMBER_TOKEN)) {
        const start = match.index ?? 0
        const token = match[0].replace(/\s+$/, '')
        const end = start + token.length
        if (numericCore(token) === core && !overlaps(start, end)) {
          taken.push({ start, end, id: citation.id })
          found = true
          break
        }
      }
    }
    if (!found) unmatched.push(citation.id)
  }
  taken.sort((a, b) => a.start - b.start)
  const parts: ClaimPart[] = []
  let cursor = 0
  for (const span of taken) {
    if (span.start > cursor) parts.push({ kind: 'text', text: text.slice(cursor, span.start) })
    parts.push({ kind: 'link', text: text.slice(span.start, span.end), findingId: span.id })
    cursor = span.end
  }
  if (cursor < text.length) parts.push({ kind: 'text', text: text.slice(cursor) })
  return { parts, unmatched }
}
