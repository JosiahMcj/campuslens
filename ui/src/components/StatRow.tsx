import type { Findings } from '../api'
import { getFinding } from '../api'
import { findingDisplay } from '../states'
import { FindingLink } from './FindingLink'

const STAT_IDS = ['M1', 'M2', 'M3', 'M4'] as const

/**
 * The four headline measures, room-scale for the projector: typographic
 * figures on a ruled row, no boxes. Each figure IS a FindingLink — it opens
 * the evidence drawer — and shows the finding's `display` string verbatim
 * (no arithmetic in the UI) at 2.4rem with tabular figures, the finding's
 * title beneath at 0.89rem. The rail stacks them vertically from 1200 px;
 * below that they form a two-column ruled row above the document.
 */
export function StatRow({
  findings,
  onOpenEvidence,
}: {
  findings: Findings
  onOpenEvidence: (findingId: string) => void
}) {
  return (
    <div className="stat-row" aria-label="The four headline measures">
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
            <span className="stat-title">{finding.title}</span>
          </FindingLink>
        )
      })}
    </div>
  )
}
