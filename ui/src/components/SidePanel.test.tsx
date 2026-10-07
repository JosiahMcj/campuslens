// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SidePanel } from './SidePanel'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

/** The app's stack in miniature: a panel, and the evidence opened over it. */
function Stack({ onPanelClose = () => {} }: { onPanelClose?: () => void }) {
  const [panel, setPanel] = useState(true)
  const [evidence, setEvidence] = useState(false)
  return (
    <>
      <main id="main-content" tabIndex={-1} inert={panel || evidence}>
        <button type="button">On the page</button>
      </main>
      {panel && (
        <SidePanel
          title="Full briefing"
          onClose={() => {
            onPanelClose()
            setPanel(false)
          }}
          covered={evidence}
        >
          <button type="button" onClick={() => setEvidence(true)}>
            18 students
          </button>
        </SidePanel>
      )}
      {evidence && (
        <SidePanel title="Not yet registered" onClose={() => setEvidence(false)} evidence>
          <button type="button">How it is computed</button>
        </SidePanel>
      )}
    </>
  )
}

function openEvidence() {
  const opener = screen.getByRole('button', { name: '18 students' })
  opener.focus()
  fireEvent.click(opener)
  return opener
}

describe('SidePanel layers', () => {
  it('marks the evidence layer and makes only the layer below inert', () => {
    render(<Stack />)
    openEvidence()
    const evidence = screen.getByRole('dialog', { name: 'Not yet registered' })
    const panel = screen.getByRole('dialog', { name: 'Full briefing', hidden: true })
    const evidenceOverlay = evidence.parentElement!
    const panelOverlay = panel.parentElement!

    expect(evidenceOverlay.hasAttribute('data-evidence-drawer')).toBe(true)
    expect(evidenceOverlay.hasAttribute('inert')).toBe(false)
    expect(evidenceOverlay.closest('[inert]')).toBeNull()
    expect(panelOverlay.hasAttribute('inert')).toBe(true)
    expect(document.activeElement).toBe(evidence)
  })

  it('closes only the top layer on Escape and returns focus to the opener below', () => {
    const onPanelClose = vi.fn()
    render(<Stack onPanelClose={onPanelClose} />)
    const opener = openEvidence()

    fireEvent.keyDown(screen.getByRole('dialog', { name: 'Not yet registered' }), {
      key: 'Escape',
    })
    expect(screen.queryByRole('dialog', { name: 'Not yet registered' })).toBeNull()
    expect(onPanelClose).not.toHaveBeenCalled()
    const panel = screen.getByRole('dialog', { name: 'Full briefing' })
    expect(panel.parentElement!.hasAttribute('inert')).toBe(false)
    expect(document.activeElement).toBe(opener)

    // The second Escape closes the panel.
    fireEvent.keyDown(opener, { key: 'Escape' })
    expect(onPanelClose).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('closes only the top layer on an Escape that reaches the page', () => {
    const onPanelClose = vi.fn()
    render(<Stack onPanelClose={onPanelClose} />)
    openEvidence()
    ;(document.activeElement as HTMLElement).blur()
    expect(document.activeElement).toBe(document.body)

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: 'Not yet registered' })).toBeNull()
    expect(onPanelClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: 'Full briefing' })).toBeTruthy()
  })

  it('keeps Tab inside the top layer', () => {
    // jsdom lays nothing out; give every element a box so it counts as visible.
    vi.spyOn(HTMLElement.prototype, 'getClientRects').mockReturnValue([
      new DOMRect(0, 0, 10, 10),
    ] as unknown as DOMRectList)
    render(<Stack />)
    openEvidence()
    const evidence = screen.getByRole('dialog', { name: 'Not yet registered' })
    const close = screen.getByRole('button', { name: 'Close Not yet registered' })
    const last = screen.getByRole('button', { name: 'How it is computed' })

    last.focus()
    fireEvent.keyDown(last, { key: 'Tab' })
    expect(document.activeElement).toBe(close)
    fireEvent.keyDown(close, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(last)
    expect(evidence.contains(document.activeElement)).toBe(true)
  })
})

/** A panel whose exit animation can be interrupted by reopening it. */
function Reopenable({ onClose }: { onClose: () => void }) {
  const [closing, setClosing] = useState(false)
  return (
    <>
      <main id="main-content" tabIndex={-1}>
        <button type="button" onClick={() => setClosing(false)}>
          Reopen
        </button>
        <button type="button" onClick={() => setClosing(true)}>
          Start closing
        </button>
      </main>
      <SidePanel title="Audit log" onClose={onClose} closing={closing}>
        <p>Entries</p>
      </SidePanel>
    </>
  )
}

describe('SidePanel reopened during its exit animation', () => {
  it('takes the top of the stack and focus back, and Escape reaches it again', () => {
    const onClose = vi.fn()
    render(<Reopenable onClose={onClose} />)
    const panel = screen.getByRole('dialog', { name: 'Audit log' })
    expect(document.activeElement).toBe(panel)

    fireEvent.click(screen.getByRole('button', { name: 'Start closing', hidden: true }))
    expect(document.activeElement).not.toBe(panel)
    // Escape on the page while it closes does not close it twice.
    ;(document.activeElement as HTMLElement).blur()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Reopen', hidden: true }))
    expect(document.activeElement).toBe(panel)
    fireEvent.keyDown(panel, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
