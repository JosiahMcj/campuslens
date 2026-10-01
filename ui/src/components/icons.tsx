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

/* The sidebar navigation's icons, in the reference's round, 2px-stroke style
   (drawn here; the reference's icon set is a paid package). */
function NavGlyph({ children, size = 18 }: { children: React.ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  )
}

export function EditIcon() {
  return (
    <NavGlyph>
      <path d="M11 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5" />
      <path d="M17.6 3.4a2 2 0 0 1 2.9 2.9L12 14.8l-3.5.7.7-3.5z" />
    </NavGlyph>
  )
}

export function SearchIcon({ size = 16 }: { size?: number }) {
  return (
    <NavGlyph size={size}>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m20 20-4.2-4.2" />
    </NavGlyph>
  )
}

export function ChevronDownIcon({ size = 16 }: { size?: number }) {
  return (
    <NavGlyph size={size}>
      <path d="m8 10 4 4 4-4" />
    </NavGlyph>
  )
}

export function SidebarToggleIcon() {
  return (
    <NavGlyph>
      <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
      <path d="M9.5 4.5v15M16 10l-2 2 2 2" />
    </NavGlyph>
  )
}

export function CheckSmallIcon() {
  return (
    <NavGlyph>
      <path d="m7.5 12.5 3 3 6-7" />
    </NavGlyph>
  )
}

export function SignOutIcon({ size = 16 }: { size?: number }) {
  return (
    <NavGlyph size={size}>
      <path d="M14 4.5h3.5a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H14" />
      <path d="M10 8l-4 4 4 4M6 12h9" />
    </NavGlyph>
  )
}

export function CrossSmallIcon({ size = 16 }: { size?: number }) {
  return (
    <NavGlyph size={size}>
      <path d="m8 8 8 8M16 8l-8 8" />
    </NavGlyph>
  )
}

export function BriefingNavIcon() {
  return (
    <NavGlyph>
      <path d="M7 3.5h7l4 4V19a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 19V5A1.5 1.5 0 0 1 7.5 3.5" />
      <path d="M13.5 3.5v4h4M9.5 12.5h5M9.5 16h5" />
    </NavGlyph>
  )
}

export function TeamNavIcon() {
  return (
    <NavGlyph>
      <circle cx="9" cy="8.5" r="3" />
      <path d="M3.5 19c.5-3 2.7-5 5.5-5s5 2 5.5 5M16 5.8a3 3 0 0 1 0 5.4M17.5 14.3c1.8.6 2.8 2.3 3 4.7" />
    </NavGlyph>
  )
}

export function AuditNavIcon() {
  return (
    <NavGlyph>
      <path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01" />
    </NavGlyph>
  )
}

export function GearIcon({ size = 16 }: { size?: number }) {
  return (
    <NavGlyph size={size}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" />
    </NavGlyph>
  )
}
