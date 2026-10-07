// Briefing follow-ups (POST /briefing/follow-up): questions asked after the
// registration briefing, answered in code from its figures, actions,
// decision and audit log. No model call; every number is computed.

export type FollowUpLabel = 'fact' | 'interpretation' | 'recommendation' | 'note'

export type FollowUpBlock =
  | {
      type: 'text'
      label: FollowUpLabel
      text: string
      finding_ids?: string[]
      author?: string
    }
  | { type: 'list'; label: FollowUpLabel; title: string; items: string[]; author?: string }
  | {
      type: 'table'
      label: FollowUpLabel
      title: string
      columns: string[]
      rows: string[][]
      finding_ids?: string[]
      row_findings?: string[][]
    }
  | {
      type: 'approval'
      label: FollowUpLabel
      decision_id: string
      title: string
      text: string
      office: string
      follow_up: string
      approved: boolean
      approved_by: string | null
      approved_at: string | null
    }

export interface FollowUpAnswer {
  matched: true
  kind: 'answer'
  intent: string
  title: string
  blocks: FollowUpBlock[]
  finding_ids: string[]
  suggestions: string[]
  source: string
  event_ids: number[]
}

export interface FollowUpDenied {
  matched: true
  kind: 'denied'
  employee: string
  employee_title: string
  message: string
  event_ids: number[]
}

export type FollowUpResponse =
  | { matched: false }
  | { matched: true; kind: 'approved'; question: string }
  | FollowUpAnswer
  | FollowUpDenied

const LABELS: readonly FollowUpLabel[] = ['fact', 'interpretation', 'recommendation', 'note']

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function blockFrom(raw: unknown): FollowUpBlock | null {
  if (typeof raw !== 'object' || raw === null) return null
  const b = raw as Record<string, unknown>
  const label = LABELS.includes(b.label as FollowUpLabel) ? (b.label as FollowUpLabel) : 'note'
  const ids = strings(b.finding_ids)
  switch (b.type) {
    case 'text':
      return typeof b.text === 'string'
        ? {
            type: 'text',
            label,
            text: b.text,
            finding_ids: ids,
            ...(typeof b.author === 'string' ? { author: b.author } : {}),
          }
        : null
    case 'list':
      return {
        type: 'list',
        label,
        title: String(b.title ?? ''),
        items: strings(b.items),
        ...(typeof b.author === 'string' ? { author: b.author } : {}),
      }
    case 'table':
      return {
        type: 'table',
        label,
        title: String(b.title ?? ''),
        columns: strings(b.columns),
        rows: Array.isArray(b.rows) ? b.rows.map(strings) : [],
        finding_ids: ids,
        ...(Array.isArray(b.row_findings) ? { row_findings: b.row_findings.map(strings) } : {}),
      }
    case 'approval':
      return typeof b.decision_id === 'string'
        ? {
            type: 'approval',
            label,
            decision_id: b.decision_id,
            title: String(b.title ?? ''),
            text: String(b.text ?? ''),
            office: String(b.office ?? ''),
            follow_up: String(b.follow_up ?? ''),
            approved: b.approved === true,
            approved_by: typeof b.approved_by === 'string' ? b.approved_by : null,
            approved_at: typeof b.approved_at === 'string' ? b.approved_at : null,
          }
        : null
    default:
      return null
  }
}

/** The server's reply, checked: anything unexpected is "not a follow-up",
 * so the question goes on to Explore as before. */
export function followUpFrom(raw: unknown): FollowUpResponse {
  if (typeof raw !== 'object' || raw === null) return { matched: false }
  const r = raw as Record<string, unknown>
  if (r.matched !== true) return { matched: false }
  const eventIds = Array.isArray(r.event_ids)
    ? r.event_ids.filter((id): id is number => typeof id === 'number')
    : []
  if (r.kind === 'approved' && typeof r.question === 'string') {
    return { matched: true, kind: 'approved', question: r.question }
  }
  if (r.kind === 'denied' && typeof r.message === 'string') {
    return {
      matched: true,
      kind: 'denied',
      employee: String(r.employee ?? ''),
      employee_title: String(r.employee_title ?? 'AI employee'),
      message: r.message,
      event_ids: eventIds,
    }
  }
  if (r.kind === 'answer' && Array.isArray(r.blocks)) {
    return {
      matched: true,
      kind: 'answer',
      intent: String(r.intent ?? ''),
      title: String(r.title ?? ''),
      blocks: r.blocks.map(blockFrom).filter((b): b is FollowUpBlock => b !== null),
      finding_ids: strings(r.finding_ids),
      suggestions: strings(r.suggestions),
      source: String(r.source ?? ''),
      event_ids: eventIds,
    }
  }
  return { matched: false }
}
