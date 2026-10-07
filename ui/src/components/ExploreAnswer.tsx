import { useEffect, useId, useRef, useState } from 'react'

import {
  answerColumns,
  answerNotes,
  cellDomId,
  dedupeLabels,
  displayText,
  formatCell,
  linkSentence,
  plannerLabel,
  quotedColumns,
  sourceLabel,
  SUPPRESSED,
  motionAllowed,
  traceLines,
  traceSummary,
  type ExploreTraceEvent,
  type ExploreClaim,
  type ExploreResponse,
  type ExploreStep,
} from '../explore'
import {
  alertQuote,
  answerActions,
  cardSteps,
  chartModel,
  planQuestion,
} from '../answerCard'
import { FIELD_LABELS, fieldLabels, normalizeField } from '../fieldLabels'
import { AnswerChart } from './AnswerChart'
import './AnswerCard.css'
import './Explore.css'
import { SearchIcon } from './icons'
import { LensMark } from './LensMark'
import { Thinking, TraceList } from './Thinking'

/** Rows a step's table shows before "Show all N". */
export const TABLE_PREVIEW_ROWS = 10

/** How long a cell stays highlighted after its number is opened. */
const HIGHLIGHT_MS = 2400

function readLabels(fields: readonly string[]): string[] {
  const known = fields.filter((field) => FIELD_LABELS[normalizeField(field)] !== undefined)
  const rest = fields.filter((field) => FIELD_LABELS[normalizeField(field)] === undefined)
  // An unlisted field still reads as words, never as an id.
  const plain = fieldLabels(rest).map((label) => label.replace(/\bids?\b/gi, '').replace(/\s+/g, ' ').trim())
  const labels = [...new Set([...fieldLabels(known), ...plain])].filter(Boolean)
  return dedupeLabels(labels)
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
  /** The live trace of this answer, when the server streamed one. */
  trace?: readonly ExploreTraceEvent[]
  /** How long the answer took, for "Thought for 6 s". */
  elapsedMs?: number
  /** The answer has just arrived: the trace collapses and the answer rises in. */
  live?: boolean
  /** The buttons under the answer, per role: null shows none. */
  actions?: AnswerActionProps | null
}

export interface AnswerActionProps {
  role: string
  /** Opens Send alert with this answer attached; null hides the button. */
  onSend: ((quote: string[]) => void) | null
  /** True for the roles that ask the briefing's follow-up questions. */
  asksBriefing: boolean
}

/** "Working it out…": the reply while an Explore question is answered,
 * with the live trace when the server streams one. */
export function ExploreWorking({
  trace,
  mark = true,
}: {
  trace?: readonly ExploreTraceEvent[]
  mark?: boolean
}) {
  return <Thinking trace={trace !== undefined ? traceLines(trace) : undefined} mark={mark} />
}

/**
 * The finished trace above an answer: one line, "Thought for 6 s · 4 steps",
 * that opens the full trace again. Right after the answer arrives it starts
 * open and folds shut (height and opacity, about 280 ms), so the live trace
 * visibly collapses into it; reopening unfolds with the same motion.
 */
export function Thought({
  trace,
  elapsedMs,
  live = false,
}: {
  trace: readonly ExploreTraceEvent[]
  elapsedMs: number
  live?: boolean
}) {
  const lines = traceLines(trace)
  const [open, setOpen] = useState(() => live && motionAllowed())
  const bodyId = useId()
  useEffect(() => {
    if (!open || !live) return
    // Fold the just-finished trace on the next frame, so the fold animates.
    const frame = window.requestAnimationFrame(() =>
      window.requestAnimationFrame(() => setOpen(false)),
    )
    return () => window.cancelAnimationFrame(frame)
    // Only once, when the answer first arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  if (lines.length === 0) return null
  return (
    <div className="thought">
      <button
        type="button"
        className="thought-toggle"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((value) => !value)}
      >
        <LensMark className={`thought-mark${live ? ' is-settling' : ''}`} />
        <span>{traceSummary(elapsedMs, lines.length)}</span>
        <svg className="chevron thought-chevron" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M4 2.5 L7.5 6 L4 9.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
        </svg>
      </button>
      <div id={bodyId} className="thought-body" data-open={open} inert={!open}>
        <div className="thought-inner">
          <TraceList lines={lines} animate={false} />
        </div>
      </div>
    </div>
  )
}

/** Questions to ask instead: the same card and rows as the home screen's
 * "Try asking", so a suggestion looks the same wherever it appears. */
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
  const titleId = useId()
  if (questions.length === 0) return null
  return (
    <div className="try-card explore-suggestions">
      <p className="explore-try-title try-card-title" id={titleId}>
        {title}
      </p>
      <ul className="try-list" aria-labelledby={titleId}>
        {questions.slice(0, 3).map((question) => (
          <li key={question}>
            <button
              type="button"
              className="try-row"
              disabled={busy}
              onClick={() => onAsk(question)}
            >
              <SearchIcon />
              <span>{question}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** A column of figures (its header sits over the figures, on the right). */
function numericColumn(step: ExploreStep, index: number): boolean {
  const kind = step.table.columns[index].kind
  if (kind !== undefined) return kind !== 'text'
  return step.table.rows.some((row) => typeof row[index] === 'number')
}

/** True while a table box has columns scrolled out of view on its right,
 * for the fade that says there is more. */
function useMoreOnRight() {
  const ref = useRef<HTMLDivElement>(null)
  const [more, setMore] = useState(false)
  useEffect(() => {
    const box = ref.current
    if (box === null) return
    const check = () => setMore(box.scrollLeft + box.clientWidth < box.scrollWidth - 1)
    check()
    box.addEventListener('scroll', check, { passive: true })
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(check)
    observer?.observe(box)
    const table = box.firstElementChild
    if (table !== null) observer?.observe(table)
    return () => {
      box.removeEventListener('scroll', check)
      observer?.disconnect()
    }
  }, [])
  return { ref, more }
}

function StepTable({
  answerKey,
  index,
  step,
  quoted,
  expanded,
  onExpand,
  highlight,
}: {
  answerKey: string
  index: number
  step: ExploreStep
  /** The column keys the answer quotes from this table, in order. */
  quoted: readonly string[]
  expanded: boolean
  onExpand: () => void
  highlight: ExploreClaim | null
}) {
  const [allColumns, setAllColumns] = useState(false)
  const { ref, more } = useMoreOnRight()
  const { columns, hidden } = answerColumns(step, quoted, allColumns)
  const rows = step.table.rows
  if (rows.length === 0 || columns.length === 0) {
    return <p className="explore-empty">This step found no rows to show.</p>
  }
  // Never fold away a single row (the whole-population row under a top 10).
  const folds = rows.length > TABLE_PREVIEW_ROWS + 1
  const shown = expanded || !folds ? rows : rows.slice(0, TABLE_PREVIEW_ROWS)
  return (
    <>
      <div className="explore-table-frame" data-more={more}>
        <div
          ref={ref}
          className="explore-table-wrap"
          role="region"
          aria-label={`Step ${index + 1} table`}
          tabIndex={0}
        >
          <table className="explore-table">
            <thead>
              <tr>
                {columns.map((c) => (
                  <th
                    key={step.table.columns[c].key}
                    scope="col"
                    className={numericColumn(step, c) ? 'explore-num-cell' : undefined}
                  >
                    {step.table.columns[c].label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {shown.map((row, r) => (
                <tr key={r}>
                  {columns.map((c) => {
                    const { key, kind } = step.table.columns[c]
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
                        {formatCell(value, kind)}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {((folds && !expanded) || hidden > 0) && (
        <p className="explore-table-more">
          {folds && !expanded && (
            <>
              <span>
                Showing {TABLE_PREVIEW_ROWS} of {rows.length.toLocaleString('en-US')} rows
              </span>{' '}
              <button
                type="button"
                className="link-button explore-show-all"
                aria-label={`Show all ${rows.length.toLocaleString('en-US')} rows`}
                onClick={onExpand}
              >
                Show all
              </button>
            </>
          )}
          {folds && !expanded && hidden > 0 && <span aria-hidden="true"> · </span>}
          {hidden > 0 && (
            <button
              type="button"
              className="link-button explore-show-all"
              onClick={() => setAllColumns(true)}
            >
              Show all columns ({hidden} more)
            </button>
          )}
        </p>
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
  trace,
  elapsedMs = 0,
  live = false,
  actions = null,
}: ExploreAnswerProps) {
  const [open, setOpen] = useState(false)
  const [panel, setPanel] = useState<'breakdown' | 'plan' | null>(null)
  const evidenceId = useId()
  const panelId = useId()
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

  const thought =
    trace !== undefined && trace.length > 0 ? (
      <Thought trace={trace} elapsedMs={elapsedMs} live={live} />
    ) : null
  const answerClass = `explore-answer${live && trace !== undefined && trace.length > 0 ? ' is-rising' : ''}`

  if (response.refused) {
    return (
      <div className="explore-answer">
        <div className="explore-declined" role="status">
          <h3>Not something CampusLens answers</h3>
          <p>{response.message ?? 'CampusLens answers with totals only.'}</p>
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
      <div className={answerClass}>
        {thought}
        <p className="explore-message">
          {response.message ?? 'CampusLens could not answer that question.'}
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
  const card = response.card
  const steps = cardSteps(response)
  const model = chartModel(response)
  const allowed =
    actions !== null ? answerActions(actions.role, card, actions.onSend !== null) : null
  const followUp = actions !== null ? planQuestion(card, actions.asksBriefing) : null

  const linked = (sentence: (typeof response.answer)[number], key: number) => (
    <span key={key}>
      {linkSentence(sentence, steps).map((part, p) =>
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
    </span>
  )

  const seeEvidence = () => {
    setOpen(true)
    window.requestAnimationFrame(() =>
      document.getElementById(evidenceId)?.scrollIntoView?.({ block: 'start' }),
    )
  }

  return (
    <div className={answerClass}>
      {thought}
      <div className="explore-sentences">
        {response.answer.map((sentence, s) => (
          <p key={s}>{linked(sentence, s)}</p>
        ))}
      </div>
      {notes.length > 0 && <p className="explore-notes">{notes.join(' ')}</p>}

      {card !== undefined && (card.key_points.length > 0 || model !== null) && (
        <div className="answer-card">
          {card.key_points.length > 0 && (
            <section aria-label="Key points">
              <h4 className="ac-section-title">Key points</h4>
              <ul className="ac-points">
                {card.key_points.map((point, index) => (
                  <li key={index}>{linked(point, index)}</li>
                ))}
              </ul>
            </section>
          )}
          {model !== null && <AnswerChart model={model} onOpen={openCell} />}
        </div>
      )}

      {source !== null && <p className="explore-source">{source}</p>}

      <details
        id={evidenceId}
        className="fold explore-how"
        open={open}
        onToggle={(event) => setOpen(event.currentTarget.open)}
      >
        <summary>How this was answered</summary>
        <ol className="explore-steps">
          {steps.map((step, index) => (
            <li key={index} className="explore-step">
              <h4 className="explore-step-title">
                {index < response.steps.length ? `Step ${index + 1}. ` : ''}
                {displayText(step.title)}
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
                quoted={quotedColumns(response, index)}
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
                  <span className="explore-read-label">Data it used:</span>{' '}
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

      {allowed !== null && actions !== null && (
        <>
          <ul className="ac-actions" aria-label="What to do with this answer">
            {allowed.send && actions.onSend !== null && (
              <li>
                <button
                  type="button"
                  className="btn-secondary secondary"
                  onClick={() => actions.onSend?.(alertQuote(response))}
                >
                  Send to department
                </button>
              </li>
            )}
            {allowed.trend && card?.followups.trend != null && (
              <li>
                <button
                  type="button"
                  className="btn-secondary secondary"
                  disabled={busy}
                  onClick={() => onAsk(card.followups.trend ?? '')}
                >
                  Show trend
                </button>
              </li>
            )}
            {allowed.breakdown && (
              <li>
                <button
                  type="button"
                  className="btn-secondary secondary"
                  aria-expanded={panel === 'breakdown'}
                  aria-controls={panelId}
                  onClick={() => setPanel((value) => (value === 'breakdown' ? null : 'breakdown'))}
                >
                  Break it down
                </button>
              </li>
            )}
            {allowed.plan && (
              <li>
                <button
                  type="button"
                  className="btn-secondary secondary"
                  disabled={followUp !== null && busy}
                  aria-expanded={followUp === null ? panel === 'plan' : undefined}
                  aria-controls={followUp === null ? panelId : undefined}
                  onClick={() =>
                    followUp !== null
                      ? onAsk(followUp)
                      : setPanel((value) => (value === 'plan' ? null : 'plan'))
                  }
                >
                  Make a plan
                </button>
              </li>
            )}
            <li>
              <button
                type="button"
                className="btn-secondary secondary"
                aria-controls={evidenceId}
                onClick={seeEvidence}
              >
                See evidence
              </button>
            </li>
          </ul>
          {panel === 'breakdown' && card !== undefined && (
            <div id={panelId} className="ac-panel" role="region" aria-label="Break it down">
              <p>Split the same figure by:</p>
              <ul className="ac-choices">
                {card.followups.breakdowns.map((choice) => (
                  <li key={choice.grouping}>
                    <button
                      type="button"
                      className="btn-secondary secondary"
                      disabled={busy}
                      onClick={() => {
                        setPanel(null)
                        onAsk(choice.question)
                      }}
                    >
                      {choice.label}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {panel === 'plan' && card !== undefined && (
            <div id={panelId} className="ac-panel" role="region" aria-label="Proposed plan">
              <p className="ac-proposed">Proposed, not approved</p>
              <p>
                Drafted from the key points. Nothing is saved or sent: a person decides, and
                the Send button shares it.
              </p>
              <ol>
                {card.plan.map((item, index) => (
                  <li key={index}>{linked(item, index)}</li>
                ))}
              </ol>
            </div>
          )}
        </>
      )}
    </div>
  )
}

