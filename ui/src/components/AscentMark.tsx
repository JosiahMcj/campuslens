/**
 * The Golden Eagle Cabinet mark, "Ascent": two stacked chevrons, an eagle
 * in flight seen from far off, gold over white on a navy tile. Brand
 * colours are fixed (the tile reads the same in light and dark themes);
 * the size comes from the caller's class. Decorative: the product name
 * always sits beside it in text.
 */
export function AscentMark({ className = '' }: { className?: string }) {
  return (
    <svg
      className={`ascent-mark ${className}`}
      viewBox="0 0 100 100"
      aria-hidden="true"
      focusable="false"
    >
      <rect width="100" height="100" rx="24" fill="#10213f" />
      <path
        d="M22 60 L50 34 L78 60"
        fill="none"
        stroke="#c9a85c"
        strokeWidth="9"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M34 70 L50 55 L66 70"
        fill="none"
        stroke="#ffffff"
        strokeWidth="9"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
