import { useState, type FormEvent } from 'react'

import type { ApprovedQuestion } from '../api'
import { SendIcon } from './icons'
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
}

/**
 * The chat composer: one question field (id question-input, as the demo
 * script and verification expect) with the send button inside it. It takes
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
}: ChatComposerProps) {
  const [question, setQuestion] = useState('')
  const approved = questions ?? []

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
        <div className="explore-try">
          <p className="explore-try-title" id="explore-try-title">
            Try
          </p>
          <ul className="explore-chip-row" aria-labelledby="explore-try-title">
            {examples.map((text) => (
              <li key={text}>
                <button
                  type="button"
                  className="chip explore-chip"
                  disabled={sending}
                  onClick={() => send(text)}
                >
                  {text}
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
          Ask the Cabinet a question
        </label>
        <input
          id="question-input"
          type="text"
          autoComplete="off"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="e.g. Which course has the highest withdrawal rate?"
          disabled={sending}
        />
        <button
          type="submit"
          className="send-button"
          aria-label="Ask the Cabinet"
          title="Ask the Cabinet"
          disabled={sending || question.trim().length === 0}
        >
          <SendIcon />
        </button>
      </form>
      <p className="composer-note">Every number is computed from the records and checked.</p>
    </div>
  )
}
