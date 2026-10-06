import { Fragment } from 'react'

import type { Finding, FindingAuthorization, OfficeHolds, RatioRowIds } from '../api'
import { isAggregateOnly, suppressionNote } from '../counseling'
import { fieldLabels } from '../fieldLabels'
import { findingDefinition, findingLabel } from '../findingLabels'
import { findingDisplay, formatTimestamp } from '../states'
import { ChevronIcon } from './icons'
import { SidePanel } from './SidePanel'

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

/** How many records sit behind the figure (0 for a term-level figure). */
function recordCount(finding: Finding): number {
  if (isAggregateOnly(finding)) return 0
  if (isRatioRowIds(finding.row_ids)) {
    return finding.row_ids.numerator.length + finding.row_ids.denominator.length
  }
  return finding.row_ids.length
}

/** Plain names for the comparison values a finding carries. */
const COMPARISON_LABELS: Record<string, string> = {
  prior_year_registered_continuing: 'Registered by the same date last year',
  prior_year_registered_credit_hours: 'Credit hours by the same date last year',
  prior_year_equivalent_date: 'Same date last year',
  threshold_usd: 'Hold amount threshold',
}

function comparisonValue(key: string, value: unknown): string {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return key.endsWith('_usd')
      ? value.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
      : value.toLocaleString('en-US')
  }
  return String(value)
}

/**
 * The evidence for one figure (Beat 4), in the shared slide-over panel: a
 * plain one-sentence definition, the value, and what it is compared with
 * first. How it is computed (the fields it reads, and the formula and raw
 * field names inside a Technical detail) and the records behind it each sit
 * behind a fold. An aggregate-only figure (M9, the authorized counseling
 * count) shows its authorization record and never a row. Escape and the
 * close button are the shared panel's, so one Escape closes only this layer.
 */
export function EvidenceDrawer({ finding, fictional, onClose }: EvidenceDrawerProps) {
  const display = findingDisplay(finding)
  // M9: an authorized aggregate with no rows behind it, ever.
  const aggregateOnly = isAggregateOnly(finding)
  const withheld = suppressionNote(finding)
  const definition = findingDefinition(finding.id)
  const comparison = Object.entries(finding.comparison ?? {}).filter(
    ([key]) => COMPARISON_LABELS[key] !== undefined,
  )
  const records = recordCount(finding)

  return (
    <SidePanel title={findingLabel(finding.id, finding.title)} onClose={onClose}>
      <div className="evidence-panel">
        {definition !== null && <p className="panel-intro">{definition}</p>}

        <p className="evidence-value">
          <span className={display.missing ? 'drawer-value missing' : 'drawer-value'}>
            {withheld !== null ? `${capitalize(display.text)} students` : display.text}
          </span>
        </p>
        {withheld !== null && <p className="hint">{withheld}</p>}
        {display.missing && <p className="hint">This figure is not available for this data.</p>}

        {comparison.length > 0 && (
          <dl className="kv evidence-comparison">
            {comparison.map(([key, value]) => (
              <Fragment key={key}>
                <dt>{COMPARISON_LABELS[key]}</dt>
                <dd>{comparisonValue(key, value)}</dd>
              </Fragment>
            ))}
          </dl>
        )}

        {finding.authorization !== undefined && (
          <AuthorizationRecord authorization={finding.authorization} />
        )}

        <OfficeBreakdown finding={finding} />
        <IndicatorBreakdown finding={finding} />

        <details className="fold technical-detail">
          <summary>
            <ChevronIcon />
            How it is computed
          </summary>
          <p>It reads these fields from each student record:</p>
          <ul className="plain-list">
            {fieldLabels(finding.source_fields).map((label) => (
              <li key={label}>{label}</li>
            ))}
          </ul>
          <details className="fold technical-detail">
            <summary>
              <ChevronIcon />
              Technical detail
            </summary>
            {display.missing && display.reason !== null && (
              <p>Why it is not available: {display.reason}</p>
            )}
            <p>Formula</p>
            <code className="formula">{finding.definition}</code>
            <p>Field names</p>
            <ul className="field-list">
              {finding.source_fields.map((field) => (
                <li key={field}>
                  <code>{field}</code>
                </li>
              ))}
            </ul>
          </details>
        </details>

        {aggregateOnly ? (
          <p className="hint">
            No records are shown for this figure. It is a count only, with no list of
            students behind it.
          </p>
        ) : records === 0 ? (
          <p className="hint">
            This is a term-level figure, so there is no list of students behind it.
          </p>
        ) : (
          <details className="fold technical-detail">
            <summary>
              <ChevronIcon />
              Show the records ({records.toLocaleString('en-US')})
            </summary>
            <Records finding={finding} />
            <p className="hint">
              {fictional
                ? 'These records are fictional and pseudonymous.'
                : 'These are pseudonymous student records from the uploaded export.'}
            </p>
          </details>
        )}
      </div>
    </SidePanel>
  )
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** The records behind the figure, by the shape the finding carries. */
function Records({ finding }: { finding: Finding }) {
  if (isRatioRowIds(finding.row_ids)) {
    return (
      <>
        <RowList label="Counted now" rows={finding.row_ids.numerator} />
        <RowList label="Compared with last year" rows={finding.row_ids.denominator} />
      </>
    )
  }
  if (hasRowRules(finding)) return <IndicatorRows finding={finding} />
  if (Array.isArray(finding.value)) {
    const offices = finding.value as OfficeHolds[]
    return (
      <>
        {offices.map((office) => (
          <RowList key={office.office} label={office.office} rows={office.hold_row_ids} />
        ))}
      </>
    )
  }
  return <RowList label="Students" rows={finding.row_ids} />
}

/** Who authorized an aggregate in writing, the document, and who recorded it. */
function AuthorizationRecord({ authorization }: { authorization: FindingAuthorization }) {
  return (
    <dl className="kv authorization-record">
        <dt>Authorized by</dt>
        <dd>{authorization.authorized_by ?? 'Not recorded'}</dd>
        <dt>Document</dt>
        <dd>{authorization.document_reference ?? 'Not recorded'}</dd>
        <dt>Recorded by</dt>
        <dd>
          {authorization.recorded_by ?? 'Not recorded'}
          {authorization.recorded_at !== null && (
            <> on {formatTimestamp(authorization.recorded_at)}</>
          )}
        </dd>
    </dl>
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
          <li key={row}>{row}</li>
        ))}
      </ul>
    </div>
  )
}

/** M5's per-office counts (the student lists are under Show the records). */
function OfficeBreakdown({ finding }: { finding: Finding }) {
  if (!Array.isArray(finding.value)) return null
  const offices = finding.value as OfficeHolds[]
  if (offices.length === 0) return null
  return (
    <table className="office-table">
      <caption>Unresolved holds by office</caption>
      <thead>
        <tr>
          <th scope="col">Office</th>
          <th scope="col">Holds</th>
        </tr>
      </thead>
      <tbody>
        {offices.map((office) => (
          <tr key={office.office}>
            <td>{office.office}</td>
            <td>{office.count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/**
 * M8's indicators: each named support indicator with its count. The
 * indicators never combine into a total per student.
 */
function IndicatorBreakdown({ finding }: { finding: Finding }) {
  const rules = finding.rules
  if (rules === undefined || rules.length === 0) return null
  return (
    <table className="office-table">
      <caption>Support indicators</caption>
      <thead>
        <tr>
          <th scope="col">Indicator</th>
          <th scope="col">Students</th>
        </tr>
      </thead>
      <tbody>
        {rules.map((rule) => (
          <tr key={rule.id}>
            <td>{rule.title}</td>
            <td>{rule.count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/**
 * M8's per-student list: each pseudonymous id with every indicator that
 * applies, by name. These are people who may need support; a student has
 * indicators when at least one applies.
 */
function IndicatorRows({ finding }: { finding: Finding }) {
  const rules = finding.rules ?? []
  const rowRules = finding.row_rules ?? {}
  const rows = Array.isArray(finding.row_ids) ? finding.row_ids : []
  return (
    <div className="row-list-block">
      <h3>Students and their indicators ({rows.length})</h3>
      <ul className="indicator-rows">
        {rows.map((row) => (
          <li key={row}>
            {row}
            <ul>
              {(rowRules[row] ?? []).map((ruleId) => {
                const rule = rules.find((entry) => entry.id === ruleId)
                return <li key={ruleId}>{rule !== undefined ? rule.title : 'Indicator'}</li>
              })}
            </ul>
          </li>
        ))}
      </ul>
    </div>
  )
}
