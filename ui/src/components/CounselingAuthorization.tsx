import { useCallback, useEffect, useRef, useState } from 'react'

import {
  fetchCounselingAuthorization,
  recordCounselingAuthorization,
  revokeCounselingAuthorization,
  type CounselingAuthorization,
  type SaveAuthorizationResult,
} from '../counseling'
import { friendlyError, friendlyLoadError } from '../errors'
import { formatTimestamp, type LoadState } from '../states'
import './Institution.css'

/** The section anchor, for links into Institution settings. */
export const COUNSELING_SECTION_ID = 'inst-counseling'

type SaveState =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'confirm-revoke' }
  | { kind: 'done'; message: string }
  | { kind: 'refused'; errors: string[] }
  | { kind: 'failed'; message: string }

interface FieldErrors {
  authorizedBy?: string
  documentReference?: string
}

/**
 * Institution settings: the counseling figure. One sentence on what the
 * authorization enables, the current state, and one action (Record, or
 * Revoke behind an inline confirmation). Recording never opens a student's
 * counseling record to anyone. It lets the briefing show one count, and a
 * small count is withheld. `onChanged` reloads the findings so the briefing
 * gains or loses the figure at once.
 */
export function CounselingAuthorizationSection({ onChanged }: { onChanged: () => void }) {
  const [state, setState] = useState<LoadState<CounselingAuthorization>>({ kind: 'loading' })
  const [authorizedBy, setAuthorizedBy] = useState('')
  const [documentReference, setDocumentReference] = useState('')
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [save, setSave] = useState<SaveState>({ kind: 'idle' })
  const cancelRef = useRef<HTMLButtonElement>(null)
  const revokeRef = useRef<HTMLButtonElement>(null)
  const headingRef = useRef<HTMLHeadingElement>(null)
  const returnFocus = useRef(false)

  const load = useCallback(async () => {
    setState({ kind: 'loading' })
    try {
      setState({ kind: 'ready', data: await fetchCounselingAuthorization() })
    } catch (error) {
      setState({ kind: 'error', message: friendlyLoadError(error) })
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  // The revoke confirmation takes focus; when it goes, focus returns to
  // the Revoke button (or the heading, once the state changed).
  const confirming = save.kind === 'confirm-revoke'
  useEffect(() => {
    if (confirming) {
      cancelRef.current?.focus()
      returnFocus.current = true
    } else if (returnFocus.current && save.kind !== 'saving') {
      returnFocus.current = false
      ;(revokeRef.current ?? headingRef.current)?.focus()
    }
  }, [confirming, save.kind])

  const apply = useCallback(
    async (action: () => Promise<SaveAuthorizationResult>, done: string, what: string) => {
      setSave({ kind: 'saving' })
      try {
        const result = await action()
        if (!result.ok) {
          setSave({ kind: 'refused', errors: result.errors })
          return
        }
        setState({ kind: 'ready', data: result.authorization })
        setAuthorizedBy('')
        setDocumentReference('')
        setSave({ kind: 'done', message: done })
        onChanged()
      } catch (error) {
        setSave({ kind: 'failed', message: friendlyError(error, what) })
      }
    },
    [onChanged],
  )

  const record = useCallback(() => {
    // Checked here exactly as the API checks it, so an error lands under
    // its field and nothing is sent.
    const errors: FieldErrors = {}
    if (authorizedBy.trim() === '') {
      errors.authorizedBy = 'Enter the name and title of the person who authorized it.'
    }
    if (documentReference.trim() === '') {
      errors.documentReference = 'Enter the reference of the written authorization.'
    }
    setFieldErrors(errors)
    if (errors.authorizedBy !== undefined || errors.documentReference !== undefined) return
    void apply(
      () => recordCounselingAuthorization(authorizedBy, documentReference),
      'Recorded. The briefing now shows the counseling count.',
      'The authorization',
    )
  }, [apply, authorizedBy, documentReference])

  const busy = save.kind === 'saving'
  const filled = authorizedBy.trim() !== '' && documentReference.trim() !== ''

  return (
    <section
      id={COUNSELING_SECTION_ID}
      className="inst-card"
      aria-labelledby="inst-counseling-heading"
    >
      <h2 id="inst-counseling-heading" ref={headingRef} tabIndex={-1}>
        Counseling figure
      </h2>
      <p className="hint">
        With the counseling director&rsquo;s written authorization, the
        briefing shows one count of students not yet registered who have had
        counseling contact this term, never who they are, and a small count
        is withheld.
      </p>
      {state.kind === 'loading' && (
        <p className="status-line">Loading the authorization…</p>
      )}
      {state.kind === 'error' && (
        <div className="state-error" role="alert">
          <h3>We couldn&rsquo;t load the authorization</h3>
          <p>{state.message}</p>
          <button type="button" className="btn-secondary" onClick={() => void load()}>
            Retry
          </button>
        </div>
      )}
      {state.kind === 'ready' && state.data.authorized && (
        <div className="counseling-auth">
          <p className="counseling-auth-state">
            Authorized by {state.data.authorizedBy}, {state.data.documentReference}.
          </p>
          <p className="hint">
            Recorded by {state.data.recordedBy}
            {state.data.recordedAt !== null && (
              <> on {formatTimestamp(state.data.recordedAt)}</>
            )}
            .
          </p>
          {confirming ? (
            <div
              className="confirm-inline"
              role="alertdialog"
              aria-labelledby="counseling-revoke-question"
              onKeyDown={(event) => {
                if (event.key === 'Escape') {
                  event.stopPropagation()
                  setSave({ kind: 'idle' })
                }
              }}
            >
              <p id="counseling-revoke-question">
                Revoke the authorization? The count leaves the briefing at once.
              </p>
              <div className="confirm-actions">
                <button
                  type="button"
                  className="btn-danger"
                  onClick={() =>
                    void apply(
                      revokeCounselingAuthorization,
                      'Revoked. The briefing no longer shows the counseling count.',
                      'The change',
                    )
                  }
                >
                  Revoke
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  ref={cancelRef}
                  onClick={() => setSave({ kind: 'idle' })}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="inst-actions">
              <button
                type="button"
                className="btn-danger"
                ref={revokeRef}
                disabled={busy}
                aria-busy={busy}
                onClick={() => setSave({ kind: 'confirm-revoke' })}
              >
                {busy ? (
                  <span className="inst-busy">
                    <span className="save-spinner" aria-hidden="true" />
                    Revoking…
                  </span>
                ) : (
                  'Revoke the authorization'
                )}
              </button>
            </div>
          )}
        </div>
      )}
      {state.kind === 'ready' && !state.data.authorized && (
        <form
          className="counseling-auth"
          noValidate
          onSubmit={(event) => {
            event.preventDefault()
            record()
          }}
        >
          <p className="counseling-auth-state">
            Not authorized. The briefing shows no counseling figure.
          </p>
          <div className="inst-fields">
            <div className="inst-field">
              <label htmlFor="counseling-by">Authorized by (name and title)</label>
              <input
                id="counseling-by"
                type="text"
                autoComplete="off"
                placeholder="e.g. Dr. J. Smith, Director of Counseling"
                value={authorizedBy}
                disabled={busy}
                aria-invalid={fieldErrors.authorizedBy !== undefined}
                aria-describedby={
                  fieldErrors.authorizedBy !== undefined ? 'counseling-by-error' : undefined
                }
                onChange={(event) => {
                  setAuthorizedBy(event.target.value)
                  setFieldErrors((errors) => ({ ...errors, authorizedBy: undefined }))
                }}
              />
              {fieldErrors.authorizedBy !== undefined && (
                <p className="field-error" id="counseling-by-error" role="alert">
                  {fieldErrors.authorizedBy}
                </p>
              )}
            </div>
            <div className="inst-field">
              <label htmlFor="counseling-doc">Document reference</label>
              <input
                id="counseling-doc"
                type="text"
                autoComplete="off"
                placeholder="e.g. Memo dated 12 March 2026"
                value={documentReference}
                disabled={busy}
                aria-invalid={fieldErrors.documentReference !== undefined}
                aria-describedby={
                  fieldErrors.documentReference !== undefined ? 'counseling-doc-error' : undefined
                }
                onChange={(event) => {
                  setDocumentReference(event.target.value)
                  setFieldErrors((errors) => ({ ...errors, documentReference: undefined }))
                }}
              />
              {fieldErrors.documentReference !== undefined && (
                <p className="field-error" id="counseling-doc-error" role="alert">
                  {fieldErrors.documentReference}
                </p>
              )}
            </div>
          </div>
          <div className="inst-actions">
            {/* Secondary until both fields are filled: the less common action
                stays quiet until it is ready to go. */}
            <button
              type="submit"
              className={filled ? 'btn-primary' : 'btn-secondary'}
              disabled={busy}
              aria-busy={busy}
            >
              {busy ? (
                <span className="inst-busy">
                  <span className="save-spinner" aria-hidden="true" />
                  Recording…
                </span>
              ) : (
                'Record the authorization'
              )}
            </button>
          </div>
        </form>
      )}
      {save.kind === 'done' && (
        <p className="office-saved" role="status">
          {save.message} The audit log records the change.
        </p>
      )}
      {save.kind === 'failed' && (
        <p className="error-line inst-under-button" role="alert">
          {save.message}
        </p>
      )}
      {save.kind === 'refused' && (
        <div className="upload-errors" role="alert">
          <p>Nothing changed. Check both fields and try again.</p>
          {save.errors.length > 0 && (
            <details className="fold">
              <summary>Technical detail</summary>
              <ul>
                {save.errors.map((problem, index) => (
                  <li key={index}>{problem}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </section>
  )
}
