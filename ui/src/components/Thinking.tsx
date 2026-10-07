import { useEffect, useRef, useState } from 'react'

import { motionAllowed, numberParts, TRACE_STAGGER_MS, type TraceLine } from '../explore'
import './Explore.css'
import { LensMark } from './LensMark'

/** What happens while an Explore question is answered, in order: shown on a
 * timer when the server does not stream its trace. */
const EXPLORE_STEPS = [
  'Reading your question',
  'Choosing the approved analysis',
  'Computing from the records',
  'Checking every number',
] as const

/**
 * The thinking animation shown while CampusLens works on a question: the
 * mark searches and its glint sweeps, "Thinking" shimmers, and the seconds
 * so far tick. With a live trace (`trace`), each stage the server reports
 * lands as a line under the title, the newest one working, the earlier ones
 * checked off; the mark gives a small focus pulse on each new line. Without
 * one, a plain line names the step in hand on a timer, as a guide to what is
 * happening, never as a progress figure. Under reduced motion everything
 * holds still and only the words change. Screen readers hear step titles
 * only, politely.
 */
export function Thinking({
  steps = EXPLORE_STEPS,
  detail,
  trace,
}: {
  steps?: readonly string[]
  /** Replaces the step line when the caller knows more (the live task list). */
  detail?: React.ReactNode
  /** The live trace: shown in place of the timed steps once it has a line. */
  trace?: readonly TraceLine[]
}) {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    const started = Date.now()
    const timer = window.setInterval(() => {
      setSeconds(Math.floor((Date.now() - started) / 1000))
    }, 500)
    return () => window.clearInterval(timer)
  }, [])
  const live = trace !== undefined && trace.length > 0
  const step =
    steps.length > 0 ? steps[Math.min(steps.length - 1, Math.floor(seconds / 2))] : null

  // A small focus pulse of the mark on each new line of the trace.
  const markRef = useRef<HTMLSpanElement>(null)
  const count = trace?.length ?? 0
  useEffect(() => {
    if (count === 0 || !motionAllowed()) return
    markRef.current?.animate?.(
      [{ transform: 'scale(1)' }, { transform: 'scale(1.06)' }, { transform: 'scale(1)' }],
      { duration: 260, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' },
    )
  }, [count])

  const announced = live ? [...trace].reverse().find((line) => line.announce) : undefined

  return (
    <div className="thinking" role="status" aria-busy="true">
      <span ref={markRef} className="thinking-mark-wrap">
        <LensMark className="thinking-mark" thinking />
      </span>
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
        {live ? (
          <>
            <TraceList lines={trace} working />
            {announced !== undefined && <span className="visually-hidden">{announced.text}</span>}
          </>
        ) : (
          (detail ??
          (step !== null && (
            <p className="thinking-step" aria-hidden="true" key={step}>
              {step}
            </p>
          )))
        )}
      </div>
    </div>
  )
}

/**
 * The trace as a list: each line enters from just below (staggered when
 * several arrive together), a thin guide on the left grows down to it, the
 * newest line shimmers with a turning arc while `working`, and a finished
 * line's arc becomes a check mark and the line settles to the soft ink.
 * Numbers count up quickly the first time a line appears. Decorative for
 * screen readers: the caller announces step titles.
 */
export function TraceList({
  lines,
  working = false,
  animate = true,
}: {
  lines: readonly TraceLine[]
  /** The last line is still being worked on. */
  working?: boolean
  /** Entry motion for lines that appear while mounted. */
  animate?: boolean
}) {
  // A keyed line mounts once, so its entry plays once. Lines that arrive
  // together are staggered from the first of their batch; the batch start
  // is the line count before the latest change (adjusted during render, the
  // documented way to follow a prop).
  const [count, setCount] = useState(lines.length)
  const [batchStart, setBatchStart] = useState(lines.length)
  if (lines.length !== count) {
    setBatchStart(Math.min(count, lines.length))
    setCount(lines.length)
  }
  const moving = animate && motionAllowed()
  return (
    <ol className="trace" aria-hidden="true">
      {lines.map((line, index) => {
        const delay = Math.max(0, index - batchStart) * TRACE_STAGGER_MS
        const active = working && index === lines.length - 1
        return (
          <li
            key={line.key}
            className={`trace-line${moving ? ' is-entering' : ''}`}
            data-state={active ? 'active' : 'done'}
            style={moving ? ({ '--trace-delay': `${delay}ms` } as React.CSSProperties) : undefined}
          >
            <TraceMark />
            <span className="trace-text">
              {numberParts(line.text).map((part, i) =>
                part.value === null ? (
                  <span key={i}>{part.text}</span>
                ) : (
                  <CountUp key={i} text={part.text} value={part.value} run={moving} />
                ),
              )}
            </span>
          </li>
        )
      })}
    </ol>
  )
}

/** The working arc and, once the line is done, the check it becomes. */
function TraceMark() {
  return (
    <svg className="trace-mark" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <circle className="trace-arc" cx="8" cy="8" r="6" pathLength="100" />
      <path className="trace-check" d="M4 8.5 L7 11.2 L12.2 5" pathLength="100" />
    </svg>
  )
}

/** A number that counts up from zero over about 400 ms, once, then holds
 * its exact text. */
function CountUp({ text, value, run }: { text: string; value: number; run: boolean }) {
  const [current, setCurrent] = useState<string>(run ? '0' : text)
  useEffect(() => {
    if (!run) return
    const grouped = text.includes(',')
    const started = performance.now()
    let frame = 0
    const tick = (now: number) => {
      const t = Math.min(1, (now - started) / 400)
      const eased = 1 - (1 - t) ** 3
      if (t >= 1) {
        setCurrent(text)
        return
      }
      const n = Math.round(value * eased)
      setCurrent(grouped ? n.toLocaleString('en-US') : String(n))
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [run, text, value])
  return <span className="trace-number">{current}</span>
}
