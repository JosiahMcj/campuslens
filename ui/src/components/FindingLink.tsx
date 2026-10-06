import type { ReactNode } from 'react'

import { findingLabel } from '../findingLabels'

interface FindingLinkProps {
  findingId: string
  onOpen: (findingId: string) => void
  children: ReactNode
  /** Extra class(es) for styled variants like the stat-row tiles. */
  className?: string
}

/**
 * A number on screen that opens the evidence for its figure. The number itself
 * is the link text; a screen reader also hears which figure it opens (by its
 * display label, never the bare finding code).
 */
export function FindingLink({ findingId, onOpen, children, className }: FindingLinkProps) {
  return (
    <button
      type="button"
      className={className !== undefined ? `finding-link ${className}` : 'finding-link'}
      onClick={() => onOpen(findingId)}
    >
      {children}
      <span className="visually-hidden">
        {' '}
        (open the evidence: {findingLabel(findingId)})
      </span>
    </button>
  )
}
