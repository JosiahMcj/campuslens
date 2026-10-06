import { useEffect, useLayoutEffect, useRef, type KeyboardEvent, type ReactNode } from 'react'

import { PANEL_MOTION_MS, prefersReducedMotion, trapTab } from '../states'
import { CloseIcon } from './icons'

interface SidePanelProps {
  title: string
  onClose: () => void
  /** True while the panel plays its exit animation (it no longer takes input). */
  closing?: boolean
  children: ReactNode
}

/**
 * A slide-over panel for everything that is not the conversation (the full
 * briefing, the AI employees' task cards, the audit log). It opens from the
 * sidebar with one click and closes with the close button, Escape, or a click
 * on the backdrop. It is modal: focus moves in on open, Tab stays inside, the
 * page behind is made inert by the app, and focus returns on close. Escape is
 * handled here and stops, so a layer underneath never closes with it.
 */
export function SidePanel({ title, onClose, closing = false, children }: SidePanelProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const onCloseRef = useRef(onClose)
  useLayoutEffect(() => {
    onCloseRef.current = onClose
  })

  // Focus moves in once, on mount; it returns where it was on unmount.
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    panelRef.current?.focus()
    return () => {
      if (previous !== null && document.contains(previous)) previous.focus?.()
      // The opener is gone or hidden (a row in the closed phone drawer):
      // focus lands on the page's main column, never on <body>.
      if (document.activeElement !== previous) {
        document.getElementById('main-content')?.focus()
      }
    }
  }, [])

  // Switching panels cross-fades the body and moves focus to the new panel.
  const firstTitle = useRef(title)
  useEffect(() => {
    if (title === firstTitle.current) return
    firstTitle.current = title
    panelRef.current?.focus()
    const body = bodyRef.current
    if (body !== null && !prefersReducedMotion() && typeof body.animate === 'function') {
      body.animate([{ opacity: 0 }, { opacity: 1 }], {
        duration: PANEL_MOTION_MS,
        easing: 'ease-out',
      })
    }
  }, [title])

  // Escape while focus is outside every layer (e.g. on the page body) still
  // closes the top panel; a key handled inside a layer never reaches here.
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      const active = document.activeElement
      if (active === null || active === document.body) onCloseRef.current()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      // An evidence drawer open over this panel is the top layer: let the
      // key reach its own handler and leave this panel open.
      if (document.querySelector('.drawer-overlay, [data-evidence-drawer]') !== null) return
      event.stopPropagation()
      event.preventDefault()
      onClose()
      return
    }
    if (panelRef.current !== null) trapTab(event, panelRef.current)
  }

  return (
    <div
      className={`panel-overlay${closing ? ' is-closing' : ''}`}
      onClick={closing ? undefined : onClose}
    >
      <div
        ref={panelRef}
        className={`side-panel${closing ? ' is-closing' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        inert={closing}
        onKeyDown={onKeyDown}
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
        <div ref={bodyRef} className="side-panel-body">
          {children}
        </div>
      </div>
    </div>
  )
}
