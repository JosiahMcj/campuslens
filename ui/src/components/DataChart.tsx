import { useEffect, useId, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'

import {
  describePoint,
  formatTick,
  niceTicks,
  valueDomain,
  type ChartData,
  type Series,
} from '../dataPage'

const HEIGHT = 232
const MARGIN = { top: 12, right: 28, bottom: 30, left: 60 }

/** The CSS colour of a series: its fixed slot, or ink for the reference. */
function seriesColor(series: Series): string {
  return series.slot === null ? 'var(--ink)' : `var(--series-${(series.slot % 7) + 1})`
}

/** The width the chart has to draw in, followed as it changes. */
function useWidth(): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(640)
  useEffect(() => {
    const element = ref.current
    if (element === null) return
    const measure = () => {
      const next = Math.round(element.getBoundingClientRect().width)
      if (next > 0) setWidth(next)
    }
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  return [ref, width]
}

interface DataChartProps {
  data: ChartData
  /** Click a series (its line, bar or legend entry): narrow to that group. */
  onPick: ((series: Series) => void) | null
}

/**
 * A line or bar chart drawn in SVG from one chart's series. A withheld
 * point is never drawn as a value: the line breaks there and a dashed
 * guide marks the place, with the reason in the tooltip. Arrow keys move
 * through the terms; the tooltip names the measure, the group and the term.
 */
export function DataChart({ data, onPick }: DataChartProps) {
  const [wrapRef, width] = useWidth()
  const [active, setActive] = useState<number | null>(null)
  const [keyboard, setKeyboard] = useState(false)
  const tipId = useId()
  const n = data.x.length
  const plotW = Math.max(width - MARGIN.left - MARGIN.right, 40)
  const plotH = HEIGHT - MARGIN.top - MARGIN.bottom
  const values = data.series.flatMap((s) =>
    s.points.filter((p) => p.status === 'ok' && p.value !== null).map((p) => p.value as number),
  )
  const [lo, hi] = valueDomain(values, data.kind, data.form)
  const ticks = niceTicks(lo, hi, plotH < 160 ? 4 : 5).filter((t) => t >= lo - 1e-9)
  const top = Math.max(hi, ticks[ticks.length - 1] ?? hi)
  const bottom = Math.min(lo, ticks[0] ?? lo)
  const y = (v: number) => MARGIN.top + plotH - ((v - bottom) / (top - bottom || 1)) * plotH
  const band = plotW / Math.max(n, 1)
  const xCenter = (i: number) =>
    data.form === 'bar' || n === 1
      ? MARGIN.left + band * (i + 0.5)
      : MARGIN.left + (plotW * i) / (n - 1)
  // Bars: the series side by side within each band.
  // The reference (everyone) is a dashed level across each group, not a bar.
  const barSeries = data.series.filter((s) => s.slot !== null)
  const reference = data.series.find((s) => s.slot === null)
  const groupW = Math.min(band * 0.72, 28 * barSeries.length + 4 * (barSeries.length - 1))
  const barW = Math.max((groupW - 2 * (barSeries.length - 1)) / Math.max(barSeries.length, 1), 3)
  // Term labels thin out on a narrow chart: every term, every fall, or every other fall.
  const labelEvery = Math.max(1, Math.ceil(n / Math.max(Math.floor(plotW / 64), 1)))
  const withheldAt = new Set<number>()
  data.series.forEach((s) =>
    s.points.forEach((p, i) => {
      if (p.status === 'withheld') withheldAt.add(i)
    }),
  )

  const indexAt = (clientX: number, svg: SVGSVGElement): number => {
    const rect = svg.getBoundingClientRect()
    const scale = rect.width > 0 ? width / rect.width : 1
    const px = (clientX - rect.left) * scale
    let best = 0
    let bestD = Infinity
    for (let i = 0; i < n; i += 1) {
      const d = Math.abs(xCenter(i) - px)
      if (d < bestD) {
        best = i
        bestD = d
      }
    }
    return best
  }

  const onMove = (event: PointerEvent<SVGSVGElement>) => {
    if (n === 0) return
    setKeyboard(false)
    setActive(indexAt(event.clientX, event.currentTarget))
  }
  const onKey = (event: KeyboardEvent<SVGSVGElement>) => {
    if (n === 0) return
    const current = active ?? -1
    let next: number | null = null
    if (event.key === 'ArrowRight') next = Math.min(current + 1, n - 1)
    else if (event.key === 'ArrowLeft') next = Math.max(current - 1, 0)
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = n - 1
    if (next !== null) {
      event.preventDefault()
      setKeyboard(true)
      setActive(next)
    }
  }
  const pick = (series: Series) => {
    if (onPick !== null && series.slot !== null) onPick(series)
  }

  const activeX = active !== null ? data.x[active] : undefined
  const tipLeft = active !== null ? (xCenter(active) / width) * 100 : 0

  return (
    <div className="chart-wrap" ref={wrapRef}>
      <svg
        className={`chart chart-form-${data.form}`}
        width={width}
        height={HEIGHT}
        viewBox={`0 0 ${width} ${HEIGHT}`}
        role="img"
        aria-label={`${data.title}: ${data.value_label} by ${data.x_label.toLowerCase()}, ${data.x[0]?.label ?? ''} to ${data.x[n - 1]?.label ?? ''}. Use the arrow keys to read each value.`}
        aria-describedby={active !== null ? tipId : undefined}
        tabIndex={0}
        onPointerMove={onMove}
        onPointerLeave={() => setActive(null)}
        onFocus={() => {
          if (active === null && n > 0) {
            setKeyboard(true)
            setActive(n - 1)
          }
        }}
        onBlur={() => setActive(null)}
        onKeyDown={onKey}
      >
        {/* Value axis: recessive grid and short labels. */}
        {ticks.map((t) => (
          <g key={t}>
            <line className="chart-grid" x1={MARGIN.left} x2={MARGIN.left + plotW} y1={y(t)} y2={y(t)} />
            <text className="chart-tick" x={MARGIN.left - 8} y={y(t)} dy="0.32em" textAnchor="end">
              {formatTick(t, data.kind)}
            </text>
          </g>
        ))}
        {/* Term axis. */}
        {data.x.map((x, i) =>
          i % labelEvery === 0 || (data.form === 'bar' && n <= 8) ? (
            <text
              key={x.key}
              className="chart-tick"
              x={xCenter(i)}
              y={HEIGHT - MARGIN.bottom + 18}
              textAnchor="middle"
            >
              {width < 480 ? x.label.replace(/^(Fall|Spring) (\d{2})(\d{2})$/, (_, s: string, __, yy: string) =>
                    data.form === 'bar' && band < 64 ? `’${yy}` : `${s === 'Fall' ? 'Fall' : 'Spr'} ’${yy}`) : x.label}
            </text>
          ) : null,
        )}
        {/* Where a point is withheld: a dashed guide, never a mark. */}
        {[...withheldAt].map((i) => (
          <line
            key={`w${i}`}
            className="chart-withheld"
            x1={xCenter(i)}
            x2={xCenter(i)}
            y1={MARGIN.top}
            y2={MARGIN.top + plotH}
          />
        ))}
        {active !== null && (
          <line
            className="chart-cursor"
            x1={xCenter(active)}
            x2={xCenter(active)}
            y1={MARGIN.top}
            y2={MARGIN.top + plotH}
          />
        )}
        {data.form === 'line'
          ? data.series.map((s) => <LineSeries key={s.key} series={s} x={xCenter} y={y} onPick={pick} pickable={onPick !== null && s.slot !== null} active={active} />)
          : barSeries.map((s, si) =>
              s.points.map((p, i) => {
                if (p.status !== 'ok' || p.value === null) return null
                const x0 = xCenter(i) - groupW / 2 + si * (barW + 2)
                const y0 = y(p.value)
                const h = Math.max(y(bottom) - y0, 1)
                return (
                  <rect
                    key={`${s.key}-${p.x}`}
                    className={`chart-bar${onPick !== null && s.slot !== null ? ' is-pickable' : ''}${active === i ? ' is-active' : ''}`}
                    x={x0}
                    y={y0}
                    width={barW}
                    height={h}
                    rx={Math.min(4, barW / 2)}
                    fill={seriesColor(s)}
                    onClick={() => pick(s)}
                  />
                )
              }),
            )}
        {data.form === 'bar' &&
          reference?.points.map((p, i) =>
            p.status === 'ok' && p.value !== null ? (
              <line
                key={`ref-${p.x}`}
                className="chart-line is-reference"
                stroke="var(--ink)"
                x1={xCenter(i) - groupW / 2 - 4}
                x2={xCenter(i) + groupW / 2 + 4}
                y1={y(p.value)}
                y2={y(p.value)}
              />
            ) : null,
          )}
        <line className="chart-axis" x1={MARGIN.left} x2={MARGIN.left + plotW} y1={y(bottom)} y2={y(bottom)} />
      </svg>
      {activeX !== undefined && active !== null && (
        <div
          id={tipId}
          className={`chart-tip${width < 520 ? ' is-below' : tipLeft > 50 ? ' is-left' : ''}`}
          style={width < 520 ? undefined : { left: `${tipLeft}%` }}
          role="status"
          aria-live={keyboard ? 'polite' : 'off'}
        >
          <p className="chart-tip-head">
            {activeX.label} · {data.value_label}
          </p>
          <ul>
            {data.series.map((s) => {
              const point = s.points[active]
              return (
                <li key={s.key}>
                  <span className={`swatch${s.slot === null ? ' is-reference' : ''}`} style={{ background: seriesColor(s) }} aria-hidden="true" />
                  <span className="chart-tip-name">{s.label}</span>
                  <span className={`chart-tip-value${point?.status === 'ok' ? '' : ' is-missing'}`}>
                    {describePoint(point, data)}
                  </span>
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </div>
  )
}

function LineSeries({
  series,
  x,
  y,
  onPick,
  pickable,
  active,
}: {
  series: Series
  x: (i: number) => number
  y: (v: number) => number
  onPick: (series: Series) => void
  pickable: boolean
  active: number | null
}) {
  // Runs of consecutive shown points; a withheld or missing point breaks the line.
  const runs: { i: number; v: number }[][] = []
  let run: { i: number; v: number }[] = []
  series.points.forEach((p, i) => {
    if (p.status === 'ok' && p.value !== null) {
      run.push({ i, v: p.value })
    } else if (run.length > 0) {
      runs.push(run)
      run = []
    }
  })
  if (run.length > 0) runs.push(run)
  const color = seriesColor(series)
  const reference = series.slot === null
  return (
    <g className={`chart-series${pickable ? ' is-pickable' : ''}`} onClick={pickable ? () => onPick(series) : undefined}>
      {runs.map((r) => {
        const d = r.map((p, k) => `${k === 0 ? 'M' : 'L'}${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ')
        return (
          <g key={r[0].i}>
            {pickable && <path className="chart-hit" d={d} />}
            <path className={`chart-line${reference ? ' is-reference' : ''}`} d={d} stroke={color} />
          </g>
        )
      })}
      {series.points.map((p, i) =>
        p.status === 'ok' && p.value !== null ? (
          <circle
            key={p.x}
            className={`chart-dot${reference ? ' is-reference' : ''}`}
            cx={x(i)}
            cy={y(p.value)}
            r={active === i ? 5 : 3.5}
            fill={reference ? 'var(--surface)' : color}
            stroke={color}
          />
        ) : null,
      )}
    </g>
  )
}
