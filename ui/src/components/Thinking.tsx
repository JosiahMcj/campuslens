import { useEffect, useState } from 'react'

import { LensMark } from './LensMark'

/** What happens while an Explore question is answered, in order. */
const EXPLORE_STEPS = [
  'Reading your question',
  'Choosing the approved analysis',
  'Computing from the records',
  'Checking every number',
] as const

/**
 * The thinking animation shown while CampusLens works on a question: the
 * mark searches and its glint sweeps, "Thinking" shimmers, and a plain line
 * names the step in hand with the seconds so far. The steps advance on a
 * timer as a guide to what is happening, never as a progress figure. Under
 * reduced motion everything holds still and only the words change.
 */
export function Thinking({
  steps = EXPLORE_STEPS,
  detail,
}: {
  steps?: readonly string[]
  /** Replaces the step line when the caller knows more (the live task list). */
  detail?: React.ReactNode
}) {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    const started = Date.now()
    const timer = window.setInterval(() => {
      setSeconds(Math.floor((Date.now() - started) / 1000))
    }, 500)
    return () => window.clearInterval(timer)
  }, [])
  const step = steps.length > 0 ? steps[Math.min(steps.length - 1, Math.floor(seconds / 2))] : null

  return (
    <div className="thinking" role="status" aria-busy="true">
      <LensMark className="thinking-mark" thinking />
      <div className="thinking-text">
        <p className="thinking-title">
          <span className="thinking-shimmer">Thinking</span>
          <span className="visually-hidden">…</span>
          {seconds >= 1 && (
            <span className="thinking-seconds" aria-hidden="true">
              {seconds} s
            </span>
          )}
        </p>
        {detail ?? (step !== null && (
          <p className="thinking-step" aria-hidden="true" key={step}>
            {step}
          </p>
        ))}
      </div>
    </div>
  )
}
