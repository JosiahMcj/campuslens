import { useState, type FormEvent } from 'react'

import type { ApprovedQuestion } from '../api'
import { APPROVED_QUESTION, isApprovedQuestion } from '../states'
import { SendIcon } from './icons'

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
}

/**
 * The chat composer: one question field (id question-input, as the demo
 * script and verification expect) with the send button inside it. On the
 * empty screen (before the first answer) the approved questions show as
 * one-click starter cards; after that the composer is the field alone, so
 * the answer keeps the screen. The field accepts only the
 * approved questions; anything else is still submitted, so the API refuses
 * it and records the refusal as a data.refused audit event (Beat 6).
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
}: ChatComposerProps) {
  const [question, setQuestion] = useState('')
  const approved = questions ?? []
  const approvedTexts = questions?.map((q) => q.text) ?? [APPROVED_QUESTION]
  const offScope = question.trim().length > 0 && !isApprovedQuestion(question, approvedTexts)

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
        <div className="starters" role="list" aria-label="Approved questions">
          {approved.map((item) => (
            <button
              key={item.id}
              type="button"
              role="listitem"
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
          Ask the Cabinet an approved question
        </label>
        <input
          id="question-input"
          type="text"
          autoComplete="off"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Ask an approved question…"
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
      <p className="composer-note" role={offScope ? 'note' : undefined}>
        {offScope
          ? 'Not an approved question: it will be refused and logged.'
          : 'Approved questions only. Every number is checked.'}
      </p>
    </div>
  )
}
