import { useCallback, useEffect, useState } from 'react'

import {
  fetchCounselingAuthorization,
  recordCounselingAuthorization,
  revokeCounselingAuthorization,
  type CounselingAuthorization,
  type SaveAuthorizationResult,
} from '../counseling'
import { formatTimestamp, type LoadState } from '../states'

/** The section anchor, for links into Institution settings. */
export const COUNSELING_SECTION_ID = 'inst-counseling'

type SaveState =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'confirm-revoke' }
  | { kind: 'failed'; message: string; errors: string[] }

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

  const load = useCallback(async () => {
    setState({ kind: 'loading' })
    try {
      setState({ kind: 'ready', data: await fetchCounselingAuthorization() })
    } catch (error) {
      setState({
        kind: 'error',
        message: error instanceof Error ? error.message : String(error),
      })
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  const apply = useCallback(
    async (action: () => Promise<SaveAuthorizationResult>) => {
      setSave({ kind: 'saving' })
      let result: SaveAuthorizationResult
      try {
        result = await action()
      } catch (error) {
        setSave({
          kind: 'failed',
          message: error instanceof Error ? error.message : 'the server could not be reached.',
          errors: [],
        })
        return
      }
      if (!result.ok) {
        setSave({ kind: 'failed', message: result.message, errors: result.errors })
        return
      }
      setState({ kind: 'ready', data: result.authorization })
      setAuthorizedBy('')
      setDocumentReference('')
      setSave({ kind: 'idle' })
      onChanged()
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
    void apply(() => recordCounselingAuthorization(authorizedBy, documentReference))
  }, [apply, authorizedBy, documentReference])

  const busy = save.kind === 'saving'

  return (
    <section id={COUNSELING_SECTION_ID} aria-labelledby="inst-counseling-heading">
      <h2 id="inst-counseling-heading">Counseling figure</h2>
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
        <div className="state-panel error-panel" role="alert">
          <h3>The authorization could not be loaded</h3>
          <p>{state.message}</p>
          <button type="button" onClick={() => void load()}>
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
          {save.kind === 'confirm-revoke' ? (
            <div className="confirm-inline" role="alertdialog" aria-live="polite">
              <p>Revoke the authorization? The count leaves the briefing at once.</p>
              <div className="confirm-actions">
                <button
                  type="button"
                  className="danger-button"
                  onClick={() => void apply(revokeCounselingAuthorization)}
                >
                  Revoke
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setSave({ kind: 'idle' })}
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              className="secondary"
              disabled={busy}
              aria-busy={busy}
              onClick={() => setSave({ kind: 'confirm-revoke' })}
            >
              {busy ? 'Revoking…' : 'Revoke the authorization'}
            </button>
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
          <p className="counseling-auth-state">Not authorized. The briefing shows no counseling figure.</p>
          <div className="counseling-auth-fields">
            <label>
              Authorized by (name and title)
              <input
                type="text"
                autoComplete="off"
                placeholder="Director of Counseling"
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
                <span className="field-error" id="counseling-by-error" role="alert">
                  {fieldErrors.authorizedBy}
                </span>
              )}
            </label>
            <label>
              Document reference
              <input
                type="text"
                autoComplete="off"
                placeholder="Memo and date"
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
                <span className="field-error" id="counseling-doc-error" role="alert">
                  {fieldErrors.documentReference}
                </span>
              )}
            </label>
          </div>
          <div className="office-book-actions">
            <button type="submit" className="primary-button" disabled={busy} aria-busy={busy}>
              {busy ? 'Recording…' : 'Record the authorization'}
            </button>
          </div>
        </form>
      )}
      {save.kind === 'failed' && (
        <div className="upload-errors" role="alert">
          <p>Nothing changed: {save.message}</p>
          {save.errors.length > 0 && (
            <ul>
              {save.errors.map((problem, index) => (
                <li key={index}>{problem}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  )
}
