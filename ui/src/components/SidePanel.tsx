import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  type KeyboardEvent,
  type ReactNode,
} from 'react'

import { PANEL_MOTION_MS, prefersReducedMotion } from '../states'
import { BackIcon } from './icons'

interface SidePanelProps {
  title: string
  onClose: () => void
  /** True while the panel plays its exit animation (it no longer takes input). */
  closing?: boolean
  /** True while another layer (the evidence) sits on top: this one is inert. */
  covered?: boolean
  /** The evidence layer, which opens over the page or over another panel. */
  evidence?: boolean
  /** A page of cards or rows (a worklist, a log) that uses the wider
   * column; reading pages keep the narrower one. */
  wide?: boolean
  /** The one sentence under the title saying what the page is for. */
  intro?: string
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
 * A page of its own for everything that is not the conversation (the full
 * briefing, the AI employees, the audit log, a figure's evidence). It fills
 * the main column beside the sidebar, which stays usable, so another item
 * opens in its place. Back (or Escape) returns to where you were, and focus
 * goes back to what opened it. A layer on top (the evidence over a page)
 * makes the page under it inert; Escape closes only the top one.
 */
export function SidePanel({
  title,
  onClose,
  closing = false,
  covered = false,
  evidence = false,
  wide = false,
  intro,
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
    }
  }

  return (
    <div
      className={`panel-overlay${evidence ? ' evidence-overlay' : ''}${closing ? ' is-closing' : ''}`}
      data-evidence-drawer={evidence ? '' : undefined}
      inert={covered}
    >
      <div
        ref={panelRef}
        className={`side-panel${evidence ? ' evidence-layer' : ''}${wide ? ' is-wide' : ''}${closing ? ' is-closing' : ''}`}
        role="region"
        aria-label={title}
        tabIndex={-1}
        inert={closing}
        onKeyDown={onKeyDown}
      >
        <div className="side-panel-header">
          <button type="button" className="page-back" onClick={onClose}>
            <BackIcon />
            <span>Back</span>
          </button>
          <h1>{title}</h1>
        </div>
        <div ref={bodyRef} className="side-panel-body">
          {intro !== undefined && <p className="panel-intro page-intro">{intro}</p>}
          {children}
        </div>
      </div>
    </div>
  )
}
