/**
 * The Golden Eagle AI Cabinet mark, "Ascent": two stacked chevrons, an
 * eagle in flight seen from far off, on a tile in the accent colour. Drawn
 * from the theme tokens (the rules are in landing.css, `.ascent-mark`), so
 * it reads on both the light and the dark ground; a hairline edge keeps the
 * tile's outline when a surface is close to its colour. The size comes from
 * the caller's class. Decorative: the product name always sits beside it in
 * text.
 */
export function AscentMark({ className = '' }: { className?: string }) {
  return (
    <svg
      className={`ascent-mark ${className}`}
      viewBox="0 0 100 100"
      aria-hidden="true"
      focusable="false"
    >
      <rect className="ascent-mark-tile" x="1" y="1" width="98" height="98" rx="23" />
      <path className="ascent-mark-upper" d="M22 60 L50 34 L78 60" />
      <path className="ascent-mark-lower" d="M34 70 L50 55 L66 70" />
    </svg>
  )
}
