import type { Findings } from '../api'
import { getFinding } from '../api'
import { findingDisplay } from '../states'
import { FindingLink } from './FindingLink'

const STAT_IDS = ['M1', 'M2', 'M3', 'M4'] as const

/**
 * The four headline measures, room-scale for the projector. Each figure IS
 * a FindingLink — it opens the evidence drawer — and shows the finding's
 * `display` string verbatim (no arithmetic in the UI) with tabular figures,
 * the finding's title beneath. They sit as a row of cards above the
 * document: four across from 1200 px, two across below.
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
