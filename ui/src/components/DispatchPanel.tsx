import type { AskTask, AuditEvent, CabinetBriefing } from '../api'
import { fieldLabels } from '../fieldLabels'
import { findingLabel } from '../findingLabels'
import {
  dispatchTasks,
  eventsAfter,
  formatTimestamp,
  latestQuestionEventId,
  roleDisplayName,
  type DispatchTask,
} from '../states'
import { CheckIcon, ChevronIcon } from './icons'

interface DispatchPanelProps {
  /** The audit events seen so far (polled every second while a run is in flight). */
  events: AuditEvent[]
  /** Events at or before this id belong to earlier runs and are ignored. */
  minEventId: number
  /** True from Ask until briefing.produced arrives (or the 200 s poll stops). */
  inFlight: boolean
  /** True once the in-flight run has worked for 60 s ("Still working…"). */
  stillWorking: boolean
  /** The audit log could not be loaded: a plain sentence, shown with Retry
   * instead of an empty panel. */
  error?: string | null
  onRetry?: () => void
}

/** The figures a task was assigned and when it finished, from its events. */
function taskFacts(
  events: AuditEvent[],
  taskId: string,
): { findings: string[]; finishedAt: string | null } {
  const assigned = events.find(
    (event) => event.type === 'task.assigned' && event.payload.task_id === taskId,
  )
  const produced = events.find(
    (event) => event.type === 'finding.produced' && event.payload.task_id === taskId,
  )
  const delivered = events.find(
    (event) =>
      event.type === 'briefing.produced' && event.payload.chief_task_id === taskId,
  )
  const findings = Array.isArray(assigned?.payload.findings)
    ? (assigned.payload.findings as unknown[]).filter(
        (id): id is string => typeof id === 'string',
      )
    : []
  return { findings, finishedAt: (produced ?? delivered)?.ts ?? null }
}

/** The status line on a task card — plain words, never a score. */
const STATUS_LABELS: Record<DispatchTask['status'], string> = {
  working: 'Working…',
  done: 'Done',
  unavailable: 'Unavailable',
}

/**
 * Beat 2: the cabinet goes to work, visibly scoped. One task card per
 * dispatched task — the Enrollment Analyst, the Student Success Analyst, and
 * the Chief of Staff once its assignment lands — each listing the exact
 * granted fields from its data.granted event. Cards resolve to "done" as
 * finding.produced / briefing.produced arrive; a task the run completed
 * without shows "unavailable", never a fake success. Everything on the panel
 * comes from the audit events; nothing is simulated client-side. Calm and
 * institutional: the only motion is the status marker's fade, and that is
 * disabled under prefers-reduced-motion.
 */
export function DispatchPanel({
  events,
  minEventId,
  inFlight,
  stillWorking,
  error = null,
  onRetry,
}: DispatchPanelProps) {
  if (error !== null) {
    return (
      <div className="state-error state-panel error-panel" role="alert">
        <p>Couldn't load the AI employees' work. {error}</p>
        {onRetry !== undefined && (
          <button type="button" className="btn-secondary secondary" onClick={onRetry}>
            Retry
          </button>
        )}
      </div>
    )
  }
  const questionEventId = latestQuestionEventId(eventsAfter(events, minEventId))
  const tasks = questionEventId === null ? [] : dispatchTasks(events, questionEventId)
  if (tasks.length === 0) {
    return (
      <p className="state-empty hint">
        Ask an approved question and each AI employee's task appears here, with
        exactly the data it was given.
      </p>
    )
  }
  const allDone = tasks.every((task) => task.status !== 'working') && !inFlight

  return (
    <section className="dispatch" aria-label="The cabinet's task dispatch">
      <h3>{allDone ? 'The cabinet has reported' : 'The cabinet is working'}</h3>
      <div className="task-cards">
        {tasks.map((task) => (
          <TaskCard
            key={task.task_id}
            task={task}
            open={!allDone}
            {...taskFacts(events, task.task_id)}
          />
        ))}
      </div>
      {inFlight && stillWorking && (
        <p className="hint" role="status">
          Still working… a live run can take a minute or two.
        </p>
      )}
      {allDone && <p className="hint">Every grant above is recorded in the audit log.</p>}
    </section>
  )
}

/** The briefing section each dispatch role's availability is read from. */
const ASK_ROLE_SECTION: Record<string, 1 | 2 | 3> = {
  enrollment_analyst: 2,
  student_success_analyst: 3,
  // The Chief of Staff writes sections 1 and 7; section 1's availability is
  // the card's.
  chief_of_staff: 1,
}

/**
 * The dispatch view for a role that may ask but may not read the audit log
 * (the executive): the same task cards, built from the /ask response itself
 * instead of polled events. The run is complete by the time the response
 * arrives, so each card is done or unavailable from its briefing section.
 */
export function AskDispatch({
  tasks,
  briefing,
}: {
  tasks: AskTask[]
  briefing: CabinetBriefing
}) {
  return (
    <section className="dispatch" aria-label="The cabinet's task dispatch">
      <h3>The cabinet has reported</h3>
      <div className="task-cards">
        {tasks.map((task) => {
          const sectionId = ASK_ROLE_SECTION[task.role]
          const section = sectionId !== undefined ? briefing.sections[sectionId] : undefined
          const status: DispatchTask['status'] =
            section !== undefined && section.kind !== 'available'
              ? 'unavailable'
              : 'done'
          return (
            <TaskCard
              key={task.task_id}
              task={{
                task_id: task.task_id,
                role: task.role,
                granted_fields: task.granted_fields.length > 0 ? task.granted_fields : null,
                level: task.level ?? null,
                status,
              }}
              open={false}
              findings={task.findings}
              finishedAt={null}
            />
          )
        })}
      </div>
      <p className="hint">
        Every grant above is recorded in the institution's audit log.
      </p>
    </section>
  )
}

/**
 * One task card: the AI employee, what it was asked to explain (by the
 * figures' display labels), the data it was given (plain field names, raw
 * names only in a folded Technical detail), and its status.
 */
function TaskCard({
  task,
  open,
  findings,
  finishedAt,
}: {
  task: DispatchTask
  open: boolean
  findings: string[]
  finishedAt: string | null
}) {
  return (
    <div className="task-card" data-status={task.status}>
      <h4>{roleDisplayName(task.role)}</h4>
      <p className="task-status">
        {task.status === 'done' && <CheckIcon />}
        {task.status === 'done' && finishedAt !== null
          ? `Finished at ${formatTimestamp(finishedAt)}`
          : STATUS_LABELS[task.status]}
      </p>
      {findings.length > 0 && (
        <p className="task-findings">
          Explains: {findings.map((id) => findingLabel(id)).join('; ')}
        </p>
      )}
      {task.granted_fields === null ? (
        <p className="hint">Waiting for its data…</p>
      ) : (
        <details className="fold technical-detail" open={open}>
          <summary>
            <ChevronIcon />
            {task.level === 'aggregate' ? 'Totals it was given' : 'Data it was given'} (
            {task.granted_fields.length})
          </summary>
          {task.level === 'aggregate' && (
            <p className="hint">Totals only, never a student's record.</p>
          )}
          <ul className="plain-list">
            {fieldLabels(task.granted_fields).map((label) => (
              <li key={label}>{label}</li>
            ))}
          </ul>
          <details className="fold technical-detail">
            <summary>
              <ChevronIcon />
              Technical detail
            </summary>
            <ul className="field-list">
              {task.granted_fields.map((field) => (
                <li key={field}>
                  <code>{field}</code>
                </li>
              ))}
            </ul>
          </details>
        </details>
      )}
    </div>
  )
}
