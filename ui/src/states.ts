// Pure UI logic for the dashboard shell: the loading / error /
// model-unavailable state machine, the demo query switches, the
// approved-question check, and display formatting. No arithmetic on
// metrics lives here — the API's `display` strings are rendered verbatim.

export const APPROVED_QUESTION = 'What should I know about spring registration?'

// --- Demo query switches ---------------------------------------------------

export interface UiFlags {
  /** ?slow=1 — add a client-side delay so the loading state is visible. */
  slow: boolean
  /** ?fail=findings — force the findings fetch to fail (error + Retry). */
  failFindings: boolean
  /** ?model=down — force the briefing text source to report unavailable. */
  modelDown: boolean
  /** ?evidence=M2 — open the evidence drawer on a finding. */
  evidence: string | null
  /** ?demo=refusal — fire the audit log's denied-request demo on load. */
  demoRefusal: boolean
}

export function parseFlags(search: string): UiFlags {
  const params = new URLSearchParams(search)
  return {
    slow: params.get('slow') === '1',
    failFindings: params.get('fail') === 'findings',
    modelDown: params.get('model') === 'down',
    evidence: params.get('evidence'),
    demoRefusal: params.get('demo') === 'refusal',
  }
}

/** Replace the `evidence` query parameter without reloading the page. */
export function evidenceUrl(search: string, evidenceId: string | null): string {
  const params = new URLSearchParams(search)
  if (evidenceId === null) {
    params.delete('evidence')
  } else {
    params.set('evidence', evidenceId)
  }
  const query = params.toString()
  return query.length > 0 ? `?${query}` : window.location.pathname
}

// --- The approved-question check (mirrors the API's rule) -------------------

export function isApprovedQuestion(
  question: string,
  approved: readonly string[] = [APPROVED_QUESTION],
): boolean {
  const normalize = (s: string) => s.trim().toLowerCase().replace(/\?+$/, '').trim()
  const candidate = normalize(question)
  return (
    candidate.length > 0 && approved.some((text) => normalize(text) === candidate)
  )
}

// --- Loading / error / ready state machine ---------------------------------

export type LoadState<T> =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; data: T }

export function describeState<T>(state: LoadState<T>): string {
  switch (state.kind) {
    case 'loading':
      return 'Loading'
    case 'error':
      return `Error: ${state.message}`
    case 'ready':
      return 'Ready'
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

// --- Display formatting -----------------------------------------------------

/**
 * The exact `--` rule, UI side: a finding renders `--` if and only if the
 * API sent display "--" (value null). `0` is a real value and renders `0`.
 */
export interface DisplayText {
  text: string
  missing: boolean
  reason: string | null
}

export function findingDisplay(finding: {
  display?: unknown
  reason?: unknown
}): DisplayText {
  const text =
    typeof finding.display === 'string' && finding.display.length > 0
      ? finding.display
      : '--'
  return {
    text,
    missing: text === '--',
    reason: typeof finding.reason === 'string' ? finding.reason : null,
  }
}

// --- M3 balance threshold ---------------------------------------------------

/**
 * The M3 finding's balance threshold for display, read from
 * `comparison.threshold_usd` (a computed field of the finding). Returns null
 * when the field is absent or not a usable number — the caller then shows the
 * finding's title text instead of a number. Formatting only; no arithmetic.
 */
export function m3ThresholdLabel(finding: {
  comparison?: unknown
}): string | null {
  const comparison =
    typeof finding.comparison === 'object' && finding.comparison !== null
      ? (finding.comparison as Record<string, unknown>)
      : null
  const threshold = comparison?.threshold_usd
  if (typeof threshold === 'number' && Number.isFinite(threshold)) {
    return threshold.toLocaleString('en-US', {
      style: 'currency',
      currency: 'USD',
      maximumFractionDigits: 0,
    })
  }
  if (typeof threshold === 'string' && threshold.length > 0) {
    return threshold
  }
  return null
}

// --- Analyst briefing source (with fallback) --------------------------------

export interface AnalystClaim {
  text: string
  finding_ids: string[]
}

/**
 * One analyst's briefing section (Enrollment Analyst: M1/M2/M7; Student
 * Success Analyst: M3/M4/M5). Same response shape on both routes.
 */
export type AnalystBriefing =
  | {
      kind: 'available'
      text: string
      claims: AnalystClaim[]
      provider: string | null
      /** A configured display label (e.g. "live model"), never a model id. */
      model_label: string | null
      recorded: boolean
    }
  | { kind: 'unavailable'; reason: string }

/**
 * Map a GET /briefing/<role> outcome (and the ?model=down switch) onto
 * the two UI branches: render the analyst's claims, or show "model
 * unavailable" while the metrics and evidence keep rendering. The response's
 * provider/model_label/recorded fields are carried through so the UI can
 * label the source of the text honestly. Any `model` field in the body is a
 * model id and is ignored on purpose — source labels never name a model.
 */
export function analystFromResponse(
  status: number,
  body: unknown,
  modelDown: boolean,
): AnalystBriefing {
  if (modelDown) {
    return {
      kind: 'unavailable',
      reason: 'Forced by the ?model=down demo switch.',
    }
  }
  const record =
    typeof body === 'object' && body !== null ? (body as Record<string, unknown>) : {}
  if (status === 404) {
    return {
      kind: 'unavailable',
      reason: 'The briefing route answered HTTP 404 (not found on this build).',
    }
  }
  if (status === 503) {
    const reason =
      typeof record.reason === 'string'
        ? record.reason
        : 'The model provider reported it is unavailable (HTTP 503).'
    return { kind: 'unavailable', reason }
  }
  if (status < 200 || status >= 300) {
    return {
      kind: 'unavailable',
      reason: `Unexpected response from the briefing source (HTTP ${status}).`,
    }
  }
  if (record.available !== true) {
    const reason =
      typeof record.reason === 'string'
        ? record.reason
        : 'The briefing source reported unavailable.'
    return { kind: 'unavailable', reason }
  }
  const claims: AnalystClaim[] = []
  if (Array.isArray(record.claims)) {
    for (const claim of record.claims) {
      if (typeof claim !== 'object' || claim === null) continue
      const c = claim as Record<string, unknown>
      if (typeof c.text !== 'string') continue
      const ids = Array.isArray(c.finding_ids)
        ? c.finding_ids.filter((id): id is string => typeof id === 'string')
        : []
      claims.push({ text: c.text, finding_ids: ids })
    }
  }
  return {
    kind: 'available',
    text: typeof record.text === 'string' ? record.text : '',
    claims,
    provider: typeof record.provider === 'string' ? record.provider : null,
    model_label:
      typeof record.model_label === 'string' ? record.model_label : null,
    recorded: record.recorded === true,
  }
}

// --- Analyst source labeling --------------------------------------------------

export type AnalystSource = 'live' | 'recorded' | 'fake'

/**
 * Who actually wrote the analyst text, from the response's provenance fields.
 * The fake provider is a deterministic test stub and must never look live.
 */
export function analystSource(briefing: {
  provider: string | null
  recorded: boolean
}): AnalystSource {
  if (briefing.provider === 'fake') return 'fake'
  if (briefing.recorded) return 'recorded'
  return 'live'
}

/**
 * The one-line source label under a model-written section. Exactly one
 * parenthetical, ever: a replay says the text was recorded from a live run;
 * a live run names its configured label, or "live model" when the label is
 * unset (or is literally "live model"); the fake provider is a test stub and
 * never passes as a model.
 */
export function analystSourceLabel(
  source: AnalystSource,
  analyst: string,
  modelLabel: string | null = null,
): string {
  switch (source) {
    case 'fake':
      return 'Test stub, not a live model'
    case 'recorded':
      return `Written by ${analyst} (recorded live run)`
    case 'live':
      return `Written by ${analyst} (${
        modelLabel !== null && modelLabel !== 'live model' ? modelLabel : 'live model'
      })`
  }
}

// --- The produced briefing (POST /ask, GET /briefing) ---------------------------

/** Who wrote a model-written section (the Chief of Staff or an analyst). */
export interface SectionProvenance {
  source: string
  provider: string | null
  /** A configured display label (e.g. "live model"), never a model id. */
  model_label: string | null
  recorded: boolean
}

/**
 * One model-written briefing section (1 executive summary, 2 current
 * measure, 3 student groups, 7 known limitations). Unavailable sections are
 * marked, never invented: the caller falls back to the computed page.
 */
export type ModelSection =
  | {
      kind: 'available'
      text: string
      claims: AnalystClaim[]
      provenance: SectionProvenance
    }
  | { kind: 'unavailable'; reason: string }

export interface BriefingFindingSummary {
  title: string
  display: string
  source_fields: string[]
}

export interface BriefingAction {
  office: string
  text: string
  finding_ids: string[]
}

export interface BriefingDecision {
  id: string
  title: string
  text: string
  follow_up: { office: string; description: string }
}

/** The whole seven-section briefing one POST /ask produced. */
export interface CabinetBriefing {
  /** The registry id of the question asked (e.g. "spring-registration"). */
  question_id: string
  /** The question text, as answered. */
  question: string
  /** The question.asked audit event id this run's task ids are built from. */
  question_event_id: number
  sections: {
    1: ModelSection
    2: ModelSection
    3: ModelSection
    4: { findings: Record<string, BriefingFindingSummary> }
    5: { actions: BriefingAction[] }
    6: { decisions: BriefingDecision[] }
    7: ModelSection
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null
    ? (value as Record<string, unknown>)
    : {}
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : []
}

/** Parse one model-written section; anything unrecognized is unavailable. */
export function modelSectionFrom(raw: unknown): ModelSection {
  const record = asRecord(raw)
  if (record.kind !== 'available') {
    return {
      kind: 'unavailable',
      reason:
        typeof record.reason === 'string'
          ? record.reason
          : 'This section is unavailable.',
    }
  }
  const claims: AnalystClaim[] = []
  if (Array.isArray(record.claims)) {
    for (const claim of record.claims) {
      const c = asRecord(claim)
      if (typeof c.text !== 'string') continue
      claims.push({ text: c.text, finding_ids: stringList(c.finding_ids) })
    }
  }
  const provenance = asRecord(record.provenance)
  return {
    kind: 'available',
    text: typeof record.text === 'string' ? record.text : '',
    claims,
    provenance: {
      source: typeof provenance.source === 'string' ? provenance.source : '',
      provider: typeof provenance.provider === 'string' ? provenance.provider : null,
      model_label:
        typeof provenance.model_label === 'string' ? provenance.model_label : null,
      recorded: provenance.recorded === true,
    },
  }
}

/** Parse the /ask (or GET /briefing) briefing; null when it is not one. */
export function cabinetBriefingFrom(raw: unknown): CabinetBriefing | null {
  const record = asRecord(raw)
  if (typeof record.question_id !== 'string') return null
  if (typeof record.question_event_id !== 'number') return null
  const sections = asRecord(record.sections)
  const findings: Record<string, BriefingFindingSummary> = {}
  const section4 = asRecord(asRecord(sections[4]).findings)
  for (const [id, value] of Object.entries(section4)) {
    const finding = asRecord(value)
    findings[id] = {
      title: typeof finding.title === 'string' ? finding.title : id,
      display: typeof finding.display === 'string' ? finding.display : '--',
      source_fields: stringList(finding.source_fields),
    }
  }
  const actions: BriefingAction[] = []
  const rawActions = asRecord(sections[5]).actions
  if (Array.isArray(rawActions)) {
    for (const action of rawActions) {
      const a = asRecord(action)
      if (typeof a.office !== 'string' || typeof a.text !== 'string') continue
      actions.push({
        office: a.office,
        text: a.text,
        finding_ids: stringList(a.finding_ids),
      })
    }
  }
  const decisions: BriefingDecision[] = []
  const rawDecisions = asRecord(sections[6]).decisions
  if (Array.isArray(rawDecisions)) {
    for (const decision of rawDecisions) {
      const d = asRecord(decision)
      if (typeof d.id !== 'string' || typeof d.text !== 'string') continue
      const followUp = asRecord(d.follow_up)
      decisions.push({
        id: d.id,
        title: typeof d.title === 'string' ? d.title : d.id,
        text: d.text,
        follow_up: {
          office: typeof followUp.office === 'string' ? followUp.office : '',
          description:
            typeof followUp.description === 'string' ? followUp.description : '',
        },
      })
    }
  }
  return {
    question_id: record.question_id,
    question: typeof record.question === 'string' ? record.question : '',
    question_event_id: record.question_event_id,
    sections: {
      1: modelSectionFrom(sections[1]),
      2: modelSectionFrom(sections[2]),
      3: modelSectionFrom(sections[3]),
      4: { findings },
      5: { actions },
      6: { decisions },
      7: modelSectionFrom(sections[7]),
    },
  }
}

// --- Beat 2 dispatch view ----------------------------------------------------

/** One task card in the dispatch panel, derived from the audit events. */
export interface DispatchTask {
  task_id: string
  role: string
  /** The granted fields from the task's data.granted event; null until it lands. */
  granted_fields: string[] | null
  /** 'aggregate' for the Chief of Staff's grant; null for the analysts'. */
  level: string | null
  /**
   * done: the task's finding.produced arrived (or the run's briefing.produced
   * with the section available, for cache-served runs). unavailable: the run
   * completed but this task produced nothing (its section is marked
   * unavailable in the briefing — the card never fakes a success).
   */
  status: 'working' | 'done' | 'unavailable'
}

const DISPATCH_ROLE_ORDER = [
  'enrollment_analyst',
  'student_success_analyst',
  'chief_of_staff',
] as const

/** The briefing section each dispatch role's availability is read from. */
const ROLE_SECTION: Record<string, string> = {
  enrollment_analyst: '2',
  student_success_analyst: '3',
  // The Chief of Staff writes sections 1 and 7; section 1's availability is
  // the card's (its task id landing in briefing.produced says nothing about
  // whether the run validated).
  chief_of_staff: '1',
}

/** The highest event id in a fetched events list (0 when empty). */
export function maxEventId(events: AuditEvent[]): number {
  return events.reduce((max, event) => Math.max(max, event.id), 0)
}

/** The events of one run: everything after the previous run's last event. */
export function eventsAfter(events: AuditEvent[], minEventId: number): AuditEvent[] {
  return events.filter((event) => event.id > minEventId)
}

/** The question.asked event id of the most recent question, or null. */
export function latestQuestionEventId(events: AuditEvent[]): number | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (events[index].type === 'question.asked') return events[index].id
  }
  return null
}

/**
 * The dispatch panel's task cards for one question run, in cabinet order:
 * the two analysts, then the Chief of Staff once its assignment lands. Each
 * card lists the exact fields from the task's data.granted event, falling
 * back to its task.assigned payload when the run was served from the cache
 * (a cache hit writes no grant). Cards resolve to done when the task's
 * finding.produced appears; when the run's briefing.produced arrives, every
 * remaining card resolves — done when its section is available in the
 * briefing, unavailable when the run completed without it (never a fake
 * success). `questionEventId` is the question.asked audit event id: task ids
 * are `task-<role>-<question_event_id>`, and briefing.produced carries it as
 * `question_event_id` (its `question_id` is the registry id, a string).
 */
export function dispatchTasks(
  events: AuditEvent[],
  questionEventId: number,
): DispatchTask[] {
  const suffix = `-${questionEventId}`
  const producedEvent = events.find(
    (event) =>
      event.type === 'briefing.produced' &&
      event.payload.question_event_id === questionEventId,
  )
  const sources =
    producedEvent !== undefined &&
    typeof producedEvent.payload.sources === 'object' &&
    producedEvent.payload.sources !== null
      ? (producedEvent.payload.sources as Record<string, unknown>)
      : {}
  const sectionAvailable = (sectionId: string): boolean => {
    const entry = sources[sectionId]
    return (
      typeof entry === 'object' &&
      entry !== null &&
      (entry as Record<string, unknown>).available === true
    )
  }
  const tasks: DispatchTask[] = []
  for (const role of DISPATCH_ROLE_ORDER) {
    const assigned = events.find(
      (event) =>
        event.type === 'task.assigned' &&
        event.payload.role === role &&
        typeof event.payload.task_id === 'string' &&
        event.payload.task_id.endsWith(suffix),
    )
    if (assigned === undefined) continue
    const taskId = assigned.payload.task_id as string
    const grant = events.find(
      (event) => event.type === 'data.granted' && event.payload.task_id === taskId,
    )
    const produced = events.some(
      (event) => event.type === 'finding.produced' && event.payload.task_id === taskId,
    )
    let status: DispatchTask['status'] = 'working'
    if (produced) {
      status = 'done'
    } else if (producedEvent !== undefined) {
      // Every card resolves from the briefing itself: done when its section
      // is available, unavailable when the run completed without it (never a
      // fake success — a failed Chief of Staff run still carries its
      // chief_task_id, so the id alone cannot mean done).
      status = sectionAvailable(ROLE_SECTION[role]) ? 'done' : 'unavailable'
    }
    const grantedFields =
      grant !== undefined
        ? stringList(grant.payload.granted_fields)
        : stringList(assigned.payload.fields)
    tasks.push({
      task_id: taskId,
      role,
      granted_fields: grantedFields.length > 0 ? grantedFields : null,
      level:
        grant !== undefined && typeof grant.payload.level === 'string'
          ? grant.payload.level
          : role === 'chief_of_staff'
            ? 'aggregate'
            : null,
      status,
    })
  }
  return tasks
}

export function roleDisplayName(role: string): string {
  switch (role) {
    case 'enrollment_analyst':
      return 'Enrollment Analyst'
    case 'student_success_analyst':
      return 'Student Success Analyst'
    case 'chief_of_staff':
      return 'Chief of Staff'
    default:
      return role
  }
}

// --- Audit log helpers -------------------------------------------------------

export const EVENT_TYPES = [
  'question.asked',
  'task.assigned',
  'data.granted',
  'data.refused',
  'finding.produced',
  'briefing.produced',
  'decision.approved',
  'task.created',
  // User administration (the audit vocabulary's one allowed extension).
  'admin.changed',
] as const

export interface AuditEvent {
  id: number
  ts: string
  type: string
  actor: string
  payload: Record<string, unknown>
}

/** The log renders oldest first — newest last — per the demo script. */
export function filterEvents(
  events: AuditEvent[],
  eventType: string | null,
): AuditEvent[] {
  if (eventType === null) return events
  return events.filter((event) => event.type === eventType)
}

// The API records every event in UTC; the reader is a person in one place
// (a president in Tulsa), so the log shows the viewer's own zone, named,
// rather than making them convert. `timeZone` is a test seam — production
// leaves it undefined and takes the browser's zone.
export function formatTimestamp(ts: string, timeZone?: string): string {
  const date = new Date(ts)
  if (Number.isNaN(date.getTime())) return ts
  const part: Record<string, string> = {}
  for (const { type, value } of new Intl.DateTimeFormat('en-US', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
    timeZoneName: 'short',
  }).formatToParts(date)) {
    part[type] = value
  }
  return (
    `${part.year}-${part.month}-${part.day} ` +
    `${part.hour}:${part.minute}:${part.second} ${part.timeZoneName}`
  )
}

export interface TaskRecord {
  id: string
  decision_id: string
  office: string
  description: string
  status: string
}

/** Recover a previously created simulated task from the audit log. */
export function taskFromEvents(
  events: AuditEvent[],
  decisionId: string,
): TaskRecord | undefined {
  const event = events.find(
    (e) => e.type === 'task.created' && e.payload.decision_id === decisionId,
  )
  const task = event?.payload.task
  if (typeof task !== 'object' || task === null) return undefined
  const record = task as Record<string, unknown>
  if (typeof record.id !== 'string' || typeof record.status !== 'string') {
    return undefined
  }
  return {
    id: record.id,
    decision_id: typeof record.decision_id === 'string' ? record.decision_id : '',
    office: typeof record.office === 'string' ? record.office : '',
    description: typeof record.description === 'string' ? record.description : '',
    status: record.status,
  }
}
