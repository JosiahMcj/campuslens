/**
 * The dashboard's drawn icons, one stroke weight (1.5) across the set:
 * check (a task resolved) and chevron (a disclosure's open state). They take
 * their color from `currentColor`, so hover and status recolor them for free.
 */
export function CheckIcon() {
  return (
    <svg
      className="icon-check"
      width="12"
      height="12"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M3 8.5 6.5 12 13 4.5"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export function ChevronIcon() {
  return (
    <svg
      className="chevron"
      width="12"
      height="12"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M6 3.5 10.5 8 6 12.5"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
