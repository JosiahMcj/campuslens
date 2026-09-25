import { useEffect, useState } from 'react'

const SECTIONS = [
  { id: 's-summary', label: '1. Executive summary' },
  { id: 's-measure', label: '2. Current measure and comparison' },
  { id: 's-groups', label: '3. Student groups most affected' },
  { id: 's-evidence', label: '4. Evidence and source fields' },
  { id: 's-actions', label: '5. Operational actions' },
  { id: 's-decision', label: '6. Leadership decisions' },
  { id: 's-limitations', label: '7. Known limitations' },
  { id: 'audit-log', label: 'Audit log' },
] as const

/**
 * The rail's section list: anchor links to the seven briefing sections and
 * the audit log. An IntersectionObserver (no library) marks the section in
 * view with aria-current; the observed band sits near the top of the
 * viewport, so the entry whose heading last crossed it stays marked.
 */
export function SectionNav() {
  const [activeId, setActiveId] = useState<string | null>(null)

  useEffect(() => {
    const targets = SECTIONS.map((section) =>
      document.getElementById(section.id),
    ).filter((element): element is HTMLElement => element !== null)
    if (targets.length === 0) return
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) setActiveId(entry.target.id)
        }
      },
      { rootMargin: '-20% 0px -70% 0px' },
    )
    for (const target of targets) observer.observe(target)
    return () => observer.disconnect()
  }, [])

  return (
    <nav className="section-nav" aria-label="Briefing sections">
      <ul>
        {SECTIONS.map((section) => (
          <li key={section.id}>
            <a
              href={`#${section.id}`}
              aria-current={activeId === section.id ? 'location' : undefined}
            >
              {section.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  )
}
