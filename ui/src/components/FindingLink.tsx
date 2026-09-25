import type { ReactNode } from 'react'

interface FindingLinkProps {
  findingId: string
  onOpen: (findingId: string) => void
  children: ReactNode
  /** Extra class(es) for styled variants like the stat-row tiles. */
  className?: string
}

/** A number in the briefing text that opens the evidence drawer for its finding. */
export function FindingLink({ findingId, onOpen, children, className }: FindingLinkProps) {
  return (
    <button
      type="button"
      className={className !== undefined ? `finding-link ${className}` : 'finding-link'}
      onClick={() => onOpen(findingId)}
      aria-label={`Open the evidence for finding ${findingId}`}
    >
      {children}
    </button>
  )
}
