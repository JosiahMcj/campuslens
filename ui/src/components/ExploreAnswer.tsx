import { useEffect, useState } from 'react'

import {
  answerNotes,
  cellDomId,
  displayText,
  formatCell,
  linkSentence,
  plannerLabel,
  sourceLabel,
  SUPPRESSED,
  visibleColumns,
  type ExploreClaim,
  type ExploreResponse,
  type ExploreStep,
} from '../explore'
import { fieldLabels } from '../fieldLabels'
import './Explore.css'

/** Rows a step's table shows before "Show all N". */
export const TABLE_PREVIEW_ROWS = 10

/** How long a cell stays highlighted after its number is opened. */
const HIGHLIGHT_MS = 2400

/**
 * School-record fields in plain words. Kept here until the shared field
 * map (ui/src/fieldLabels.ts) carries them; anything not listed falls back
 * to fieldLabels' own wording.
 */
const SCHOOL_FIELDS: Record<string, string> = {
  'academic_periods.academic_year': 'Academic year',
  'academic_periods.season': 'Season (fall, spring, summer)',
  'academic_programs.college_code': 'College',
  'academic_programs.major_code': 'Major',
  'academic_programs.name': 'Major name',
  'academic_standings.standing': 'Academic standing',
  'academic_standings.term_code': 'Term of the standing',
  'colleges.name': 'College name',
  'courses.course_level': 'Course level',
  'courses.grade_mode': 'Grading mode',
  'courses.subject_code': 'Subject',
  'courses.title': 'Course title',
  'final_grades.grade': 'Final grade',
  'instructors.academic_rank': 'Academic rank',
  'instructors.first_name': 'Instructor first name (fictional)',
  'instructors.last_name': 'Instructor last name (fictional)',
  'person_holds.amount': 'Hold amount',
  'person_holds.category': 'Hold type',
  'person_holds.end_date': 'Date the hold ended',
  'person_holds.responsible_office': 'Office responsible for the hold',
  'person_holds.term_code': 'Term the hold was placed',
  'program_requirements.course_id': 'Courses each major requires',
  'section_instructors.instructor_id': 'Instructor of record',
  'section_instructors.instructor_id (counted)': 'Number of instructors (counted, not named)',
  'section_registrations.section_id': 'Section registered in',
  'section_registrations.student_id (counted)': 'Number of students (counted, not named)',
  'section_registrations.term_code': 'Term registered',
  'sections.course_id': 'Course of each section',
  'sections.modality': 'Teaching mode',
  'sections.term_code': 'Term of each section',
  'student_academic_programs.end_term': 'Last term in the major',
  'student_academic_programs.program_code': 'Major',
  'student_academic_programs.status': 'Status in the major',
  'student_appointments.status': 'Advising appointment status',
  'student_appointments.term_code': 'Term of the advising appointment',
  'student_term_records.attempted_hours': 'Credit hours attempted',
  'student_term_records.cumulative_gpa': 'Cumulative GPA',
  'student_term_records.earned_hours': 'Credit hours earned',
  'student_term_records.program_code': 'Major each term',
  'student_term_records.term_code': 'Term',
  'students.entry_term': 'Entry term',
  'students.entry_type': 'Entry type',
  'students.first_generation': 'First-generation student',
  'students.pell_recipient': 'Pell recipient',
  'students.residency': 'Residency',
}

function readLabels(fields: readonly string[]): string[] {
  const known = fields.filter((field) => SCHOOL_FIELDS[field] !== undefined)
  const rest = fields.filter((field) => SCHOOL_FIELDS[field] === undefined)
  // An unlisted field still reads as words, never as an id.
  const plain = fieldLabels(rest).map((label) => label.replace(/\bids?\b/gi, '').replace(/\s+/g, ' ').trim())
  return [...new Set([...known.map((field) => SCHOOL_FIELDS[field]), ...plain])].filter(Boolean)
}

interface ExploreAnswerProps {
  /** Unique per answer on the page (the exchange id), for cell ids. */
  answerKey: string
  response: ExploreResponse
  /** Shown as one-tap chips under a refusal that carries no suggestions. */
  fallbackSuggestions: readonly string[]
  onAsk: (question: string) => void
  /** True while another question is being answered. */
  busy: boolean
  /** Opens the audit log (roles that may read it); null hides the link. */
  onSeeAuditLog: (() => void) | null
}

/** "Working it out…": the reply while an Explore question is answered. */
export function ExploreWorking() {
  return (
    <div className="explore-working" role="status" aria-busy="true">
      <p className="explore-working-line">Working it out…</p>
      <div className="skeleton skeleton-line" />
      <div className="skeleton skeleton-line" />
      <div className="skeleton skeleton-line short" />
    </div>
  )
}

function Suggestions({
  title,
  questions,
  onAsk,
  busy,
}: {
  title: string
  questions: readonly string[]
  onAsk: (question: string) => void
  busy: boolean
}) {
  if (questions.length === 0) return null
  return (
    <div className="explore-suggestions">
      <p className="explore-suggestions-title">{title}</p>
      <ul className="explore-chip-row">
        {questions.slice(0, 3).map((question) => (
          <li key={question}>
            <button
              type="button"
              className="chip explore-chip"
              disabled={busy}
              onClick={() => onAsk(question)}
            >
              {question}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

function StepTable({
  answerKey,
  index,
  step,
  expanded,
  onExpand,
  highlight,
}: {
  answerKey: string
  index: number
  step: ExploreStep
  expanded: boolean
  onExpand: () => void
  highlight: ExploreClaim | null
}) {
  const columns = visibleColumns(step)
  const rows = step.table.rows
  if (rows.length === 0 || columns.length === 0) {
    return <p className="explore-empty">This step found no rows to show.</p>
  }
  const shown = expanded ? rows : rows.slice(0, TABLE_PREVIEW_ROWS)
  return (
    <>
      <div className="explore-table-wrap" role="region" aria-label={`Step ${index + 1} table`} tabIndex={0}>
        <table className="explore-table">
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={step.table.columns[c].key} scope="col">
                  {step.table.columns[c].label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((row, r) => (
              <tr key={r}>
                {columns.map((c) => {
                  const key = step.table.columns[c].key
                  const value = row[c] ?? null
                  const lit =
                    highlight !== null &&
                    highlight.table === index &&
                    highlight.row === r &&
                    highlight.column === key
                  return (
                    <td
                      key={key}
                      id={cellDomId(answerKey, index, r, key)}
                      tabIndex={-1}
                      className={[
                        typeof value === 'number' ? 'explore-num-cell' : '',
                        value === SUPPRESSED ? 'explore-withheld' : '',
                        lit ? 'explore-cell-lit' : '',
                      ]
                        .filter(Boolean)
                        .join(' ') || undefined}
                    >
                      {formatCell(value)}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > TABLE_PREVIEW_ROWS && !expanded && (
        <button type="button" className="link-button explore-show-all" onClick={onExpand}>
          Show all {rows.length}
        </button>
      )}
    </>
  )
}

/**
 * One Explore answer: the sentences (each number a link to its table
 * cell), a quiet notes line, the honest source line, and "How this was
 * answered" with every step's parameters, table and the data it read. A
 * refusal reads like the briefing's refusals; a question no analysis
 * answers gets a plain line and three questions to try instead.
 */
export function ExploreAnswer({
  answerKey,
  response,
  fallbackSuggestions,
  onAsk,
  busy,
  onSeeAuditLog,
}: ExploreAnswerProps) {
  const [open, setOpen] = useState(false)
  const [expanded, setExpanded] = useState<Record<number, boolean>>({})
  const [highlight, setHighlight] = useState<ExploreClaim | null>(null)
  // Bumped on every number opened, so opening the same one again scrolls again.
  const [jump, setJump] = useState(0)

  useEffect(() => {
    if (highlight === null) return
    // Once the fold has opened and laid out, bring the cell into view
    // (inside its own scrolling table box too) and move focus onto it. A
    // just-opened <details> renders its content a frame or two later, and
    // until then the cell can be neither focused nor scrolled to, so try
    // again on the next frames.
    let frame = 0
    let tries = 0
    let settle = 0
    const reveal = () => {
      const cell = document.getElementById(
        cellDomId(answerKey, highlight.table, highlight.row, highlight.column),
      )
      if (cell === null) return
      const fold = cell.closest('details')
      if (fold !== null && !fold.open) fold.open = true
      cell.focus({ preventScroll: true })
      if (document.activeElement !== cell && tries < 12) {
        tries += 1
        frame = window.requestAnimationFrame(reveal)
        return
      }
      // An instant jump (Chrome drops a smooth scroll that has to move two
      // scrolling boxes at once, the page and the table), repeated over the
      // next frames while the opened fold finishes its layout.
      cell.scrollIntoView?.({ block: 'center', inline: 'nearest' })
      if (settle < 3) {
        settle += 1
        frame = window.requestAnimationFrame(reveal)
      }
    }
    frame = window.requestAnimationFrame(reveal)
    const timer = window.setTimeout(() => setHighlight(null), HIGHLIGHT_MS)
    return () => {
      window.cancelAnimationFrame(frame)
      window.clearTimeout(timer)
    }
  }, [highlight, jump, answerKey])

  const openCell = (claim: ExploreClaim) => {
    setOpen(true)
    if (claim.row >= TABLE_PREVIEW_ROWS) {
      setExpanded((previous) => ({ ...previous, [claim.table]: true }))
    }
    setHighlight(claim)
    setJump((value) => value + 1)
  }

  if (response.refused) {
    return (
      <div className="explore-answer">
        <div className="refusal-card" role="alert">
          <h3>Refused</h3>
          <p>{response.message ?? 'The Cabinet does not answer that question.'}</p>
          <p className="explore-recorded">
            The question and the refusal are recorded in the audit log.
            {onSeeAuditLog !== null && (
              <>
                {' '}
                <button type="button" className="link-button" onClick={onSeeAuditLog}>
                  See the audit log
                </button>
              </>
            )}
          </p>
        </div>
        <Suggestions
          title="You can ask one of these instead"
          questions={
            response.suggestions !== undefined && response.suggestions.length > 0
              ? response.suggestions
              : fallbackSuggestions
          }
          onAsk={onAsk}
          busy={busy}
        />
      </div>
    )
  }

  if (response.answer.length === 0) {
    return (
      <div className="explore-answer">
        <p className="explore-message">
          {response.message ?? 'The Cabinet could not answer that question.'}
        </p>
        <Suggestions
          title="Try one of these"
          questions={response.suggestions ?? []}
          onAsk={onAsk}
          busy={busy}
        />
      </div>
    )
  }

  const notes = answerNotes(response)
  const source = sourceLabel(response.source)
  const planned = plannerLabel(response.planner)
  const fellBack = (response.fallbacks ?? []).length > 0

  return (
    <div className="explore-answer">
      <div className="explore-sentences">
        {response.answer.map((sentence, s) => (
          <p key={s}>
            {linkSentence(sentence, response.steps).map((part, p) =>
              'claim' in part ? (
                <button
                  key={p}
                  type="button"
                  className="finding-link explore-link"
                  title="Show where this comes from"
                  onClick={() => openCell(part.claim)}
                >
                  {part.text}
                </button>
              ) : (
                <span key={p}>{part.text}</span>
              ),
            )}
          </p>
        ))}
      </div>
      {notes.length > 0 && <p className="explore-notes">{notes.join(' ')}</p>}
      {source !== null && <p className="explore-source">{source}</p>}

      <details
        className="fold explore-how"
        open={open}
        onToggle={(event) => setOpen(event.currentTarget.open)}
      >
        <summary>How this was answered</summary>
        <ol className="explore-steps">
          {response.steps.map((step, index) => (
            <li key={index} className="explore-step">
              <h4 className="explore-step-title">
                Step {index + 1}. {displayText(step.title)}
              </h4>
              {step.params_plain.length > 0 && (
                <ul className="explore-params">
                  {step.params_plain.map((line) => (
                    <li key={line}>{displayText(line)}</li>
                  ))}
                </ul>
              )}
              {step.error !== undefined && (
                <p className="explore-step-note">This step could not run with these settings.</p>
              )}
              <StepTable
                answerKey={answerKey}
                index={index}
                step={step}
                expanded={expanded[index] === true}
                onExpand={() => setExpanded((previous) => ({ ...previous, [index]: true }))}
                highlight={highlight}
              />
              {step.notes
                .filter(
                  // Said once, in the line under the answer.
                  (note) => !(step.instructor_rows_withheld === true && note.startsWith('Instructor-level results')),
                )
                .map((note) => (
                <p key={note} className="explore-step-note">
                  {displayText(note)}
                </p>
              ))}
              {step.fields_read.length > 0 && (
                <p className="explore-read">
                  <span className="explore-read-label">Data it read:</span>{' '}
                  {readLabels(step.fields_read).join('; ')}. Totals only, never a single student.
                </p>
              )}
            </li>
          ))}
        </ol>
        {(planned !== null || fellBack) && (
          <details className="fold explore-about">
            <summary>About this answer</summary>
            {planned !== null && <p>{planned}</p>}
            {fellBack && (
              <p>
                The Chief of Staff could not help with part of this answer, so that part
                was done by the fixed rules instead.
              </p>
            )}
          </details>
        )}
      </details>
    </div>
  )
}
