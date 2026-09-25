import type { AskTask, AuditEvent, CabinetBriefing } from '../api'
import {
  dispatchTasks,
  eventsAfter,
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
}: DispatchPanelProps) {
  const questionEventId = latestQuestionEventId(eventsAfter(events, minEventId))
  if (questionEventId === null) return null
  const tasks = dispatchTasks(events, questionEventId)
  if (tasks.length === 0) return null
  const allDone = tasks.every((task) => task.status !== 'working') && !inFlight

  return (
    <section className="dispatch" aria-label="The cabinet's task dispatch">
      <h3>{allDone ? 'The cabinet has reported' : 'The cabinet is working'}</h3>
      <div className="task-cards">
        {tasks.map((task) => (
          <TaskCard key={task.task_id} task={task} open={!allDone} />
        ))}
      </div>
      {inFlight && stillWorking && (
        <p className="hint" role="status">
          Still working… a live run can take a minute or two.
        </p>
      )}
      {allDone && (
        <p className="hint">
          Every grant above is logged. The full event list is in the audit log
          below.
        </p>
      )}
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
              open
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

/** One task card: the role, its task id, its granted fields, its status. */
function TaskCard({ task, open }: { task: DispatchTask; open: boolean }) {
  return (
    <div className="task-card" data-status={task.status}>
      <h4>{roleDisplayName(task.role)}</h4>
      <p className="task-id">{task.task_id}</p>
      {task.granted_fields === null ? (
        <p className="hint">Awaiting its data grant…</p>
      ) : (
        <details open={open}>
          <summary>
            <ChevronIcon />
            Fields granted ({task.granted_fields.length})
          </summary>
          <p>
            {task.level === 'aggregate'
              ? 'Aggregate fields granted (never student rows):'
              : 'Fields this analyst is allowed to see:'}
          </p>
          <ul>
            {task.granted_fields.map((field) => (
              <li key={field}>
                <code>{field}</code>
              </li>
            ))}
          </ul>
        </details>
      )}
      <p className="task-status">
        {task.status === 'done' && <CheckIcon />}
        {STATUS_LABELS[task.status]}
      </p>
    </div>
  )
}
