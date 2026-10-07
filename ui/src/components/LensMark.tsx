import { useId } from 'react'

/**
 * The CampusLens mark: a graduation cap over a magnifying glass, in the
 * logo's navy-to-teal gradient. The gradient stops are the --brand-* tokens
 * (index.css), so the mark reads on both the light and the dark ground. The
 * size comes from the caller's class. Decorative: the product name always
 * sits beside it in text.
 */
export function LensMark({ className = '' }: { className?: string }) {
  const gradient = `lens-mark-${useId().replace(/:/g, '')}`
  const paint = `url(#${gradient})`
  return (
    <svg
      className={`lens-mark ${className}`}
      viewBox="0 0 100 100"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id={gradient} gradientUnits="userSpaceOnUse" x1="10" y1="95" x2="92" y2="12">
          <stop offset="0" className="lens-mark-stop-deep" />
          <stop offset="0.55" className="lens-mark-stop-mid" />
          <stop offset="1" className="lens-mark-stop-teal" />
        </linearGradient>
      </defs>
      <g fill={paint} stroke={paint} strokeLinejoin="round" strokeLinecap="round">
        <path d="M11 26 L50 10 L89 26 L50 41 Z" strokeWidth="4" />
        <path d="M27 33 L50 41 L67 33 L63.5 59 A16.5 16.5 0 0 0 30.5 59 Z" stroke="none" />
        <circle cx="47" cy="59" r="21" fill="none" strokeWidth="9" />
        <path d="M62.5 74.5 L80 90" fill="none" strokeWidth="10.5" />
        <path d="M83 26.5 L83 48" fill="none" strokeWidth="2.6" />
        <circle cx="83" cy="50.5" r="3.4" stroke="none" />
        <path d="M80.2 54 L85.8 54 L87.8 67 L78.2 67 Z" strokeWidth="1.5" />
      </g>
      <path className="lens-mark-glint" d="M36.5 51 A13.5 13.5 0 0 1 52.5 46.5" />
      <path className="lens-mark-glint lens-mark-glint-soft" d="M36 64.5 A12 12 0 0 0 42.5 71" />
    </svg>
  )
}
