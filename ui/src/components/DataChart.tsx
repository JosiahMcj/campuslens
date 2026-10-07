import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
} from 'react'

import {
  describePoint,
  type ChartFocus,
  formatTick,
  niceTicks,
  valueDomain,
  type ChartData,
  type Series,
} from '../dataPage'
import { useWidth } from '../useWidth'

const FULL_HEIGHT = 232
const COMPACT_HEIGHT = 176
const MARGIN = { top: 12, right: 28, bottom: 30, left: 60 }

/** The CSS colour of a series: its fixed slot, or ink for the reference. */
function seriesColor(series: Series): string {
  return series.slot === null ? 'var(--ink)' : `var(--series-${(series.slot % 7) + 1})`
}

/** A point the person opened: a term, and a group or all of them. */
interface Pinned {
  index: number
  seriesKey: string | null
}

interface DataChartProps {
  data: ChartData
  /** "Show only this group" in a point's popover: narrow to that group. */
  onPick: ((series: Series) => void) | null
  /** "Ask about this" in a point's popover (null: not offered). */
  onAsk?: ((focus: ChartFocus) => void) | null
  /** "Send to department" in a point's popover (null: not offered). */
  onSend?: ((focus: ChartFocus) => void) | null
  /** A term to mark with a cursor that stays (the point an alert names). */
  highlight?: number | null
  /** A shorter chart (an alert's attachment). */
  compact?: boolean
}

/**
 * A line or bar chart drawn in SVG from one chart's series. A withheld
 * point is never drawn as a value: the line breaks there and a dashed
 * guide marks the place, with the reason in the tooltip. Arrow keys move
 * through the terms; the tooltip names the measure, the group and the term.
 */
export function DataChart({ data, onPick, onAsk = null, onSend = null, highlight = null, compact = false }: DataChartProps) {
  const [wrapRef, width] = useWidth()
  const [active, setActive] = useState<number | null>(null)
  const [keyboard, setKeyboard] = useState(false)
  const [pinned, setPinned] = useState<Pinned | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const tipId = useId()
  const popId = useId()
  const HEIGHT = compact ? COMPACT_HEIGHT : FULL_HEIGHT
  const n = data.x.length
  // A point opens a popover when there is something to do with it.
  const popover = onAsk !== null || onSend !== null || onPick !== null
  const single = data.series.length === 1 ? data.series[0] : null
  const plotW = Math.max(width - MARGIN.left - MARGIN.right, 40)
  const plotH = HEIGHT - MARGIN.top - MARGIN.bottom
  const values = data.series.flatMap((s) =>
    s.points.filter((p) => p.status === 'ok' && p.value !== null).map((p) => p.value as number),
  )
  const [lo, hi] = valueDomain(values, data.kind, data.form)
  const ticks = niceTicks(lo, hi, plotH < 160 ? 4 : 5).filter((t) => t >= lo - 1e-9)
  const top = Math.max(hi, ticks[ticks.length - 1] ?? hi)
  const bottom = Math.min(lo, ticks[0] ?? lo)
  // A value axis that does not start at zero (an average such as GPA) is
  // marked with a break, so a small change is never read as a large one.
  const broken = bottom > 0
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
    else if ((event.key === 'Enter' || event.key === ' ') && popover) {
      event.preventDefault()
      openPoint(active ?? n - 1, single?.key ?? null)
      return
    }
    if (next !== null) {
      event.preventDefault()
      setKeyboard(true)
      setActive(next)
    }
  }
  const openPoint = (index: number, seriesKey: string | null) => {
    if (!popover || index < 0 || index >= n) return
    setPinned({ index, seriesKey })
    setActive(index)
  }
  const closePoint = (refocus: boolean) => {
    const index = pinned?.index ?? null
    setPinned(null)
    if (refocus) {
      svgRef.current?.focus()
      setActive(index)
    }
  }
  const onChartClick = (event: MouseEvent<SVGSVGElement>) => {
    if (!popover || n === 0) return
    const target = event.target as Element
    const at = target.getAttribute('data-index')
    const seriesKey = target.getAttribute('data-series')
    const index = at !== null ? Number(at) : indexAt(event.clientX, event.currentTarget)
    openPoint(index, seriesKey ?? single?.key ?? null)
  }

  // The popover takes focus when it opens, and closes on a click elsewhere.
  useEffect(() => {
    if (pinned === null) return
    popRef.current?.querySelector<HTMLElement>('button')?.focus()
    const away = (event: Event) => {
      const target = event.target as Node
      if (popRef.current?.contains(target) || svgRef.current?.contains(target)) return
      setPinned(null)
    }
    document.addEventListener('pointerdown', away)
    return () => document.removeEventListener('pointerdown', away)
  }, [pinned])

  const activeX = active !== null && pinned === null ? data.x[active] : undefined
  const tipLeft = active !== null ? (xCenter(active) / width) * 100 : 0
  const cursorAt = pinned?.index ?? active ?? highlight
  const narrow = width < 520

  return (
    <div className="chart-wrap" ref={wrapRef}>
      <svg
        ref={svgRef}
        className={`chart chart-form-${data.form}${popover ? ' is-pickable' : ''}`}
        width={width}
        height={HEIGHT}
        viewBox={`0 0 ${width} ${HEIGHT}`}
        role="img"
        aria-label={`${data.title}: ${data.value_label} by ${data.x_label.toLowerCase()}, ${data.x[0]?.label ?? ''} to ${data.x[n - 1]?.label ?? ''}. ${broken ? ` The value axis starts at ${formatTick(bottom, data.kind)}, not zero.` : ''} Use the arrow keys to read each value${popover ? ', and Enter to open one' : ''}.`}
        aria-describedby={pinned !== null ? undefined : active !== null ? tipId : undefined}
        aria-haspopup={popover ? 'dialog' : undefined}
        aria-expanded={popover ? pinned !== null : undefined}
        aria-controls={pinned !== null ? popId : undefined}
        tabIndex={0}
        onPointerMove={onMove}
        onPointerLeave={() => setActive(null)}
        onFocus={() => {
          if (active === null && n > 0) {
            setKeyboard(true)
            setActive(n - 1)
          }
        }}
        onBlur={() => {
          if (pinned === null) setActive(null)
        }}
        onKeyDown={onKey}
        onClick={onChartClick}
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
        {cursorAt !== null && cursorAt < n && (
          <line
            className={`chart-cursor${pinned !== null || (active === null && highlight !== null) ? ' is-pinned' : ''}`}
            x1={xCenter(cursorAt)}
            x2={xCenter(cursorAt)}
            y1={MARGIN.top}
            y2={MARGIN.top + plotH}
          />
        )}
        {data.form === 'line'
          ? data.series.map((s) => (
              <LineSeries key={s.key} series={s} x={xCenter} y={y} pickable={popover} active={cursorAt} />
            ))
          : barSeries.map((s, si) =>
              s.points.map((p, i) => {
                if (p.status !== 'ok' || p.value === null) return null
                const x0 = xCenter(i) - groupW / 2 + si * (barW + 2)
                const y0 = y(p.value)
                const h = Math.max(y(bottom) - y0, 1)
                return (
                  <rect
                    key={`${s.key}-${p.x}`}
                    className={`chart-bar${popover ? ' is-pickable' : ''}${cursorAt === i ? ' is-active' : ''}`}
                    data-index={i}
                    data-series={s.key}
                    x={x0}
                    y={y0}
                    width={barW}
                    height={h}
                    rx={Math.min(4, barW / 2)}
                    fill={seriesColor(s)}
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
        {broken && (
          <g className="chart-break" aria-hidden="true">
            <title>The value axis does not start at zero</title>
            <rect x={MARGIN.left - 6} y={y(bottom) - 9} width={12} height={8} />
            <path
              d={`M${MARGIN.left - 6},${y(bottom) - 3} l4,-4 l4,4 l4,-4 M${MARGIN.left - 6},${y(bottom) - 7} l4,-4 l4,4 l4,-4`}
            />
          </g>
        )}
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
      {pinned !== null && (
        <PointPopover
          id={popId}
          ref={popRef}
          data={data}
          pinned={pinned}
          left={narrow ? null : (xCenter(pinned.index) / width) * 100}
          onScope={(seriesKey) => setPinned({ ...pinned, seriesKey })}
          onClose={() => closePoint(true)}
          onAsk={onAsk === null ? null : () => {
            setPinned(null)
            onAsk(pinned)
          }}
          onSend={onSend === null ? null : () => {
            setPinned(null)
            onSend(pinned)
          }}
          onPick={onPick === null ? null : (series) => {
            setPinned(null)
            onPick(series)
          }}
        />
      )}
    </div>
  )
}

/**
 * One point, opened by a click or Enter: its exact value (or why it is
 * withheld) for the term and group, and what to do with it. Opened on a
 * term with several groups and none chosen, it lists each group's value;
 * choosing one scopes the actions to it. A withheld point's actions work
 * but never carry a value.
 */
function PointPopover({
  id,
  ref,
  data,
  pinned,
  left,
  onScope,
  onClose,
  onAsk,
  onSend,
  onPick,
}: {
  id: string
  ref: React.RefObject<HTMLDivElement | null>
  data: ChartData
  pinned: Pinned
  left: number | null
  onScope: (seriesKey: string | null) => void
  onClose: () => void
  onAsk: (() => void) | null
  onSend: (() => void) | null
  onPick: ((series: Series) => void) | null
}) {
  const x = data.x[pinned.index]
  const scoped = pinned.seriesKey !== null ? data.series.find((s) => s.key === pinned.seriesKey) : undefined
  const rows = scoped !== undefined ? [scoped] : data.series
  const several = scoped === undefined && data.series.length > 1
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      onClose()
    }
  }
  const placement = left === null ? ' is-below' : left > 50 ? ' is-left' : ''
  return (
    <div
      id={id}
      ref={ref}
      className={`chart-pop${placement}`}
      style={left === null ? undefined : { left: `${left}%` }}
      role="dialog"
      aria-label={`${x?.label ?? ''}: ${data.title}`}
      onKeyDown={onKeyDown}
    >
      <div className="chart-pop-head">
        <p className="chart-tip-head">
          {x?.label} · {data.value_label}
        </p>
        <button type="button" className="chart-pop-close" aria-label="Close" onClick={onClose}>
          <span aria-hidden="true">×</span>
        </button>
      </div>
      <ul className="chart-pop-rows">
        {rows.map((s) => {
          const point = s.points[pinned.index]
          const body = (
            <>
              <span className={`swatch${s.slot === null ? ' is-reference' : ''}`} style={{ background: seriesColor(s) }} aria-hidden="true" />
              <span className="chart-tip-name">{s.label}</span>
              <span className={`chart-tip-value${point?.status === 'ok' ? '' : ' is-missing'}`}>
                {describePoint(point, data)}
              </span>
            </>
          )
          return (
            <li key={s.key}>
              {several ? (
                <button
                  type="button"
                  className="chart-pop-row"
                  onClick={() => onScope(s.key)}
                  aria-label={`${s.label}: ${describePoint(point, data)}. Choose this group`}
                >
                  {body}
                </button>
              ) : (
                <span className="chart-pop-row is-static">{body}</span>
              )}
            </li>
          )
        })}
      </ul>
      {several && <p className="chart-pop-hint">Choose a group to ask or send about it, or use the whole term.</p>}
      {scoped !== undefined && data.series.length > 1 && (
        <button type="button" className="link-button chart-pop-back" onClick={() => onScope(null)}>
          Every group in {x?.label}
        </button>
      )}
      <div className="chart-pop-actions">
        {onAsk !== null && (
          <button type="button" className="btn-primary" onClick={onAsk}>
            Ask about this
          </button>
        )}
        {onSend !== null && (
          <button type="button" className="btn-secondary" onClick={onSend}>
            Send to department
          </button>
        )}
        {onPick !== null && scoped !== undefined && scoped.slot !== null && data.split !== null && (
          <button type="button" className="btn-secondary" onClick={() => onPick(scoped)}>
            Show only {scoped.label}
          </button>
        )}
      </div>
    </div>
  )
}

function LineSeries({
  series,
  x,
  y,
  pickable,
  active,
}: {
  series: Series
  x: (i: number) => number
  y: (v: number) => number
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
    <g className={`chart-series${pickable ? ' is-pickable' : ''}`}>
      {runs.map((r) => {
        const d = r.map((p, k) => `${k === 0 ? 'M' : 'L'}${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ')
        return (
          <g key={r[0].i}>
            {pickable && <path className="chart-hit" d={d} data-series={series.key} />}
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
      {pickable &&
        series.points.map((p, i) =>
          p.status === 'ok' && p.value !== null ? (
            <circle
              key={`hit-${p.x}`}
              className="chart-dot-hit"
              cx={x(i)}
              cy={y(p.value)}
              r={11}
              data-index={i}
              data-series={series.key}
            />
          ) : null,
        )}
    </g>
  )
}
