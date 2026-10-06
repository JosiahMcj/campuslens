import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  type KeyboardEvent,
  type ReactNode,
} from 'react'

import { PANEL_MOTION_MS, prefersReducedMotion, trapTab } from '../states'
import { CloseIcon } from './icons'

interface SidePanelProps {
  title: string
  onClose: () => void
  /** True while the panel plays its exit animation (it no longer takes input). */
  closing?: boolean
  /** True while another layer (the evidence) sits on top: this one is inert. */
  covered?: boolean
  /** The evidence layer, which opens over the page or over another panel. */
  evidence?: boolean
  children: ReactNode
}

/**
 * The open layers, bottom to top. Only the top one answers Escape when focus
 * is outside every layer; a key pressed inside a layer is that layer's alone.
 */
const openLayers: HTMLElement[] = []

function isTopLayer(element: HTMLElement | null): boolean {
  return element !== null && openLayers[openLayers.length - 1] === element
}

/**
 * A slide-over panel for everything that is not the conversation (the full
 * briefing, the AI employees' task cards, the audit log). It opens from the
 * sidebar with one click and closes with the close button, Escape, or a click
 * on the backdrop. It is modal: focus moves in on open, Tab stays inside, the
 * page behind is made inert by the app, and focus returns on close. Escape is
 * handled here and stops, so a layer underneath never closes with it.
 */
export function SidePanel({
  title,
  onClose,
  closing = false,
  covered = false,
  evidence = false,
  children,
}: SidePanelProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const onCloseRef = useRef(onClose)
  useLayoutEffect(() => {
    onCloseRef.current = onClose
  })

  // Focus moves in on mount. It goes back where it was (for the evidence
  // over a panel, the number in the panel that opened it) as soon as the
  // layer starts to close, so the exit animation never holds focus. A layer
  // reopened during its exit animation (closing turns false again) takes
  // its place on top and focus back.
  const previousRef = useRef<HTMLElement | null>(null)
  // The element this layer holds in the stack (kept here: on unmount the
  // DOM ref is already cleared when the cleanup runs).
  const heldRef = useRef<HTMLElement | null>(null)
  const acquire = useCallback(() => {
    const panel = panelRef.current
    if (panel === null || heldRef.current !== null) return
    heldRef.current = panel
    previousRef.current = document.activeElement as HTMLElement | null
    openLayers.push(panel)
    panel.focus()
  }, [])
  const release = useCallback(() => {
    const panel = heldRef.current
    if (panel === null) return
    heldRef.current = null
    const index = openLayers.indexOf(panel)
    if (index !== -1) openLayers.splice(index, 1)
    const previous = previousRef.current
    if (previous !== null && document.contains(previous)) previous.focus?.()
    if (document.activeElement !== previous) {
      // The opener is gone or hidden (a row in the closed phone drawer):
      // focus lands on the layer below, else the page's main column,
      // never on <body>.
      const below = openLayers[openLayers.length - 1]
      ;(below ?? document.getElementById('main-content'))?.focus()
    }
  }, [])
  useEffect(() => {
    if (closing) release()
    else acquire()
  }, [closing, acquire, release])
  useEffect(() => release, [release])

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
  // closes the top layer, and only that one; a key handled inside a layer
  // never reaches here.
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      if (!isTopLayer(panelRef.current)) return
      const active = document.activeElement
      if (active === null || active === document.body) {
        event.preventDefault()
        onCloseRef.current()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      // A layer open over this one (the evidence) is the top layer: leave
      // this panel open and the key to that layer.
      if (!isTopLayer(panelRef.current)) return
      event.stopPropagation()
      event.preventDefault()
      onClose()
      return
    }
    if (panelRef.current !== null) trapTab(event, panelRef.current)
  }

  return (
    <div
      className={`panel-overlay${evidence ? ' evidence-overlay' : ''}${closing ? ' is-closing' : ''}`}
      data-evidence-drawer={evidence ? '' : undefined}
      inert={covered}
      onClick={closing || covered ? undefined : onClose}
    >
      <div
        ref={panelRef}
        className={`side-panel${evidence ? ' evidence-layer' : ''}${closing ? ' is-closing' : ''}`}
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
