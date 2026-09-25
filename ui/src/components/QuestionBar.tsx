import { useState, type FormEvent } from 'react'

import type { ApprovedQuestion, AskResponse } from '../api'
import { APPROVED_QUESTION, isApprovedQuestion } from '../states'

export type AskState =
  | { kind: 'idle' }
  | { kind: 'sending' }
  | { kind: 'accepted'; response: Extract<AskResponse, { accepted: true }> }
  | { kind: 'refused'; refusal: string }
  | { kind: 'error'; message: string }

interface QuestionBarProps {
  state: AskState
  /** The approved questions from GET /questions; null until loaded. */
  questions: ApprovedQuestion[] | null
  onAsk: (question: string) => void
}

/**
 * The one question field (Beat 1 and the Beat 6 refusal) with one button per
 * approved question. The field accepts only the approved questions; anything
 * else is still submitted so the API logs the refusal as a data.refused
 * audit event.
 */
export function QuestionBar({ state, questions, onAsk }: QuestionBarProps) {
  const [question, setQuestion] = useState('')
  const approvedTexts = questions?.map((q) => q.text) ?? [APPROVED_QUESTION]

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const trimmed = question.trim()
    if (trimmed.length === 0 || state.kind === 'sending') return
    onAsk(trimmed)
  }

  return (
    <section aria-labelledby="question-heading" className="question-bar">
      <h2 id="question-heading">Ask the Cabinet</h2>
      <form onSubmit={submit}>
        <label htmlFor="question-input">
          The approved questions for this prototype
        </label>
        <div className="question-row">
          <input
            id="question-input"
            type="text"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder={APPROVED_QUESTION}
            disabled={state.kind === 'sending'}
          />
          <button type="submit" disabled={state.kind === 'sending'}>
            {state.kind === 'sending' ? 'Asking the cabinet…' : 'Ask the Cabinet'}
          </button>
          {questions?.map((approved) => (
            <button
              key={approved.id}
              type="button"
              className="secondary"
              disabled={state.kind === 'sending'}
              onClick={() => setQuestion(approved.text)}
            >
              {approved.text}
            </button>
          ))}
        </div>
      </form>

      {question.trim().length > 0 && !isApprovedQuestion(question, approvedTexts) && (
        <p className="hint" role="note">
          This question is outside the approved use case. Submitting it will be
          refused, and the refusal is recorded in the audit log.
        </p>
      )}

      <div aria-live="polite">
        {state.kind === 'sending' && (
          <p className="status-line">The cabinet is working…</p>
        )}
        {state.kind === 'error' && (
          <p className="error-line" role="alert">
            The question could not be sent: {state.message}
          </p>
        )}
        {state.kind === 'refused' && (
          <div className="refusal-card" role="status">
            <h3>Refused by the Chief of Staff</h3>
            <p>{state.refusal}</p>
          </div>
        )}
        {state.kind === 'accepted' && (
          <p className="status-line">
            The question was accepted. The cabinet's dispatch and the briefing
            are below.
          </p>
        )}
      </div>
    </section>
  )
}
