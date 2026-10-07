import { useCallback, useEffect, useState } from 'react'

import { fetchConnections, type Connections } from '../connections'
import { friendlyLoadError } from '../errors'
import { formatTimestamp, type LoadState } from '../states'
import { GrantedIcon, RefusedIcon } from './icons'
import './Institution.css'

/** The section anchor, for links into Institution settings. */
export const CONNECTIONS_SECTION_ID = 'inst-connections'

/**
 * An Institution section's loading state: the shared skeleton lines, with
 * the words for a screen reader.
 */
export function SectionLoading({ label }: { label: string }) {
  return (
    <div className="inst-loading" role="status" aria-busy="true">
      <span className="visually-hidden">{label}</span>
      <div className="skeleton-line" aria-hidden="true" />
      <div className="skeleton-line" aria-hidden="true" />
      <div className="skeleton-line short" aria-hidden="true" />
    </div>
  )
}

/** A connection's state as a pill, with the shared granted/refused mark. */
function StatePill({ on, children }: { on: boolean; children: string }) {
  return (
    <p className={on ? 'connection-state on' : 'connection-state'}>
      {on ? <GrantedIcon /> : <RefusedIcon />}
      <span>{children}</span>
    </p>
  )
}

/**
 * Institution settings: the outside connections, read only. Whether the
 * student-records import from Ellucian is set up on this server (each
 * setting set or not, never its value, folded under Details) and its last
 * import, and where messages to office mailboxes go. Changing either is a
 * server setting (RUNBOOK.md), so the section says who to ask instead of
 * offering a button.
 */
export function ConnectionsSection() {
  const [state, setState] = useState<LoadState<Connections>>({ kind: 'loading' })

  const load = useCallback(async () => {
    setState({ kind: 'loading' })
    try {
      setState({ kind: 'ready', data: await fetchConnections() })
    } catch (error) {
      setState({ kind: 'error', message: friendlyLoadError(error) })
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  return (
    <section
      id={CONNECTIONS_SECTION_ID}
      className="inst-card"
      aria-labelledby="inst-connections-heading"
    >
      <h2 id="inst-connections-heading" tabIndex={-1}>
        Connections
      </h2>
      <p className="hint">
        Where student records come from, and where messages to offices go. These are
        set on the server by whoever runs CampusLens; no password or key is ever shown
        here.
      </p>
      {state.kind === 'loading' && <SectionLoading label="Loading the connections…" />}
      {state.kind === 'error' && (
        <div className="state-error" role="alert">
          <h3>We couldn’t load the connections</h3>
          <p>{state.message}</p>
          <button type="button" className="btn-secondary" onClick={() => void load()}>
            Retry
          </button>
        </div>
      )}
      {state.kind === 'ready' && (
        <div className="connection-grid">
          <div className="connection">
            <h3>Student records from Ellucian</h3>
            <StatePill on={state.data.ellucian.configured}>
              {state.data.ellucian.configured ? 'Set up' : 'Not set up'}
            </StatePill>
            {state.data.ellucian.settings.length > 0 && (
              <details className="fold technical-detail">
                <summary>Settings on the server</summary>
                <ul className="connection-settings">
                  {state.data.ellucian.settings.map((setting) => (
                    <li key={setting.label}>
                      {setting.set ? <GrantedIcon /> : <RefusedIcon />}
                      <span>
                        {setting.label}: {setting.set ? 'set' : 'not set'}
                      </span>
                    </li>
                  ))}
                </ul>
              </details>
            )}
            <p className="hint">
              {state.data.ellucian.last_import === null
                ? 'No import has arrived from Ellucian yet. The briefing uses the data uploaded under Data.'
                : `Last import: ${state.data.ellucian.last_import.name}, ${formatTimestamp(state.data.ellucian.last_import.at)}. ${
                    state.data.ellucian.last_import.in_use
                      ? 'It is the data in use.'
                      : 'It is not the data in use; activate it under Data.'
                  }`}
            </p>
          </div>
          <div className="connection">
            <h3>Messages to offices</h3>
            <StatePill on>
              {state.data.outbound.provider === 'smtp' ? 'Sent by email' : 'Kept on this server'}
            </StatePill>
            <p className="hint">
              {state.data.outbound.provider === 'smtp'
                ? 'A message a staff member sends goes to the office mailbox through your mail server.'
                : 'A message a staff member sends is saved in the outbox on this server, and nothing leaves it. Whoever runs CampusLens can switch on delivery by email.'}
            </p>
          </div>
        </div>
      )}
    </section>
  )
}
