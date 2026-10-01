import { useState, type FormEvent } from 'react'

import type { ApprovedQuestion } from '../api'
import { APPROVED_QUESTION, isApprovedQuestion } from '../states'
import { SendIcon } from './icons'

interface ChatComposerProps {
  /** The approved questions from GET /questions; null until loaded. */
  questions: ApprovedQuestion[] | null
  sending: boolean
  /** True on the empty screen: the approved questions show as starter cards. */
  starters: boolean
  onAsk: (question: string) => void
}

/**
 * The chat composer: one question field (id question-input, as the demo
 * script and verification expect) with the send button inside it, and the
 * approved questions as one-click prompts. The field accepts only the
 * approved questions; anything else is still submitted, so the API refuses
 * it and records the refusal as a data.refused audit event (Beat 6).
 */
export function ChatComposer({ questions, sending, starters, onAsk }: ChatComposerProps) {
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
      <form className="composer" onSubmit={submit}>
        <label htmlFor="question-input" className="visually-hidden">
          Ask the cabinet an approved question
        </label>
        <input
          id="question-input"
          type="text"
          autoComplete="off"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Ask the cabinet an approved question…"
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
      {!starters && approved.length > 0 && (
        <div className="quick-questions">
          {approved.map((item) => (
            <button
              key={item.id}
              type="button"
              className="chip"
              disabled={sending}
              onClick={() => send(item.text)}
            >
              {item.text}
            </button>
          ))}
        </div>
      )}
      <p className="composer-note" role={offScope ? 'note' : undefined}>
        {offScope
          ? 'This question is outside the approved use case. Sending it will be refused, and the refusal is recorded in the audit log.'
          : 'The cabinet answers approved questions only. Every number is checked against the data.'}
      </p>
    </div>
  )
}
