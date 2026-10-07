import type { ReactNode } from 'react'

import {
  formatMark,
  TEMPLATE_TITLES,
  type ChartModel,
  type Delta,
  type Mark,
  type TemplateId,
} from '../answerCard'
import { formatCell, SUPPRESSED, type ExploreClaim } from '../explore'
import { DataChart } from './DataChart'
import './DataPage.css'
import './AnswerCard.css'

/** Shows where a figure comes from (the evidence table cell); null where a
 * chart is drawn from sample data. */
type OpenCell = ((claim: ExploreClaim) => void) | null

interface RendererProps<T extends ChartModel> {
  model: T
  onOpen: OpenCell
}

/** The largest value to scale bars against (never zero). */
function scaleMax(marks: readonly (Mark | null)[]): number {
  const values = marks.map((mark) => mark?.value ?? 0)
  return Math.max(...values, 0) || 1
}

function share(value: number, max: number): string {
  return `${Math.max(0, Math.min(100, (100 * value) / max)).toFixed(2)}%`
}

function ValueButton({ mark, kind, onOpen }: { mark: Mark; kind: string; onOpen: OpenCell }) {
  const text = formatMark(mark, kind)
  if (onOpen === null || mark.claim === null) return <span className="ac-value">{text}</span>
  const claim = mark.claim
  return (
    <button
      type="button"
      className="finding-link ac-value"
      title="Show where this comes from"
      onClick={() => onOpen(claim)}
    >
      {text}
    </button>
  )
}

function DeltaLine({ delta, kind }: { delta: Delta; kind: string }) {
  const unit = kind === 'pct' ? ' points' : ''
  const amount = delta.change !== null ? `${formatCell(delta.change, kind === 'pct' ? 'points' : kind)}${unit}` : ''
  const pct = delta.changePct !== null ? ` (${formatCell(delta.changePct, 'pct')}%)` : ''
  const arrow = delta.direction === 'up' ? '▲' : delta.direction === 'down' ? '▼' : '■'
  return (
    <p className="ac-delta" data-direction={delta.direction}>
      <span aria-hidden="true">{arrow}</span>{' '}
      {delta.direction === 'unchanged' ? 'Unchanged' : `${delta.direction === 'up' ? 'Up' : 'Down'} ${amount}${pct}`}{' '}
      since {delta.since}
    </p>
  )
}

function RankingBar({ model, onOpen }: RendererProps<Extract<ChartModel, { template: 'ranking_bar' }>>) {
  const shown = model.bars.slice(0, 12)
  const max = scaleMax([...shown, model.reference])
  const refAt = model.reference?.value != null ? share(model.reference.value, max) : null
  return (
    <>
      <ol className="ac-bars">
        {shown.map((bar) => (
          <li key={bar.key} className="ac-bar-row" data-withheld={bar.withheld || undefined}>
            <span className="ac-bar-label">{bar.label}</span>
            <span className="ac-bar-track" aria-hidden="true">
              {bar.value !== null && <span className="ac-bar-fill" style={{ width: share(bar.value, max) }} />}
              {refAt !== null && <span className="ac-ref" style={{ left: refAt }} />}
            </span>
            <ValueButton mark={bar} kind={model.kind} onOpen={onOpen} />
          </li>
        ))}
      </ol>
      {model.bars.length > shown.length && (
        <p className="ac-more">
          The top {shown.length} of {model.bars.length} are drawn; every row is in the evidence table.
        </p>
      )}
      {model.reference !== null && model.reference.value !== null && (
        <p className="ac-legend">
          <span className="ac-ref-swatch" aria-hidden="true" /> All students:{' '}
          <ValueButton mark={model.reference} kind={model.kind} onOpen={onOpen} />
        </p>
      )}
    </>
  )
}

function TrendLine({ model }: RendererProps<Extract<ChartModel, { template: 'trend_line' }>>) {
  return (
    <>
      {model.delta !== null && <DeltaLine delta={model.delta} kind={model.data.kind} />}
      <DataChart data={model.data} onPick={null} />
    </>
  )
}

function GroupedBars({ model }: RendererProps<Extract<ChartModel, { template: 'grouped_bars' }>>) {
  // Horizontal, one block per group: long names wrap and a phone fits.
  const { data } = model
  const values = data.series.flatMap((s) => s.points.flatMap((p) => (p.value !== null ? [p.value] : [])))
  const max = Math.max(...values, 0) || 1
  const kind = data.kind === 'dollars' ? 'money' : data.kind
  return (
    <>
      <ul className="ac-keys" aria-label="Groups">
        {data.series.map((series) => (
          <li key={series.key}>
            <span
              className="swatch"
              aria-hidden="true"
              style={{ background: `var(--series-${((series.slot ?? 0) % 7) + 1})` }}
            />
            {series.label}
          </li>
        ))}
      </ul>
      <ol className="ac-groups">
        {data.x.slice(0, 12).map((x, xi) => (
          <li key={x.key} className="ac-group">
            <p className="ac-group-label">{x.label}</p>
            <ol className="ac-bars">
              {data.series.map((series) => {
                const point = series.points[xi]
                const withheld = point?.status === 'withheld'
                const value = point?.status === 'ok' ? point.value : null
                return (
                  <li key={series.key} className="ac-bar-row is-compact" data-withheld={withheld || undefined}>
                    <span className="ac-bar-label">{series.label}</span>
                    <span className="ac-bar-track" aria-hidden="true">
                      {value !== null && (
                        <span
                          className="ac-bar-fill"
                          style={{
                            width: share(value, max),
                            background: `var(--series-${((series.slot ?? 0) % 7) + 1})`,
                          }}
                        />
                      )}
                    </span>
                    <span className="ac-value">
                      {withheld
                        ? `withheld (${SUPPRESSED} students)`
                        : value === null
                          ? 'no figure'
                          : formatMark({ key: '', label: '', value, withheld: false, claim: null }, kind)}
                    </span>
                  </li>
                )
              })}
            </ol>
          </li>
        ))}
      </ol>
      {data.x.length > 12 && (
        <p className="ac-more">The first 12 of {data.x.length} groups are drawn; every row is in the evidence table.</p>
      )}
    </>
  )
}

/** One small trend: a line through the shown points, broken at a gap. */
function Spark({ points }: { points: readonly Mark[] }) {
  const values = points.flatMap((p) => (p.value !== null ? [p.value] : []))
  const lo = Math.min(...values, Infinity)
  const hi = Math.max(...values, -Infinity)
  const span = hi - lo || 1
  const n = Math.max(points.length - 1, 1)
  const runs: string[] = []
  let run = ''
  points.forEach((p, i) => {
    if (p.value === null) {
      if (run) runs.push(run)
      run = ''
      return
    }
    const x = (100 * i) / n
    const y = 36 - (32 * (p.value - lo)) / span
    run += `${run ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)} `
  })
  if (run) runs.push(run)
  return (
    <svg className="ac-spark" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true">
      {points.map((p, i) =>
        p.withheld ? <line key={i} className="chart-withheld" x1={(100 * i) / n} x2={(100 * i) / n} y1={2} y2={38} /> : null,
      )}
      {runs.map((d) => (
        <path key={d} d={d} className="ac-spark-line" vectorEffect="non-scaling-stroke" />
      ))}
    </svg>
  )
}

function SmallMultiples({ model }: RendererProps<Extract<ChartModel, { template: 'small_multiples' }>>) {
  return (
    <ul className="ac-multiples">
      {model.panels.map((panel) => {
        const last = [...panel.points].reverse().find((p) => p.value !== null) ?? null
        const first = panel.points.find((p) => p.value !== null) ?? null
        return (
          <li key={panel.label} className="ac-multiple">
            <p className="ac-multiple-label">{panel.label}</p>
            <Spark points={panel.points} />
            <p className="ac-multiple-range">
              {first !== null && last !== null
                ? `${first.label} ${formatMark(first, model.kind)} → ${last.label} ${formatMark(last, model.kind)}`
                : 'Every term is withheld'}
            </p>
          </li>
        )
      })}
    </ul>
  )
}

function KpiNumber({ model, onOpen }: RendererProps<Extract<ChartModel, { template: 'kpi_number' }>>) {
  return (
    <div className="ac-kpi">
      <p className="ac-kpi-value">
        <ValueButton mark={model.value} kind={model.kind} onOpen={onOpen} />
      </p>
      <p className="ac-kpi-label">
        {model.title}
        {model.scope !== null ? `, ${model.scope}` : ''}
      </p>
      {model.delta !== null && <DeltaLine delta={model.delta} kind={model.kind} />}
    </div>
  )
}

function ShareBar({ model, onOpen }: RendererProps<Extract<ChartModel, { template: 'share_bar' }>>) {
  const total = model.total || 1
  return (
    <>
      <div className="ac-stack" aria-hidden="true">
        {model.parts.map((part, index) =>
          part.value !== null ? (
            <span
              key={part.key}
              className="ac-stack-part"
              style={{ width: share(part.value, total), background: `var(--series-${(index % 7) + 1})` }}
            />
          ) : null,
        )}
      </div>
      <ul className="ac-keys ac-share-keys">
        {model.parts.map((part, index) => (
          <li key={part.key}>
            <span className="swatch" aria-hidden="true" style={{ background: `var(--series-${(index % 7) + 1})` }} />
            {part.label}: <ValueButton mark={part} kind={model.kind} onOpen={onOpen} />
            {part.value !== null && ` (${formatCell((100 * part.value) / total, 'pct')}%)`}
          </li>
        ))}
      </ul>
    </>
  )
}

function BeforeAfter({ model, onOpen }: RendererProps<Extract<ChartModel, { template: 'before_after' }>>) {
  const max = scaleMax([model.before, model.after])
  return (
    <>
      <ol className="ac-bars">
        {[model.before, model.after].map((bar, index) => (
          <li key={index} className="ac-bar-row" data-withheld={bar.withheld || undefined}>
            <span className="ac-bar-label">{bar.label}</span>
            <span className="ac-bar-track" aria-hidden="true">
              {bar.value !== null && (
                <span className={`ac-bar-fill${index === 0 ? ' is-before' : ''}`} style={{ width: share(bar.value, max) }} />
              )}
            </span>
            <ValueButton mark={bar} kind={model.kind} onOpen={onOpen} />
          </li>
        ))}
      </ol>
      {model.changePct !== null && (
        <p className="ac-delta" data-direction={model.changePct > 0 ? 'up' : model.changePct < 0 ? 'down' : 'unchanged'}>
          A change of {formatCell(model.changePct, 'pct')}% from {model.before.label} to {model.after.label}
        </p>
      )}
    </>
  )
}

function Funnel({ model, onOpen }: RendererProps<Extract<ChartModel, { template: 'funnel' }>>) {
  const max = scaleMax(model.stages)
  return (
    <ol className="ac-funnel">
      {model.stages.map((stage, index) => {
        const previous = index > 0 ? model.stages[index - 1] : null
        const of =
          previous?.value != null && stage.value !== null && previous.value > 0
            ? ` · ${formatCell((100 * stage.value) / previous.value, 'pct')}% of the stage before`
            : ''
        return (
          <li key={stage.key} className="ac-funnel-stage" data-withheld={stage.withheld || undefined}>
            <span className="ac-funnel-bar" aria-hidden="true" style={{ width: stage.value !== null ? share(stage.value, max) : '100%' }} />
            <span className="ac-funnel-text">
              {stage.label}: <ValueButton mark={stage} kind={model.kind} onOpen={onOpen} />
              {of}
            </span>
          </li>
        )
      })}
    </ol>
  )
}

/** The renderer for each template id: the fixed registry (CHART_TEMPLATES
 * in backend/src/cabinet/explore/card.py). */
const RENDERERS: { [K in TemplateId]: (props: RendererProps<Extract<ChartModel, { template: K }>>) => ReactNode } = {
  ranking_bar: RankingBar,
  trend_line: TrendLine,
  grouped_bars: GroupedBars,
  kpi_number: KpiNumber,
  share_bar: ShareBar,
  before_after: BeforeAfter,
  funnel: Funnel,
  small_multiples: SmallMultiples,
}


/** One answer's chart, drawn by its template's renderer from computed cells. */
export function AnswerChart({ model, onOpen = null }: { model: ChartModel; onOpen?: OpenCell }) {
  const Renderer = RENDERERS[model.template] as (props: RendererProps<ChartModel>) => ReactNode
  return (
    <figure className={`answer-chart ac-t-${model.template}`} data-template={model.template}>
      <figcaption className="ac-caption">
        <span className="ac-kicker">{TEMPLATE_TITLES[model.template]}</span> {model.title}
      </figcaption>
      <Renderer model={model} onOpen={onOpen} />
    </figure>
  )
}
