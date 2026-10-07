import type { Findings } from '../api'
import { getFinding } from '../api'
import { findingLabel } from '../findingLabels'
import { tidyNumbers } from '../displayFormat'
import { findingDisplay } from '../states'
import { FindingLink } from './FindingLink'

/** The five headline figures. M9 (the counseling aggregate) is never a card. */
const STAT_IDS = ['M1', 'M2', 'M3', 'M4', 'M8'] as const

/**
 * The five headline measures, room-scale for the projector. Each figure IS
 * a FindingLink (it opens the evidence) and shows the finding's `display`
 * string (no arithmetic in the UI; only the house number style, see
 * displayFormat.ts) with tabular figures, the figure's display label beneath.
 */
export function StatRow({
  findings,
  onOpenEvidence,
  ids = STAT_IDS,
  label = 'The five headline measures',
}: {
  findings: Findings
  onOpenEvidence: (findingId: string) => void
  /** Which figures to show; the five headline figures by default. */
  ids?: readonly string[]
  label?: string
}) {
  return (
    <div className="stat-row" aria-label={label}>
      {ids.map((id) => {
        const finding = getFinding(findings, id)
        if (!finding) return null
        const display = findingDisplay(finding)
        return (
          <FindingLink
            key={id}
            findingId={id}
            onOpen={onOpenEvidence}
            className="stat-figure"
          >
            <span className={display.missing ? 'stat-display missing' : 'stat-display'}>
              {tidyNumbers(display.text)}
            </span>
            <span className="stat-title">{findingLabel(id, finding.title)}</span>
          </FindingLink>
        )
      })}
    </div>
  )
}
