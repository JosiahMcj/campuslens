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
    const evidence = screen.getByRole('region', { name: 'Not yet registered' })
    const panel = screen.getByRole('region', { name: 'Full briefing', hidden: true })
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

    fireEvent.keyDown(screen.getByRole('region', { name: 'Not yet registered' }), {
      key: 'Escape',
    })
    expect(screen.queryByRole('region', { name: 'Not yet registered' })).toBeNull()
    expect(onPanelClose).not.toHaveBeenCalled()
    const panel = screen.getByRole('region', { name: 'Full briefing' })
    expect(panel.parentElement!.hasAttribute('inert')).toBe(false)
    expect(document.activeElement).toBe(opener)

    // The second Escape closes the panel.
    fireEvent.keyDown(opener, { key: 'Escape' })
    expect(onPanelClose).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('region')).toBeNull()
  })

  it('closes only the top layer on an Escape that reaches the page', () => {
    const onPanelClose = vi.fn()
    render(<Stack onPanelClose={onPanelClose} />)
    openEvidence()
    ;(document.activeElement as HTMLElement).blur()
    expect(document.activeElement).toBe(document.body)

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('region', { name: 'Not yet registered' })).toBeNull()
    expect(onPanelClose).not.toHaveBeenCalled()
    expect(screen.getByRole('region', { name: 'Full briefing' })).toBeTruthy()
  })

  it('is a page with a Back button that returns to the page below', () => {
    render(<Stack />)
    const opener = openEvidence()
    const evidence = screen.getByRole('region', { name: 'Not yet registered' })
    expect(evidence.getAttribute('aria-modal')).toBeNull()
    expect(screen.getByRole('heading', { level: 1, name: 'Not yet registered' })).toBeTruthy()
    const backs = screen.getAllByRole('button', { name: 'Back' })
    fireEvent.click(backs[backs.length - 1])
    expect(screen.queryByRole('region', { name: 'Not yet registered' })).toBeNull()
    expect(document.activeElement).toBe(opener)
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
    const panel = screen.getByRole('region', { name: 'Audit log' })
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
