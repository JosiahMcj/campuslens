/**
 * The dashboard's drawn icons, one stroke weight (1.5) across the set:
 * check (a task resolved), chevron (a disclosure's open state), and sun and
 * moon (the theme switch). They take
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

export function SunIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="3" stroke="currentColor" strokeWidth="1.5" />
      <path
        d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1.06 1.06M11.54 11.54l1.06 1.06M3.4 12.6l1.06-1.06M11.54 4.46l1.06-1.06"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  )
}

export function MoonIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M13.5 9.6A5.5 5.5 0 0 1 6.4 2.5a5.5 5.5 0 1 0 7.1 7.1Z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  )
}

/** Sidebar and composer icons, drawn on the same 16-unit grid. */
function Glyph({ d, size = 16 }: { d: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d={d}
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export function SendIcon() {
  return <Glyph d="M8 13V3.5M3.5 8 8 3.5 12.5 8" size={18} />
}

export function PlusIcon() {
  return <Glyph d="M8 3v10M3 8h10" />
}

export function MenuIcon() {
  return <Glyph d="M2.5 4h11M2.5 8h11M2.5 12h11" size={18} />
}

export function CloseIcon() {
  return <Glyph d="M4 4l8 8M12 4l-8 8" />
}

export function DocumentIcon() {
  return <Glyph d="M4 1.75h5L12 4.75v9.5H4zM9 1.75v3h3M6 8h4M6 10.75h4" />
}

export function TeamIcon() {
  return (
    <Glyph d="M5.5 7.25a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM1.75 13c.3-2.1 1.8-3.5 3.75-3.5S8.95 10.9 9.25 13M11 7.25a1.75 1.75 0 1 0 0-3.5M11.5 9.6c1.5.3 2.5 1.5 2.75 3.4" />
  )
}

export function LogIcon() {
  return <Glyph d="M5.5 4h8M5.5 8h8M5.5 12h8M2.5 4h.01M2.5 8h.01M2.5 12h.01" />
}

export function SettingsIcon() {
  return (
    <Glyph d="M8 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM8 1.75v1.5M8 12.75v1.5M1.75 8h1.5M12.75 8h1.5M3.6 3.6l1.05 1.05M11.35 11.35l1.05 1.05M3.6 12.4l1.05-1.05M11.35 4.65l1.05-1.05" />
  )
}

export function ChatIcon() {
  return <Glyph d="M2.5 3.5h11v7.25H7L4 13.25v-2.5H2.5z" />
}
