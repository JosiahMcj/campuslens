import { chartAbout, chartQuestion, describePoint, type ChartFocus } from '../dataPage'
import type { ChartSnapshot } from '../inbox'
import { DataChart } from './DataChart'
import { Legend } from './DataPage'

import './DataPage.css'

/**
 * A Data page chart someone sent, as the server computed it for this
 * reader's role just now: the chart redrawn, the point the sender chose
 * (its value, or why it is withheld), and Ask about this.
 */
export function ChartAttachment({
  chart,
  onAsk,
}: {
  chart: ChartSnapshot
  /** Start a new chat about the chart; null for roles that cannot ask. */
  onAsk: ((question: string, about: string) => void) | null
}) {
  const at = chart.at !== null ? chart.x.findIndex((x) => x.key === chart.at) : -1
  const focus: ChartFocus = { index: at >= 0 ? at : null, seriesKey: chart.focus_series }
  const group = chart.group !== null ? { label: chart.group.label, value: chart.group.value } : null
  const rows =
    focus.index === null
      ? []
      : chart.focus_series !== null
        ? chart.series.filter((s) => s.key === chart.focus_series)
        : chart.series
  const shown = chart.series.some((s) => s.points.some((p) => p.status === 'ok'))
  return (
    <div className="alert-card alert-chart">
      <p className="alert-card-kicker">
        Data page chart{group !== null ? ` · ${group.label}: ${group.value}` : ''}
        {chart.split !== null && chart.series.length > 1 ? ` · by ${chart.split.label.toLowerCase()}` : ''}
      </p>
      <p className="alert-card-title">{chart.title}</p>
      {focus.index !== null && (
        <ul className="alert-chart-points" aria-label={`${chart.x[focus.index].label}: ${chart.value_label}`}>
          {rows.map((s) => {
            const point = s.points[focus.index as number]
            return (
              <li key={s.key}>
                <span className="alert-chart-when">
                  {chart.x[focus.index as number].label}
                  {chart.series.length > 1 ? ` · ${s.label}` : ''}
                </span>
                <span className={`alert-card-value${point?.status === 'ok' ? '' : ' is-missing'}`}>
                  {describePoint(point, chart)}
                </span>
              </li>
            )
          })}
        </ul>
      )}
      {shown ? (
        <>
          <DataChart data={chart} onPick={null} highlight={focus.index} compact />
          {chart.series.length > 1 && <Legend series={chart.series} onPick={null} />}
        </>
      ) : (
        <p className="hint">Every figure in this chart is withheld for your role's view of it.</p>
      )}
      <p className="hint">{chart.definition}</p>
      {onAsk !== null && (
        <button
          type="button"
          className="btn-secondary alert-chart-ask"
          onClick={() => onAsk(chartQuestion(chart, group, focus), chartAbout(chart, group, focus))}
        >
          Ask about this
        </button>
      )}
    </div>
  )
}
