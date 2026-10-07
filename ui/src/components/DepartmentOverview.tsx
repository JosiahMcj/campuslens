import { useCallback, useEffect, useState } from 'react'

import { roleDisplayName, type Department } from '../auth'
import { friendlyLoadError } from '../errors'
import {
  fetchOverview,
  type AlertSource,
  type DepartmentOverview as Overview,
  type OverviewTable,
  type OverviewTile,
} from '../inbox'

import './Roles.css'

/**
 * One department's overview: four headline figures and two tables, all
 * aggregate (groups under the minimum size read "Fewer than 10"), and for
 * Finance two more sections (the university budget, and student accounts).
 * A department account sees its own; the president and the admin switch
 * between every department. Each figure can be sent to someone's inbox.
 */
export function DepartmentOverview({
  departments,
  onSendAlert,
}: {
  departments: Department[]
  onSendAlert: ((source: AlertSource) => void) | null
}) {
  const [department, setDepartment] = useState<Department>(departments[0])
  const [state, setState] = useState<
    { kind: 'loading' } | { kind: 'ready'; data: Overview } | { kind: 'error'; message: string }
  >({ kind: 'loading' })

  const load = useCallback(async (which: Department) => {
    setState({ kind: 'loading' })
    try {
      setState({ kind: 'ready', data: await fetchOverview(which) })
    } catch (failure) {
      setState({ kind: 'error', message: friendlyLoadError(failure) })
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the fetch's setState lands after an await
    void load(department)
  }, [department, load])

  return (
    <div className="overview">
      {departments.length > 1 && (
        <div className="inbox-tabs" role="tablist" aria-label="Department">
          {departments.map((which) => (
            <button
              key={which}
              type="button"
              role="tab"
              className="inbox-tab"
              aria-selected={which === department}
              onClick={() => setDepartment(which)}
            >
              {roleDisplayName(which)}
            </button>
          ))}
        </div>
      )}
      {state.kind === 'loading' && (
        <div role="status" aria-busy="true" className="panel-skeleton">
          <span className="visually-hidden">Loading the overview…</span>
          <div className="skeleton skeleton-line skeleton-heading" />
          <div className="skeleton skeleton-line" />
        </div>
      )}
      {state.kind === 'error' && (
        <div className="state-panel error-panel state-error" role="alert">
          <p>We couldn't load the overview. {state.message}</p>
          <button type="button" className="secondary btn-secondary" onClick={() => void load(department)}>
            Retry
          </button>
        </div>
      )}
      {state.kind === 'ready' && (
        <>
          <p className="hint overview-term">
            {state.data.term.name}
            {state.data.fictional ? ' · fictional data' : ''} · groups under{' '}
            {state.data.minimum_cell_size} students are withheld
          </p>
          <Tiles tiles={state.data.tiles} department={state.data.department} onSendAlert={onSendAlert} />
          {state.data.tables.map((table) => (
            <OverviewTableView key={table.key} table={table} />
          ))}
          {(state.data.sections ?? []).map((section) => (
            <section key={section.key} className="overview-section" aria-labelledby={`overview-${section.key}`}>
              <h2 id={`overview-${section.key}`}>{section.title}</h2>
              <p className="hint">{section.intro}</p>
              <Tiles tiles={section.tiles} department={state.data.department} onSendAlert={onSendAlert} />
              {section.tables.map((table) => (
                <OverviewTableView key={table.key} table={table} />
              ))}
            </section>
          ))}
        </>
      )}
    </div>
  )
}

function Tiles({
  tiles,
  department,
  onSendAlert,
}: {
  tiles: OverviewTile[]
  department: string
  onSendAlert: ((source: AlertSource) => void) | null
}) {
  return (
    <div className="overview-tiles">
      {tiles.map((tile) => (
        <div key={tile.key} className="overview-tile">
          <p className="overview-tile-label">{tile.label}</p>
          <p className="overview-tile-value">{tile.display}</p>
          {tile.note && <p className="overview-tile-note">{tile.note}</p>}
          {onSendAlert !== null && (
            <button
              type="button"
              className="link-button overview-alert"
              onClick={() =>
                onSendAlert({
                  kind: 'overview',
                  ref: `${department}:${tile.key}`,
                  label: `${tile.label}: ${tile.display}`,
                })
              }
            >
              Send alert
            </button>
          )}
        </div>
      ))}
    </div>
  )
}

function OverviewTableView({ table }: { table: OverviewTable }) {
  const last = table.rows.length - 1
  return (
    <section className="overview-table">
      <h3>{table.title}</h3>
      <div className="overview-table-scroll">
        <table>
          <thead>
            <tr>
              {table.columns.map((column) => (
                <th key={column.key} scope="col">
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, index) => (
              <tr key={index} className={table.total_last === true && index === last ? 'overview-total' : undefined}>
                {table.columns.map((column, at) =>
                  at === 0 ? (
                    <th key={column.key} scope="row">
                      {row[column.key]}
                    </th>
                  ) : (
                    <td key={column.key}>{row[column.key]}</td>
                  ),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(table.notes ?? []).map((note) => (
        <p key={note} className="hint overview-table-note">
          {note}
        </p>
      ))}
    </section>
  )
}
