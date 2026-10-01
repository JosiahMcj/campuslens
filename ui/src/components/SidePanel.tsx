import { useEffect, useRef, type ReactNode } from 'react'

import { CloseIcon } from './icons'

interface SidePanelProps {
  title: string
  onClose: () => void
  children: ReactNode
}

/**
 * A slide-over panel for everything that is not the conversation (the full
 * briefing, the AI employees' task cards, the audit log). It opens from the
 * sidebar with one click and closes with the close button, Escape, or a click
 * on the backdrop; focus moves into the panel on open and back on close.
 */
export function SidePanel({ title, onClose, children }: SidePanelProps) {
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    panelRef.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      previous?.focus?.()
    }
  }, [onClose])

  return (
    <div className="panel-overlay" onClick={onClose}>
      <div
        ref={panelRef}
        className="side-panel"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="side-panel-header">
          <h2>{title}</h2>
          <button
            type="button"
            className="icon-button"
            aria-label={`Close ${title}`}
            onClick={onClose}
          >
            <CloseIcon />
          </button>
        </div>
        <div className="side-panel-body">{children}</div>
      </div>
    </div>
  )
}
