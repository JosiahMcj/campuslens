import type { Findings } from '../api'
import { getFinding } from '../api'
import { findingLabel } from '../findingLabels'
import { findingDisplay } from '../states'
import { FindingLink } from './FindingLink'

/** The five headline figures. M9 (the counseling aggregate) is never a card. */
const STAT_IDS = ['M1', 'M2', 'M3', 'M4', 'M8'] as const

/**
 * The five headline measures, room-scale for the projector. Each figure IS
 * a FindingLink (it opens the evidence) and shows the finding's `display`
 * string verbatim (no arithmetic in the UI) with tabular figures, the
 * figure's display label beneath.
 */
export function StatRow({
  findings,
  onOpenEvidence,
}: {
  findings: Findings
  onOpenEvidence: (findingId: string) => void
}) {
  return (
    <div className="stat-row" aria-label="The five headline measures">
      {STAT_IDS.map((id) => {
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
              {display.text}
            </span>
            <span className="stat-title">{findingLabel(id, finding.title)}</span>
          </FindingLink>
        )
      })}
    </div>
  )
}
