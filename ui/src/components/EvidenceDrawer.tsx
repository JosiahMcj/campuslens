import { useEffect, useRef } from 'react'

import type { Finding, FindingAuthorization, OfficeHolds, RatioRowIds } from '../api'
import { isAggregateOnly, suppressionNote } from '../counseling'
import { findingDisplay, formatTimestamp } from '../states'

interface EvidenceDrawerProps {
  finding: Finding
  /** True while the active dataset is the fictional demonstration set. */
  fictional: boolean
  onClose: () => void
}

function isRatioRowIds(rowIds: Finding['row_ids']): rowIds is RatioRowIds {
  return !Array.isArray(rowIds)
}

/** M8 carries a per-student map of the support indicator rules that fired. */
function hasRowRules(finding: Finding): boolean {
  return (
    finding.row_rules !== undefined &&
    Object.keys(finding.row_rules).length > 0 &&
    Array.isArray(finding.row_ids)
  )
}

/**
 * The evidence drawer (Beat 4): a claim opened to its source fields, its
 * formula/definition, and the row IDs behind the number. An aggregate-only
 * figure (M9, the authorized counseling count) shows its authorization
 * record instead and never a row: there is no drill-down behind it. Keyboard-closable
 * (Esc) and focus-managed: focus moves into the drawer on open and returns
 * to the element that opened it on close.
 */
export function EvidenceDrawer({ finding, fictional, onClose }: EvidenceDrawerProps) {
  const drawerRef = useRef<HTMLDivElement>(null)
  const openerRef = useRef<Element | null>(null)

  useEffect(() => {
    openerRef.current = document.activeElement
    drawerRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      const opener = openerRef.current
      if (opener instanceof HTMLElement) opener.focus()
    }
  }, [onClose])

  const display = findingDisplay(finding)
  // M9: an authorized aggregate with no rows behind it, ever.
  const aggregateOnly = isAggregateOnly(finding)
  const withheld = suppressionNote(finding)

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <div
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="drawer-title"
        className="drawer"
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="drawer-header">
          <h2 id="drawer-title">
            <span className="id-badge">{finding.id}</span>
            {finding.title}
          </h2>
          <button type="button" className="close-button" onClick={onClose}>
            Close <span aria-hidden="true">(Esc)</span>
          </button>
        </div>

        <dl className="drawer-facts">
          <dt>Value</dt>
          <dd>
            <span className={display.missing ? 'drawer-value missing' : 'drawer-value'}>
              {withheld !== null ? `${capitalize(display.text)} students` : display.text}
            </span>
            {withheld !== null && <p className="hint">{withheld}</p>}
            {display.missing && display.reason !== null && (
              <p className="hint">Not available: {display.reason}</p>
            )}
          </dd>

          {finding.authorization !== undefined && (
            <>
              <dt>Authorization</dt>
              <dd>
                <AuthorizationRecord authorization={finding.authorization} />
              </dd>
            </>
          )}

          <dt>Formula / definition</dt>
          <dd>
            <code className="formula">{finding.definition}</code>
          </dd>

          <dt>Source fields</dt>
          <dd>
            <ul className="field-list">
              {finding.source_fields.map((field) => (
                <li key={field}>
                  <code>{field}</code>
                </li>
              ))}
            </ul>
          </dd>

          {finding.comparison !== null && (
            <>
              <dt>Comparison</dt>
              <dd>
                <ul className="field-list">
                  {Object.entries(finding.comparison).map(([key, value]) => (
                    <li key={key}>
                      <code>{key}</code>: {String(value)}
                    </li>
                  ))}
                </ul>
              </dd>
            </>
          )}

          <dt>Rows behind this number</dt>
          <dd>
            {aggregateOnly ? (
              <p className="hint">
                No rows are shown for this figure. It is a count only, with no
                list of students behind it.
              </p>
            ) : isRatioRowIds(finding.row_ids) ? (
              <>
                <RowList
                  label="Numerator"
                  rows={finding.row_ids.numerator}
                />
                <RowList
                  label="Denominator"
                  rows={finding.row_ids.denominator}
                />
              </>
            ) : hasRowRules(finding) ? (
              <IndicatorRows finding={finding} />
            ) : finding.row_ids.length > 0 ? (
              <RowList label="Rows" rows={finding.row_ids} />
            ) : (
              <p className="hint">
                This is a term-level figure, so there is no per-student row list.
              </p>
            )}
          </dd>
        </dl>

        <OfficeBreakdown finding={finding} />
        <IndicatorBreakdown finding={finding} />

        {!aggregateOnly && (
          <p className="hint">
            {fictional
              ? 'These rows are fictional and pseudonymous. data/VERIFY.md lists them for hand-counting.'
              : 'These rows are pseudonymous student records from the uploaded export.'}
          </p>
        )}
      </div>
    </div>
  )
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** Who authorized an aggregate in writing, the document, and who recorded it. */
function AuthorizationRecord({ authorization }: { authorization: FindingAuthorization }) {
  return (
    <ul className="field-list authorization-record">
      <li>Authorized by {authorization.authorized_by ?? 'not recorded'}</li>
      <li>Document: {authorization.document_reference ?? 'not recorded'}</li>
      <li>
        Recorded by {authorization.recorded_by ?? 'not recorded'}
        {authorization.recorded_at !== null && (
          <> on {formatTimestamp(authorization.recorded_at)}</>
        )}
      </li>
    </ul>
  )
}

function RowList({ label, rows }: { label: string; rows: string[] }) {
  return (
    <div className="row-list-block">
      <h3>
        {label} ({rows.length})
      </h3>
      <ul className="row-list">
        {rows.map((row) => (
          <li key={row}>
            <code>{row}</code>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** M5 carries a per-office breakdown with hold row IDs. */
function OfficeBreakdown({ finding }: { finding: Finding }) {
  if (!Array.isArray(finding.value)) return null
  const offices = finding.value as OfficeHolds[]
  if (offices.length === 0) return null
  return (
    <table className="office-table">
      <caption>Unresolved holds by responsible office</caption>
      <thead>
        <tr>
          <th scope="col">Office</th>
          <th scope="col">Holds</th>
          <th scope="col">Hold row IDs</th>
        </tr>
      </thead>
      <tbody>
        {offices.map((office) => (
          <tr key={office.office}>
            <td>{office.office}</td>
            <td>{office.count}</td>
            <td>
              <code>{office.hold_row_ids.join(', ')}</code>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/**
 * M8's per-rule table: each named support indicator with its count and the
 * fields it reads. The rules never combine into a total per student.
 */
function IndicatorBreakdown({ finding }: { finding: Finding }) {
  const rules = finding.rules
  if (rules === undefined || rules.length === 0) return null
  return (
    <table className="office-table">
      <caption>Support indicator rules</caption>
      <thead>
        <tr>
          <th scope="col">Rule</th>
          <th scope="col">Title</th>
          <th scope="col">Students</th>
          <th scope="col">Fields read</th>
        </tr>
      </thead>
      <tbody>
        {rules.map((rule) => (
          <tr key={rule.id}>
            <td>
              <span className="id-badge">{rule.id}</span>
            </td>
            <td>{rule.title}</td>
            <td>{rule.count}</td>
            <td>
              <code>{rule.fields_read.join(', ')}</code>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/**
 * M8's per-student list: each pseudonymous id with every rule that fired and
 * the rule's reason, exactly as the other findings list their rows. These
 * are people who may need support; a student has indicators when at least
 * one rule fires.
 */
function IndicatorRows({ finding }: { finding: Finding }) {
  const rules = finding.rules ?? []
  const rowRules = finding.row_rules ?? {}
  const rows = Array.isArray(finding.row_ids) ? finding.row_ids : []
  return (
    <div className="row-list-block">
      <h3>Support indicators by student ({rows.length})</h3>
      <ul className="indicator-rows">
        {rows.map((row) => (
          <li key={row}>
            <code>{row}</code>
            <ul>
              {(rowRules[row] ?? []).map((ruleId) => {
                const rule = rules.find((entry) => entry.id === ruleId)
                return (
                  <li key={ruleId}>
                    <span className="id-badge">{ruleId}</span>{' '}
                    {rule !== undefined ? rule.reason : ruleId}
                  </li>
                )
              })}
            </ul>
          </li>
        ))}
      </ul>
    </div>
  )
}
