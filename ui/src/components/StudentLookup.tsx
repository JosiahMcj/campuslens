import { useRef, useState, type FormEvent } from 'react'

import { friendlyLoadError } from '../errors'
import {
  MIN_QUERY_CHARS,
  searchStudents,
  type StudentRecord,
  type StudentSearch,
} from '../students'
import { SearchIcon } from './icons'
import './StudentLookup.css'

type LookupState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; result: StudentSearch }

/** One student's record, read from the directory and nothing else. */
function StudentCard({ student }: { student: StudentRecord }) {
  return (
    <li className="student-card">
      <div className="student-head">
        <h3>{student.name}</h3>
        <span className="student-id">{student.student_id}</span>
      </div>
      <dl className="student-facts">
        <div>
          <dt>Program</dt>
          <dd>{student.program}</dd>
        </div>
        <div>
          <dt>Advisor</dt>
          <dd>{student.advisor}</dd>
        </div>
        <div>
          <dt>Degree progress</dt>
          <dd>
            {student.degree_progress_display}
            <progress
              className="student-progress"
              max={1}
              value={student.degree_progress}
              aria-label={`Degree progress, ${student.degree_progress_display}`}
            />
          </dd>
        </div>
        <div>
          <dt>Current GPA</dt>
          <dd>
            {student.current_gpa_display}{' '}
            <span className="student-change">
              ({student.gpa_change_display} from {student.previous_gpa_display})
            </span>
          </dd>
        </div>
        <div className="student-holds">
          <dt>Holds</dt>
          <dd>
            {student.holds.length === 0 ? (
              'None'
            ) : (
              <ul>
                {student.holds.map((hold) => (
                  <li key={hold}>{hold}</li>
                ))}
              </ul>
            )}
          </dd>
        </div>
      </dl>
    </li>
  )
}

/**
 * Find a student by name. One search per press of Search (never per
 * keystroke), because the API writes each search to the audit log. The
 * directory is fictional demonstration data and says so beside the heading.
 */
export function StudentLookup() {
  const [query, setQuery] = useState('')
  const [state, setState] = useState<LookupState>({ kind: 'idle' })
  const [hint, setHint] = useState<string | null>(null)
  const latest = useRef(0)
  const resultsRef = useRef<HTMLDivElement>(null)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const text = query.trim()
    if (text.length < MIN_QUERY_CHARS) {
      setHint(`Type at least ${MIN_QUERY_CHARS} letters of a name.`)
      return
    }
    setHint(null)
    const run = (latest.current += 1)
    setState({ kind: 'loading' })
    searchStudents(text).then(
      (result) => {
        if (run !== latest.current) return
        setState({ kind: 'ready', result })
        resultsRef.current?.focus()
      },
      (error: unknown) => {
        if (run !== latest.current) return
        setState({ kind: 'error', message: friendlyLoadError(error) })
      },
    )
  }

  return (
    <div className="student-lookup">
      <p className="student-notice">
        <span className="data-tag">Fictional data</span>
        This directory is made-up demonstration data. Each search is recorded in the audit
        log with your name; the log does not keep what you typed or who you found.
      </p>
      <form className="student-search" role="search" onSubmit={submit}>
        <label htmlFor="student-query">Student name</label>
        <div className="student-search-row">
          <input
            id="student-query"
            type="search"
            autoComplete="off"
            spellCheck={false}
            placeholder="e.g. Obinna Amadi"
            value={query}
            aria-invalid={hint !== null}
            aria-describedby={hint !== null ? 'student-query-hint' : undefined}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button
            type="submit"
            className="btn-primary"
            aria-busy={state.kind === 'loading'}
          >
            {state.kind === 'loading' ? (
              <span className="spinner" aria-hidden="true" />
            ) : (
              <SearchIcon />
            )}
            {state.kind === 'loading' ? 'Searching…' : 'Search'}
          </button>
        </div>
        {hint !== null && (
          <p id="student-query-hint" className="field-error" role="alert">
            {hint}
          </p>
        )}
      </form>

      <div ref={resultsRef} tabIndex={-1} className="student-results" aria-live="polite">
        {state.kind === 'error' && (
          <div className="state-error state-panel error-panel" role="alert">
            <p>Couldn't search the directory. {state.message}</p>
          </div>
        )}
        {state.kind === 'ready' && state.result.total === 0 && (
          <p className="state-empty hint">
            No student matches “{state.result.query}”. Check the spelling, or try just the
            first or last name.
          </p>
        )}
        {state.kind === 'ready' && state.result.total > 0 && (
          <>
            <p className="student-count">
              {state.result.total === 1
                ? '1 student found.'
                : state.result.shown < state.result.total
                  ? `${state.result.total} students found. Showing the first ${state.result.shown}; add more of the name to narrow it.`
                  : `${state.result.total} students found.`}
            </p>
            <ul className="student-list">
              {state.result.students.map((student) => (
                <StudentCard key={student.student_id} student={student} />
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  )
}
