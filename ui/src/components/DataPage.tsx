import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  fetchDataCatalog,
  fetchSeries,
  formatValue,
  isNotApplicable,
  loadChoices,
  saveChoices,
  seriesQuery,
  sliceYears,
  validChoices,
  WITHHELD_TEXT,
  describePoint,
  type ChartData,
  type ChartSummary,
  type DataCatalog,
  type DataChoices,
  type Series,
  type SeriesResponse,
} from '../dataPage'
import { friendlyLoadError } from '../errors'
import { DataChart } from './DataChart'
import './DataPage.css'

type Load<T> = { kind: 'loading' } | { kind: 'ready'; data: T } | { kind: 'error'; message: string }

/** How many chart requests run at once (each is one governed query). */
const PARALLEL = 3

interface DataPageProps {
  /** The signed-in account: the person's choices are remembered per account. */
  account: string
}

/**
 * The Data page: the dashboards this role may open, each a grid of charts
 * over the years in the records. The filter bar narrows every chart to one
 * kind of student and can split each into groups; a series (or its legend
 * entry) narrows to that group. Choices are remembered per account on this
 * browser.
 */
export function DataPage({ account }: DataPageProps) {
  const [catalog, setCatalog] = useState<Load<DataCatalog>>({ kind: 'loading' })
  const [choices, setChoices] = useState<DataChoices>(() => loadChoices(account))
  const [results, setResults] = useState<Record<string, Load<SeriesResponse>>>({})
  const [loadRun, setLoadRun] = useState(0)
  const requested = useRef(new Set<string>())

  useEffect(() => {
    let live = true
    fetchDataCatalog()
      .then((data) => {
        if (!live) return
        setCatalog({ kind: 'ready', data })
        setChoices((current) => validChoices(current, data))
      })
      .catch((error: unknown) => {
        if (live) setCatalog({ kind: 'error', message: friendlyLoadError(error) })
      })
    return () => {
      live = false
    }
  }, [loadRun])

  const ready = catalog.kind === 'ready' ? catalog.data : null
  const dashboard = ready?.dashboards.find((d) => d.id === choices.dashboard) ?? ready?.dashboards[0]

  useEffect(() => {
    if (ready !== null) saveChoices(account, choices)
  }, [account, choices, ready])

  // A dashboard without student data (the university budget) takes no
  // student filter or comparison: its charts are asked for plainly.
  const students = dashboard?.students !== false
  const queries = useMemo(
    () =>
      (dashboard?.charts ?? []).map((chart) => ({
        chart,
        query: students ? seriesQuery(chart.id, choices.compare, choices.filters) : seriesQuery(chart.id, '', {}),
      })),
    [dashboard, students, choices.compare, choices.filters],
  )

  // Fetch the charts not yet loaded, a few at a time.
  useEffect(() => {
    const todo = queries.map((q) => q.query).filter((q) => !requested.current.has(q))
    if (todo.length === 0) return
    todo.forEach((q) => requested.current.add(q))
    setResults((current) => {
      const next = { ...current }
      todo.forEach((q) => {
        next[q] = { kind: 'loading' }
      })
      return next
    })
    let index = 0
    const worker = async () => {
      while (index < todo.length) {
        const query = todo[index]
        index += 1
        try {
          const data = await fetchSeries(query)
          setResults((current) => ({ ...current, [query]: { kind: 'ready', data } }))
        } catch (error) {
          requested.current.delete(query)
          setResults((current) => ({
            ...current,
            [query]: { kind: 'error', message: friendlyLoadError(error) },
          }))
        }
      }
    }
    for (let k = 0; k < Math.min(PARALLEL, todo.length); k += 1) void worker()
  }, [queries])

  const retryChart = useCallback((query: string) => {
    requested.current.delete(query)
    setResults((current) => {
      const next = { ...current }
      delete next[query]
      return next
    })
    // A new object identity re-runs the fetch effect for the missing query.
    setChoices((current) => ({ ...current, filters: { ...current.filters } }))
  }, [])

  const update = (patch: Partial<DataChoices>) => setChoices((current) => ({ ...current, ...patch }))
  // One group OR one comparison, never both: choosing either clears the other.
  const setGroup = (key: string, value: string) =>
    setChoices((current) => ({
      ...current,
      filters: key === '' || value === '' ? {} : { [key]: value },
      compare: key === '' || value === '' ? current.compare : '',
    }))
  const setCompare = (key: string) =>
    setChoices((current) => ({ ...current, compare: key, filters: key === '' ? current.filters : {} }))

  if (catalog.kind === 'loading') {
    return (
      <div className="data-page" aria-busy="true">
        <p className="skeleton-line" />
        <p className="skeleton-line short" />
      </div>
    )
  }
  if (catalog.kind === 'error') {
    return (
      <div className="state-error" role="alert">
        <p>Couldn't load the dashboards. {catalog.message}</p>
        <button type="button" className="btn-primary" onClick={() => setLoadRun((n) => n + 1)}>
          Retry
        </button>
      </div>
    )
  }
  const data = catalog.data
  if (dashboard === undefined) {
    return (
      <div className="state-empty">
        <p>There are no dashboards for your role.</p>
      </div>
    )
  }
  const group = Object.entries(choices.filters)[0] ?? null
  const labelOf = (key: string, value: string) => {
    const spec = data.filters.find((f) => f.key === key)
    return {
      key: spec?.label ?? key,
      value: spec?.options.find((o) => o.value === value)?.label ?? value,
    }
  }
  const lastYear = data.years[data.years.length - 1]
  const fromYear = choices.from === '' ? data.years[0] : choices.from
  const toYear = choices.to === '' ? lastYear : choices.to
  const yearLabel = (year: string) => year.replace('-', '–')

  return (
    <div className="data-page">
      <p className="data-lede">
        {data.fictional && <span className="data-tag">Fictional data</span>}
        <span>
          {students
            ? `Totals only, never a student's record. A point covering fewer than ${data.minimum_cell_size} students is withheld and shown as a gap.`
            : "The university's own books by fiscal year (July to June). No student records are in them."}
        </span>
      </p>

      {data.dashboards.length > 1 && (
        <div className="data-boards" role="group" aria-label="Dashboard">
          {data.dashboards.map((d) => (
            <button
              key={d.id}
              type="button"
              className="summary-tile"
              aria-pressed={d.id === dashboard.id}
              onClick={() => update({ dashboard: d.id })}
            >
              <span className="summary-label">{d.title}</span>
            </button>
          ))}
        </div>
      )}

      <section className="data-filters" aria-label="Choose what the charts show">
        <div className="data-filter-row">
          <label className="data-field">
            <span>From</span>
            <select
              className="field"
              value={fromYear}
              onChange={(e) => update({ from: e.target.value === data.years[0] ? '' : e.target.value })}
            >
              {data.years.map((year, i) => (
                <option key={year} value={year} disabled={i > data.years.indexOf(toYear)}>
                  {yearLabel(year)}
                </option>
              ))}
            </select>
          </label>
          <label className="data-field">
            <span>To</span>
            <select
              className="field"
              value={toYear}
              onChange={(e) => update({ to: e.target.value === lastYear ? '' : e.target.value })}
            >
              {data.years.map((year, i) => (
                <option key={year} value={year} disabled={i < data.years.indexOf(fromYear)}>
                  {yearLabel(year)}
                </option>
              ))}
            </select>
          </label>
          {students && (
            <>
              <label className="data-field data-field-wide">
                <span>Students</span>
                <select
                  className="field"
                  value={group === null ? '' : `${group[0]}=${group[1]}`}
                  onChange={(e) => {
                    const [key = '', value = ''] = e.target.value.split('=')
                    setGroup(key, value)
                  }}
                >
                  <option value="">All students</option>
                  {data.filters.map((spec) => (
                    <optgroup key={spec.key} label={spec.label}>
                      {spec.options.map((o) => (
                        <option key={o.value} value={`${spec.key}=${o.value}`}>
                          {o.label}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              </label>
              <label className="data-field">
                <span>Compare by</span>
                <select className="field" value={choices.compare} onChange={(e) => setCompare(e.target.value)}>
                  <option value="">No comparison</option>
                  {data.compare.map((c) => (
                    <option key={c.key} value={c.key}>
                      {c.label}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}
        </div>
        {students && (
          <p className="data-hint">
            Show one group of students, or compare groups. Not both at once: choosing one clears the
            other, so a group of fewer than {data.minimum_cell_size} students can never be worked out.
          </p>
        )}

        {students && group !== null && (
          <div className="data-chips">
            <span className="data-chips-label">Showing only</span>
            <button
              type="button"
              className="data-chip"
              aria-label={`Remove ${labelOf(group[0], group[1]).key}: ${labelOf(group[0], group[1]).value}`}
              onClick={() => setGroup('', '')}
            >
              {labelOf(group[0], group[1]).value}
              <span aria-hidden="true"> ×</span>
            </button>
            <button type="button" className="btn-secondary" onClick={() => setGroup('', '')}>
              Show all students
            </button>
          </div>
        )}
      </section>

      <p className="data-board-intro">{dashboard.intro}</p>
      <div className="data-grid">
        {queries.map(({ chart, query }) => (
          <ChartCard
            key={chart.id}
            summary={chart}
            state={results[query] ?? { kind: 'loading' }}
            years={data.years}
            from={choices.from}
            to={choices.to}
            onRetry={() => retryChart(query)}
            onPick={(split, series) => setGroup(split, series.key)}
          />
        ))}
      </div>
    </div>
  )
}

function ChartCard({
  summary,
  state,
  years,
  from,
  to,
  onRetry,
  onPick,
}: {
  summary: ChartSummary
  state: Load<SeriesResponse>
  years: string[]
  from: string
  to: string
  onRetry: () => void
  onPick: (split: string, series: Series) => void
}) {
  const headingId = `chart-${summary.id}`
  if (state.kind === 'loading') {
    return (
      <figure className="data-card" aria-labelledby={headingId} aria-busy="true">
        <h3 id={headingId}>{summary.title}</h3>
        <div className="data-card-loading">
          <p className="skeleton-line" />
          <p className="skeleton-line short" />
        </div>
      </figure>
    )
  }
  if (state.kind === 'error') {
    return (
      <figure className="data-card" aria-labelledby={headingId}>
        <h3 id={headingId}>{summary.title}</h3>
        <div className="state-error">
          <p>Couldn't load this chart. {state.message}</p>
          <button type="button" className="btn-primary" onClick={onRetry}>
            Retry
          </button>
        </div>
      </figure>
    )
  }
  const response = state.data
  if (isNotApplicable(response)) {
    return (
      <figure className="data-card" aria-labelledby={headingId}>
        <h3 id={headingId}>{summary.title}</h3>
        <p className="data-card-empty">
          Not shown for this selection: this chart can't be narrowed or split by{' '}
          {response.not_applicable.label.toLowerCase()}.
        </p>
      </figure>
    )
  }
  const data = sliceYears(response, years, from, to)
  return <ChartBody data={data} headingId={headingId} onPick={onPick} />
}

function ChartBody({
  data,
  headingId,
  onPick,
}: {
  data: ChartData
  headingId: string
  onPick: (split: string, series: Series) => void
}) {
  const split = data.split
  const shown = data.series.some((s) => s.points.some((p) => p.status === 'ok'))
  const anyWithheld = data.series.some((s) => s.points.some((p) => p.status === 'withheld'))
  const single = data.series.length === 1 ? data.series[0] : null
  // A single series leads with its latest shown value.
  const latest = (() => {
    if (single === null) return null
    for (let i = single.points.length - 1; i >= 0; i -= 1) {
      const p = single.points[i]
      if (p.status === 'ok' && p.value !== null) return { point: p, x: data.x[i] }
    }
    return null
  })()
  const pick = split !== null ? (series: Series) => onPick(split.key, series) : null
  const xNoun =
    data.x_label === 'Term'
      ? 'Fall and spring terms'
      : data.x_label === 'Fiscal year'
        ? 'Fiscal years (July to June)'
        : 'Entering classes (fall)'

  return (
    <figure className="data-card" aria-labelledby={headingId}>
      <div className="data-card-head">
        <div>
          <h3 id={headingId}>{data.title}</h3>
          <p className="data-card-sub">
            {data.value_label} · {xNoun}
          </p>
        </div>
        {latest !== null && (
          <p className="data-card-latest">
            <span className="data-card-figure">{formatValue(latest.point.value as number, data.kind)}</span>
            <span className="data-card-when">{latest.x.label}</span>
          </p>
        )}
      </div>

      {data.x.length === 0 ? (
        <p className="data-card-empty">No figure falls in the years chosen.</p>
      ) : !shown ? (
        <p className="data-card-empty">
          {anyWithheld || data.notes.some((n) => n.startsWith('Withheld'))
            ? 'Every figure here is withheld for this selection: it covers fewer than 10 students, or could reveal a group that small.'
            : 'No students match this selection.'}
        </p>
      ) : (
        <DataChart data={data} onPick={pick} />
      )}

      {data.series.length > 1 && shown && (
        <ul className="data-legend" aria-label="Groups in this chart">
          {data.series.map((s) => (
            <li key={s.key}>
              {pick !== null && s.slot !== null ? (
                <button
                  type="button"
                  className="data-legend-item"
                  onClick={() => pick(s)}
                  aria-label={`${s.label}: show only this group in every chart`}
                  title="Show only this group in every chart"
                >
                  <span className="swatch" style={{ background: `var(--series-${(s.slot % 7) + 1})` }} aria-hidden="true" />
                  {s.label}
                </button>
              ) : (
                <span className="data-legend-item is-static">
                  <span className="swatch is-reference" aria-hidden="true" />
                  {s.label}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {anyWithheld && shown && (
        <p className="data-card-note">
          <span className="data-gap-key" aria-hidden="true" />
          Dashed line: a withheld figure (fewer than 10 students, or it could reveal a group that small).
        </p>
      )}
      {data.notes.map((note) => (
        <p key={note} className="data-card-note">
          {note}
        </p>
      ))}

      {data.x.length > 0 && (
        <details className="fold data-figures">
          <summary>Show the figures</summary>
          <p className="data-card-note">{data.definition}</p>
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">{data.x_label}</th>
                  {data.series.map((s) => (
                    <th key={s.key} scope="col">
                      {s.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.x.map((x, i) => (
                  <tr key={x.key}>
                    <th scope="row">{x.label}</th>
                    {data.series.map((s) => {
                      const point = s.points[i]
                      return (
                        <td
                          key={s.key}
                          className={point?.status === 'ok' ? undefined : 'is-missing'}
                          title={point?.status === 'withheld' ? WITHHELD_TEXT : undefined}
                        >
                          {point?.status === 'withheld' ? 'Withheld' : describePoint(point, data)}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </figure>
  )
}

