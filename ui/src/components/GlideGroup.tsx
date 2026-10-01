import { useRef, type MouseEvent, type ReactNode } from 'react'

/**
 * A group of rows with one shared hover highlight that glides from row to
 * row, as in the reference sidebar. Rows mark themselves with `data-row`.
 * The highlight is positioned through the element's style object (CSSOM),
 * never an inline style attribute, so the page's `style-src 'self'` CSP
 * holds. On first entry it appears in place; after that it slides.
 */
export function GlideGroup({
  children,
  className = '',
}: {
  children: ReactNode
  className?: string
}) {
  const groupRef = useRef<HTMLDivElement>(null)
  const highlightRef = useRef<HTMLSpanElement>(null)

  const place = (event: MouseEvent) => {
    const group = groupRef.current
    const highlight = highlightRef.current
    const row = (event.target as Element).closest<HTMLElement>('[data-row]')
    if (group === null || highlight === null || row === null || !group.contains(row)) {
      return
    }
    const entering = group.dataset.glide !== 'on'
    if (entering) highlight.classList.add('glide-instant')
    highlight.style.transform = `translate(${row.offsetLeft}px, ${row.offsetTop}px)`
    highlight.style.width = `${row.offsetWidth}px`
    highlight.style.height = `${row.offsetHeight}px`
    if (entering) {
      // Commit the jump before re-enabling the slide transition.
      void highlight.offsetWidth
      highlight.classList.remove('glide-instant')
      group.dataset.glide = 'on'
    }
  }

  const leave = () => {
    if (groupRef.current !== null) groupRef.current.dataset.glide = 'off'
  }

  return (
    <div
      ref={groupRef}
      className={`glide-group ${className}`}
      data-glide="off"
      onMouseOver={place}
      onMouseLeave={leave}
    >
      <span ref={highlightRef} className="glide-highlight" aria-hidden="true" />
      {children}
    </div>
  )
}
