import {
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from 'react'

import type { ApprovedQuestion } from '../api'
import { SendIcon, SearchIcon } from './icons'
import './Explore.css'

interface ChatComposerProps {
  /** The approved questions from GET /questions; null until loaded. */
  questions: ApprovedQuestion[] | null
  sending: boolean
  /** True on the empty screen (before the first answer): the approved
   * questions show as starter cards. */
  starters: boolean
  onAsk: (question: string) => void
  /** The last ask failed: a plain sentence, shown above the field. */
  error?: string | null
  /** Re-send the failed question ("Ask again"); null hides the button. */
  onAskAgain?: (() => void) | null
  /** The approved questions could not be loaded: a quiet line with Retry
   * takes the starter cards' place (the field still works). */
  questionsFailed?: boolean
  onRetryQuestions?: (() => void) | null
  /** The Explore example questions for the "Try" row on the empty screen:
   * null while loading, [] when there are none to show. */
  examples?: string[] | null
  /** The examples could not be loaded: a quiet line with Retry. */
  examplesFailed?: boolean
  onRetryExamples?: (() => void) | null
  /** Hide the one-line note under the field (phones, once the
   * conversation has started; the CSS decides by width). */
  quietNote?: boolean
}

/** The field grows with the question up to this many lines, then scrolls. */
const MAX_LINES = 6

/**
 * The chat composer: one question field (id question-input, as the demo
 * script and verification expect) with the send button inside it. The field
 * is a textarea that grows with the question (up to six lines): Enter asks,
 * Shift+Enter starts a new line. It takes
 * any question: an approved briefing question runs the briefing, anything
 * else is answered by Explore (the page decides). On the empty screen
 * (before the first answer) the approved questions show as one-click
 * starter cards with a quiet "Try" row of example questions under them;
 * after that the composer is the field alone, so the answer keeps the
 * screen.
 */
export function ChatComposer({
  questions,
  sending,
  starters,
  onAsk,
  error = null,
  onAskAgain = null,
  questionsFailed = false,
  onRetryQuestions = null,
  examples = [],
  examplesFailed = false,
  onRetryExamples = null,
  quietNote = false,
}: ChatComposerProps) {
  const [question, setQuestion] = useState('')
  const approved = questions ?? []
  const fieldRef = useRef<HTMLTextAreaElement>(null)

  // Grow the field to fit what is typed, up to MAX_LINES; past that it scrolls.
  useLayoutEffect(() => {
    const field = fieldRef.current
    if (field === null || typeof window.getComputedStyle !== 'function') return
    field.style.height = 'auto'
    const style = window.getComputedStyle(field)
    const line = Number.parseFloat(style.lineHeight)
    const padding =
      Number.parseFloat(style.paddingTop) + Number.parseFloat(style.paddingBottom)
    const border =
      Number.parseFloat(style.borderTopWidth) + Number.parseFloat(style.borderBottomWidth)
    const max = Number.isFinite(line) ? line * MAX_LINES + padding + border : Infinity
    const wanted = field.scrollHeight + (Number.isFinite(border) ? border : 0)
    if (wanted > 0) field.style.height = `${Math.min(wanted, max)}px`
    field.style.overflowY = wanted > max ? 'auto' : 'hidden'
  }, [question])

  const send = (text: string) => {
    const trimmed = text.trim()
    if (trimmed.length === 0 || sending) return
    onAsk(trimmed)
    setQuestion('')
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    send(question)
  }

  // Enter asks; Shift+Enter is a new line; Enter that confirms an input
  // method's composition (Japanese, Chinese, ...) never asks.
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
    event.preventDefault()
    send(question)
  }

  return (
    <div className="composer-wrap">
      {starters && approved.length > 0 && (
        <div className="starters" role="group" aria-label="Approved questions">
          {approved.map((item) => (
            <button
              key={item.id}
              type="button"
              className="starter"
              disabled={sending}
              onClick={() => send(item.text)}
            >
              <span className="starter-kicker">Approved question</span>
              <span className="starter-text">{item.text}</span>
            </button>
          ))}
        </div>
      )}
      {starters && questionsFailed && (
        <p className="questions-retry hint" role="status">
          The approved questions didn’t load. You can still type one below.{' '}
          {onRetryQuestions !== null && (
            <button type="button" className="link-button" onClick={onRetryQuestions}>
              Retry
            </button>
          )}
        </p>
      )}
      {starters && examples !== null && examples.length > 0 && (
        <div className="explore-try try-card">
          <p className="explore-try-title try-card-title" id="explore-try-title">
            Try asking
          </p>
          <ul className="try-list" aria-labelledby="explore-try-title">
            {examples.map((text) => (
              <li key={text}>
                <button
                  type="button"
                  className="try-row"
                  disabled={sending}
                  onClick={() => send(text)}
                >
                  <SearchIcon />
                  <span>{text}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {starters && examples === null && !examplesFailed && (
        <div className="explore-try" role="status" aria-busy="true">
          <span className="visually-hidden">Loading example questions…</span>
          <div className="skeleton skeleton-line short" />
        </div>
      )}
      {starters && examplesFailed && (
        <p className="explore-try explore-try-line" role="status">
          The example questions didn’t load.{' '}
          {onRetryExamples !== null && (
            <button type="button" className="link-button" onClick={onRetryExamples}>
              Retry
            </button>
          )}
        </p>
      )}
      {error !== null && (
        <div className="ask-error state-error" role="alert">
          <p className="error-line">{error}</p>
          {onAskAgain !== null && (
            <button
              type="button"
              className="secondary btn-secondary"
              disabled={sending}
              onClick={onAskAgain}
            >
              Ask again
            </button>
          )}
        </div>
      )}
      <form className="composer" onSubmit={submit}>
        <label htmlFor="question-input" className="visually-hidden">
          Ask CampusLens a question
        </label>
        <textarea
          ref={fieldRef}
          id="question-input"
          rows={1}
          autoComplete="off"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask a question"
          disabled={sending}
        />
        <button
          type="submit"
          className="send-button"
          aria-label="Ask CampusLens"
          title="Ask CampusLens"
          disabled={sending || question.trim().length === 0}
        >
          <SendIcon />
        </button>
      </form>
      <p className={`composer-note${quietNote ? ' is-quiet' : ''}`}>
        Every number is computed from the records and checked.
      </p>
    </div>
  )
}
