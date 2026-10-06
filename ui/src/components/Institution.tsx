import { useCallback, useEffect, useRef, useState } from 'react'

import { roleDisplayName, type Role } from '../auth'
import { CounselingAuthorizationSection } from './CounselingAuthorization'
import {
  activateDataset,
  deleteDataset,
  fetchDatasets,
  formatBytes,
  rowCountLabel,
  uploadDataset,
  type DatasetRow,
  type UploadResult,
} from '../datasets'
import {
  draftRowsFrom,
  fetchDecisionOffices,
  fetchOffices,
  officesForSave,
  saveOffices,
  validateOfficeRows,
  type OfficeDraftRow,
  type OfficeRowErrors,
} from '../offices'
import { formatTimestamp, type LoadState } from '../states'
import {
  addUser,
  fetchUsers,
  setUserDisabled,
  setUserRole,
  userStatusLabel,
  type CreatedUser,
  type UserRow,
} from '../users'

/** The active dataset's id and fictional flag, from the findings meta. */
export interface ActiveDatasetMeta {
  id: number
  fictional: boolean
}

interface InstitutionProps {
  /** The signed-in admin's institution name. */
  institutionName: string
  /** Which dataset is active and fictional, once the findings loaded. */
  activeDataset: ActiveDatasetMeta | null
  /** The signed-in admin's email; their own user row is marked "you". */
  currentUserEmail: string
  /** Called after any change that recomputes the briefing (activate). */
  onDataChanged: () => void
}

type ConfirmState = { action: 'activate' | 'delete'; dataset: DatasetRow } | null

type UserConfirmState =
  | { action: 'disable' | 'enable'; user: UserRow }
  | { action: 'role'; user: UserRow; role: Role }
  | null

type UploadState =
  | { kind: 'idle' }
  | { kind: 'uploading' }
  | { kind: 'done'; result: Extract<UploadResult, { ok: true }> }
  | { kind: 'failed'; errors: string[] }

type OfficeSaveState =
  | { kind: 'idle' }
  | { kind: 'invalid' }
  | { kind: 'saving' }
  | { kind: 'saved'; count: number }
  | { kind: 'failed'; message: string; errors: string[] }

const USER_ROLES: readonly Role[] = ['admin', 'executive', 'staff', 'reviewer', 'aid']

/** The section anchor the decision panel links to. */
export const OFFICES_SECTION_ID = 'inst-offices'

/**
 * The Institution area (admin only): the institution's users with their
 * roles and status (with the one-time password of a newly added user shown
 * once in a callout), the office address book approved follow-ups are sent
 * to (one mailbox per office, edited inline and saved whole), the
 * counseling figure's recorded authorization (record or revoke), the
 * institution's datasets with their row counts and
 * active and fictional flags, an upload form (JSON only, with the API's
 * validation errors listed line by line and the counseling flag shown as
 * information), and activate / soft delete / disable / enable / role
 * changes behind inline confirmations, never modals. Backups are
 * deliberately absent: they are an operator task.
 */
export function Institution({
  institutionName,
  activeDataset,
  currentUserEmail,
  onDataChanged,
}: InstitutionProps) {
  const [datasetsState, setDatasetsState] = useState<LoadState<DatasetRow[]>>({
    kind: 'loading',
  })
  const [file, setFile] = useState<{ name: string; size: number; bytes: ArrayBuffer } | null>(
    null,
  )
  const [fileError, setFileError] = useState<string | null>(null)
  const [uploadState, setUploadState] = useState<UploadState>({ kind: 'idle' })
  const [confirm, setConfirm] = useState<ConfirmState>(null)
  const [actionBusy, setActionBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const [usersState, setUsersState] = useState<LoadState<UserRow[]>>({
    kind: 'loading',
  })
  const [newEmail, setNewEmail] = useState('')
  const [newRole, setNewRole] = useState<Role>('staff')
  const [addBusy, setAddBusy] = useState(false)
  const [addErrors, setAddErrors] = useState<string[]>([])
  const [created, setCreated] = useState<CreatedUser | null>(null)
  const [copied, setCopied] = useState(false)
  const [userConfirm, setUserConfirm] = useState<UserConfirmState>(null)
  const [userBusy, setUserBusy] = useState(false)
  const [userError, setUserError] = useState<string | null>(null)

  const [officesState, setOfficesState] = useState<LoadState<null>>({ kind: 'loading' })
  const [officeRows, setOfficeRows] = useState<OfficeDraftRow[]>([])
  const [decisionOffices, setDecisionOffices] = useState<string[]>([])
  const [officeErrors, setOfficeErrors] = useState<Record<string, OfficeRowErrors>>({})
  const [officeSave, setOfficeSave] = useState<OfficeSaveState>({ kind: 'idle' })
  const newOfficeCount = useRef(0)
  const focusKey = useRef<string | null>(null)

  const loadOffices = useCallback(async () => {
    setOfficesState({ kind: 'loading' })
    try {
      const [book, routed] = await Promise.all([fetchOffices(), fetchDecisionOffices()])
      setDecisionOffices(routed)
      setOfficeRows(draftRowsFrom(book, routed))
      setOfficeErrors({})
      setOfficesState({ kind: 'ready', data: null })
    } catch (error) {
      setOfficesState({
        kind: 'error',
        message: error instanceof Error ? error.message : String(error),
      })
    }
  }, [])

  const editOfficeRow = useCallback(
    (key: string, field: 'office' | 'email', value: string) => {
      setOfficeRows((rows) =>
        rows.map((row) => (row.key === key ? { ...row, [field]: value } : row)),
      )
      setOfficeErrors((errors) => {
        if (errors[key]?.[field] === undefined) return errors
        const next = { ...errors, [key]: { ...errors[key], [field]: undefined } }
        if (next[key].office === undefined && next[key].email === undefined) delete next[key]
        return next
      })
      setOfficeSave((state) => (state.kind === 'saving' ? state : { kind: 'idle' }))
    },
    [],
  )

  const removeOfficeRow = useCallback((row: OfficeDraftRow) => {
    // An office a decision routes to stays listed, without its mailbox, so
    // the gap stays visible; any other row leaves the book on the next Save.
    setOfficeRows((rows) =>
      row.known
        ? rows.map((r) => (r.key === row.key ? { ...r, email: '' } : r))
        : rows.filter((r) => r.key !== row.key),
    )
    setOfficeErrors((errors) => {
      if (errors[row.key] === undefined) return errors
      const next = { ...errors }
      delete next[row.key]
      return next
    })
    setOfficeSave({ kind: 'idle' })
  }, [])

  const addOfficeRow = useCallback(() => {
    newOfficeCount.current += 1
    const key = `new:${newOfficeCount.current}`
    focusKey.current = key
    setOfficeRows((rows) => [...rows, { key, office: '', email: '', known: false }])
    setOfficeSave({ kind: 'idle' })
  }, [])

  useEffect(() => {
    if (focusKey.current === null) return
    const input = document.getElementById(`office-name-${focusKey.current}`)
    focusKey.current = null
    input?.focus()
  }, [officeRows])

  const submitOffices = useCallback(async () => {
    if (officeSave.kind === 'saving') return
    const errors = validateOfficeRows(officeRows)
    setOfficeErrors(errors)
    if (Object.keys(errors).length > 0) {
      setOfficeSave({ kind: 'invalid' })
      return
    }
    setOfficeSave({ kind: 'saving' })
    let result: Awaited<ReturnType<typeof saveOffices>>
    try {
      result = await saveOffices(officesForSave(officeRows))
    } catch (error) {
      // A network failure, or a session that ended (the app returns to
      // the sign-in screen on its own in that case).
      setOfficeSave({
        kind: 'failed',
        message: error instanceof Error ? error.message : 'the server could not be reached.',
        errors: [],
      })
      return
    }
    if (result.ok) {
      setOfficeRows(draftRowsFrom(result.offices, decisionOffices))
      setOfficeSave({ kind: 'saved', count: result.offices.length })
    } else {
      setOfficeSave({ kind: 'failed', message: result.message, errors: result.errors })
    }
  }, [officeSave.kind, officeRows, decisionOffices])

  const loadUsers = useCallback(async () => {
    try {
      setUsersState({ kind: 'ready', data: await fetchUsers() })
    } catch (error) {
      setUsersState({
        kind: 'error',
        message: error instanceof Error ? error.message : String(error),
      })
    }
  }, [])

  const load = useCallback(async () => {
    try {
      setDatasetsState({ kind: 'ready', data: await fetchDatasets() })
    } catch (error) {
      setDatasetsState({
        kind: 'error',
        message: error instanceof Error ? error.message : String(error),
      })
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
    void loadUsers()
    void loadOffices()
  }, [load, loadUsers, loadOffices])

  // Arriving from the decision panel's link (/institution#inst-offices):
  // once the section has rendered, bring it into view.
  const officesReady = officesState.kind === 'ready'
  useEffect(() => {
    if (!officesReady || window.location.hash !== `#${OFFICES_SECTION_ID}`) return
    document.getElementById(OFFICES_SECTION_ID)?.scrollIntoView?.({ block: 'start' })
  }, [officesReady])

  const pickFile = useCallback((files: FileList | null) => {
    setFileError(null)
    setUploadState({ kind: 'idle' })
    const picked = files?.[0]
    if (picked === undefined) {
      setFile(null)
      return
    }
    if (!picked.name.toLowerCase().endsWith('.json')) {
      setFile(null)
      setFileError('Choose a JSON file. The dataset document is JSON only.')
      return
    }
    void picked.arrayBuffer().then((bytes) => {
      setFile({ name: picked.name, size: picked.size, bytes })
    })
  }, [])

  const upload = useCallback(async () => {
    if (file === null || uploadState.kind === 'uploading') return
    setUploadState({ kind: 'uploading' })
    const result = await uploadDataset(file.bytes)
    if (result.ok) {
      setFile(null)
      setUploadState({ kind: 'done', result })
      await load()
    } else {
      setUploadState({ kind: 'failed', errors: result.errors })
    }
  }, [file, uploadState.kind, load])

  const runAction = useCallback(
    async (action: 'activate' | 'delete', dataset: DatasetRow) => {
      setActionBusy(true)
      setActionError(null)
      const result =
        action === 'activate'
          ? await activateDataset(dataset.id)
          : await deleteDataset(dataset.id)
      setActionBusy(false)
      setConfirm(null)
      if (!result.ok) {
        setActionError(result.message)
        return
      }
      await load()
      if (action === 'activate') onDataChanged()
    },
    [load, onDataChanged],
  )

  const submitAddUser = useCallback(async () => {
    const email = newEmail.trim()
    if (addBusy || email === '') return
    setAddBusy(true)
    setAddErrors([])
    setCreated(null)
    const result = await addUser(email, newRole)
    setAddBusy(false)
    if (result.ok) {
      setNewEmail('')
      setNewRole('staff')
      setCreated(result.user)
      setCopied(false)
      await loadUsers()
    } else {
      setAddErrors(result.errors)
    }
  }, [addBusy, newEmail, newRole, loadUsers])

  const runUserAction = useCallback(
    async (pending: NonNullable<UserConfirmState>) => {
      setUserBusy(true)
      setUserError(null)
      const result =
        pending.action === 'role'
          ? await setUserRole(pending.user.id, pending.role)
          : await setUserDisabled(pending.user.id, pending.action === 'disable')
      setUserBusy(false)
      setUserConfirm(null)
      if (!result.ok) {
        setUserError(result.message)
        return
      }
      await loadUsers()
    },
    [loadUsers],
  )

  const copyPassword = useCallback(async (password: string) => {
    try {
      await navigator.clipboard.writeText(password)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }, [])

  return (
    <div className="institution">
      <section aria-labelledby="inst-users">
        <h2 id="inst-users">Users</h2>
        <p className="hint">
          Everyone who can sign in to {institutionName}. Adding a user creates
          a sign in with a one time password. Disabling ends access at the
          next request. The audit log records every change.
        </p>
        {usersState.kind === 'loading' && (
          <p className="status-line">Loading the users…</p>
        )}
        {usersState.kind === 'error' && (
          <div className="state-panel error-panel" role="alert">
            <h3>The users could not be loaded</h3>
            <p>{usersState.message}</p>
            <button type="button" onClick={() => void loadUsers()}>
              Retry
            </button>
          </div>
        )}
        {usersState.kind === 'ready' && (
          <table className="office-table dataset-table user-table">
            <caption>Users of {institutionName}</caption>
            <thead>
              <tr>
                <th scope="col">Email</th>
                <th scope="col">Role</th>
                <th scope="col">Status</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {usersState.data.map((user) => {
                const isYou = user.email === currentUserEmail
                return (
                  <tr key={user.id}>
                    <td>
                      {user.email}
                      {isYou && <span className="dataset-flag">you</span>}
                    </td>
                    <td>
                      <select
                        aria-label={`Role for ${user.email}`}
                        value={user.role}
                        disabled={userBusy}
                        onChange={(event) => {
                          const role = event.target.value as Role
                          if (role !== user.role) {
                            setUserError(null)
                            setUserConfirm({ action: 'role', user, role })
                          }
                        }}
                      >
                        {USER_ROLES.map((role) => (
                          <option key={role} value={role}>
                            {roleDisplayName(role)}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      {user.disabled ? (
                        userStatusLabel(user)
                      ) : (
                        <strong>{userStatusLabel(user)}</strong>
                      )}
                    </td>
                    <td>
                      {!isYou && (
                        <div className="dataset-actions">
                          {user.disabled ? (
                            <button
                              type="button"
                              className="secondary"
                              disabled={userBusy}
                              onClick={() => {
                                setUserError(null)
                                setUserConfirm({ action: 'enable', user })
                              }}
                            >
                              Enable
                            </button>
                          ) : (
                            <button
                              type="button"
                              className="secondary"
                              disabled={userBusy}
                              onClick={() => {
                                setUserError(null)
                                setUserConfirm({ action: 'disable', user })
                              }}
                            >
                              Disable
                            </button>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}

        {userConfirm !== null && (
          <div className="confirm-inline" role="alertdialog" aria-live="polite">
            {userConfirm.action === 'disable' && (
              <p>
                Disable {userConfirm.user.email}? They cannot sign in, and any
                open session ends at the next request.
              </p>
            )}
            {userConfirm.action === 'enable' && (
              <p>Enable {userConfirm.user.email}? They can sign in again.</p>
            )}
            {userConfirm.action === 'role' && (
              <p>
                Change {userConfirm.user.email} from{' '}
                {roleDisplayName(userConfirm.user.role)} to{' '}
                {roleDisplayName(userConfirm.role)}?
              </p>
            )}
            <div className="confirm-actions">
              <button
                type="button"
                className={
                  userConfirm.action === 'disable' ? 'danger-button' : 'primary-button'
                }
                disabled={userBusy}
                onClick={() => void runUserAction(userConfirm)}
              >
                {userBusy
                  ? 'Working…'
                  : userConfirm.action === 'role'
                    ? 'Change the role'
                    : userConfirm.action === 'disable'
                      ? 'Disable'
                      : 'Enable'}
              </button>
              <button
                type="button"
                className="secondary"
                disabled={userBusy}
                onClick={() => setUserConfirm(null)}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
        {userError !== null && (
          <p className="error-line" role="alert">
            {userError}
          </p>
        )}

        <form
          className="add-user"
          onSubmit={(event) => {
            event.preventDefault()
            void submitAddUser()
          }}
        >
          <h3>Add a user</h3>
          <div className="add-user-row">
            <label htmlFor="new-user-email">Email</label>
            <input
              id="new-user-email"
              type="email"
              autoComplete="off"
              value={newEmail}
              disabled={addBusy}
              onChange={(event) => setNewEmail(event.target.value)}
            />
            <label htmlFor="new-user-role">Role</label>
            <select
              id="new-user-role"
              value={newRole}
              disabled={addBusy}
              onChange={(event) => setNewRole(event.target.value as Role)}
            >
              {USER_ROLES.map((role) => (
                <option key={role} value={role}>
                  {roleDisplayName(role)}
                </option>
              ))}
            </select>
            <button
              type="submit"
              className="primary-button"
              disabled={addBusy || newEmail.trim() === ''}
            >
              {addBusy ? 'Adding…' : 'Add the user'}
            </button>
          </div>
        </form>
        {addErrors.length > 0 && (
          <div className="upload-errors" role="alert">
            <p>The user could not be added.</p>
            <ul>
              {addErrors.map((problem, index) => (
                <li key={index}>{problem}</li>
              ))}
            </ul>
          </div>
        )}
        {created !== null && (
          <div className="password-callout" role="status">
            <p>
              {created.email} was added as {roleDisplayName(created.role)}. The
              one time password is
            </p>
            <p className="password-value">
              <code>{created.one_time_password}</code>
            </p>
            <div className="confirm-actions">
              <button
                type="button"
                className="secondary"
                onClick={() => void copyPassword(created.one_time_password)}
              >
                {copied ? 'Copied' : 'Copy the password'}
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() => setCreated(null)}
              >
                Done
              </button>
            </div>
            <p className="hint">
              Shown once. Share it privately; it is not stored.
            </p>
          </div>
        )}
      </section>

      <section id={OFFICES_SECTION_ID} aria-labelledby="inst-offices-heading">
        <h2 id="inst-offices-heading">Offices</h2>
        <p className="hint">
          Where approved follow-ups are sent. Each office gets one mailbox.
          Nothing is sent until a staff member presses Send.
        </p>
        {officesState.kind === 'loading' && (
          <p className="status-line">Loading the office contacts…</p>
        )}
        {officesState.kind === 'error' && (
          <div className="state-panel error-panel" role="alert">
            <h3>The office contacts could not be loaded</h3>
            <p>{officesState.message}</p>
            <button type="button" onClick={() => void loadOffices()}>
              Retry
            </button>
          </div>
        )}
        {officesState.kind === 'ready' && (
          <form
            className="office-book"
            noValidate
            onSubmit={(event) => {
              event.preventDefault()
              void submitOffices()
            }}
          >
            {officeRows.length === 0 ? (
              <p className="status-line office-empty">
                No office has a mailbox yet. Add the first one below.
              </p>
            ) : (
              <table className="office-table dataset-table office-book-table">
                <caption>Office mailboxes for {institutionName}</caption>
                <thead>
                  <tr>
                    <th scope="col">Office</th>
                    <th scope="col">Mailbox</th>
                    <th scope="col">
                      <span className="visually-hidden">Remove</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {officeRows.map((row, index) => {
                    const errors = officeErrors[row.key] ?? {}
                    const isNew = row.key.startsWith('new:')
                    const label = row.office.trim() !== '' ? row.office.trim() : `new office ${index + 1}`
                    const officeErrorId = `office-name-error-${row.key}`
                    const emailErrorId = `office-email-error-${row.key}`
                    const busy = officeSave.kind === 'saving'
                    return (
                      <tr key={row.key}>
                        <td data-label="Office">
                          {isNew ? (
                            <>
                              <input
                                id={`office-name-${row.key}`}
                                type="text"
                                autoComplete="off"
                                aria-label={`Office name, row ${index + 1}`}
                                aria-invalid={errors.office !== undefined}
                                aria-describedby={
                                  errors.office !== undefined ? officeErrorId : undefined
                                }
                                placeholder="Office name"
                                value={row.office}
                                disabled={busy}
                                onChange={(event) =>
                                  editOfficeRow(row.key, 'office', event.target.value)
                                }
                              />
                              {errors.office !== undefined && (
                                <p className="field-error" id={officeErrorId}>
                                  {errors.office}
                                </p>
                              )}
                            </>
                          ) : (
                            <>
                              {row.office}
                              {row.known && row.email.trim() === '' && (
                                <span className="dataset-flag office-gap">
                                  No mailbox yet. Approved follow-ups go here.
                                </span>
                              )}
                              {errors.office !== undefined && (
                                <p className="field-error" id={officeErrorId}>
                                  {errors.office}
                                </p>
                              )}
                            </>
                          )}
                        </td>
                        <td data-label="Mailbox">
                          <input
                            type="email"
                            inputMode="email"
                            autoComplete="off"
                            aria-label={`Mailbox for ${label}`}
                            aria-invalid={errors.email !== undefined}
                            aria-describedby={
                              errors.email !== undefined ? emailErrorId : undefined
                            }
                            placeholder="office@example.edu"
                            value={row.email}
                            disabled={busy}
                            onChange={(event) =>
                              editOfficeRow(row.key, 'email', event.target.value)
                            }
                          />
                          {errors.email !== undefined && (
                            <p className="field-error" id={emailErrorId}>
                              {errors.email}
                            </p>
                          )}
                        </td>
                        <td className="office-row-actions">
                          {(!row.known || row.email !== '') && (
                            <button
                              type="button"
                              className="secondary"
                              aria-label={`Remove the mailbox for ${label}`}
                              disabled={busy}
                              onClick={() => removeOfficeRow(row)}
                            >
                              Remove
                            </button>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
            <div className="office-book-actions">
              <button
                type="button"
                className="secondary"
                disabled={officeSave.kind === 'saving'}
                onClick={addOfficeRow}
              >
                Add an office
              </button>
              <button
                type="submit"
                className="primary-button"
                disabled={officeSave.kind === 'saving'}
                aria-busy={officeSave.kind === 'saving'}
              >
                {officeSave.kind === 'saving' ? (
                  <>
                    <span className="save-spinner" aria-hidden="true" />
                    Saving…
                  </>
                ) : (
                  'Save the office contacts'
                )}
              </button>
            </div>
            {officeSave.kind === 'invalid' && (
              <p className="error-line" role="alert">
                Nothing was saved. Fix the marked fields, then save again.
              </p>
            )}
            {officeSave.kind === 'saved' && (
              <p className="office-saved" role="status">
                Saved. {officeSave.count === 1 ? '1 office has' : `${officeSave.count} offices have`}{' '}
                a mailbox. The audit log records the change.
              </p>
            )}
            {officeSave.kind === 'failed' && (
              <div className="upload-errors" role="alert">
                <p>The office contacts were not saved: {officeSave.message}</p>
                {officeSave.errors.length > 0 && (
                  <ul>
                    {officeSave.errors.map((problem, index) => (
                      <li key={index}>{problem}</li>
                    ))}
                  </ul>
                )}
                <p className="hint">
                  The address book is unchanged. Check the entries and press
                  Save again. If it keeps failing, reload the page and try
                  once more.
                </p>
              </div>
            )}
          </form>
        )}
      </section>

      <CounselingAuthorizationSection onChanged={onDataChanged} />

      <section aria-labelledby="inst-datasets">
        <h2 id="inst-datasets">Datasets</h2>
        <p className="hint">
          The active dataset is the one the briefing is computed from.
          Uploading adds a dataset. Activating one recomputes the briefing.
        </p>
        {datasetsState.kind === 'loading' && (
          <p className="status-line">Loading the datasets…</p>
        )}
        {datasetsState.kind === 'error' && (
          <div className="state-panel error-panel" role="alert">
            <h3>The datasets could not be loaded</h3>
            <p>{datasetsState.message}</p>
            <button type="button" onClick={() => void load()}>
              Retry
            </button>
          </div>
        )}
        {datasetsState.kind === 'ready' && (
          <table className="office-table dataset-table">
            <caption>
              Datasets uploaded for {institutionName}
            </caption>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Uploaded</th>
                <th scope="col">Rows</th>
                <th scope="col">Status</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {datasetsState.data.map((dataset) => {
                const fictional =
                  activeDataset !== null &&
                  dataset.id === activeDataset.id &&
                  activeDataset.fictional
                return (
                  <tr key={dataset.id}>
                    <td>
                      {dataset.name}
                      {fictional && (
                        <span className="dataset-flag">Fictional demonstration</span>
                      )}
                    </td>
                    <td>
                      {formatTimestamp(dataset.uploaded_at)}
                      <span className="dataset-flag">by {dataset.uploaded_by}</span>
                    </td>
                    <td>{rowCountLabel(dataset.row_counts)}</td>
                    <td>{dataset.is_active ? <strong>Active</strong> : 'Inactive'}</td>
                    <td>
                      {!dataset.is_active && (
                        <div className="dataset-actions">
                          <button
                            type="button"
                            className="secondary"
                            disabled={actionBusy}
                            onClick={() => {
                              setActionError(null)
                              setConfirm({ action: 'activate', dataset })
                            }}
                          >
                            Activate
                          </button>
                          <button
                            type="button"
                            className="secondary"
                            disabled={actionBusy}
                            onClick={() => {
                              setActionError(null)
                              setConfirm({ action: 'delete', dataset })
                            }}
                          >
                            Delete
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}

        {confirm !== null && (
          <div className="confirm-inline" role="alertdialog" aria-live="polite">
            {confirm.action === 'activate' ? (
              <p>
                Activate '{confirm.dataset.name}'? The briefing will recompute.
              </p>
            ) : (
              <p>
                Delete '{confirm.dataset.name}'? It is kept for 30 days, then
                purged. The audit log keeps its upload and deletion events.
              </p>
            )}
            <div className="confirm-actions">
              <button
                type="button"
                className={
                  confirm.action === 'delete' ? 'danger-button' : 'primary-button'
                }
                disabled={actionBusy}
                onClick={() => void runAction(confirm.action, confirm.dataset)}
              >
                {actionBusy
                  ? 'Working…'
                  : confirm.action === 'activate'
                    ? 'Activate'
                    : 'Delete'}
              </button>
              <button
                type="button"
                className="secondary"
                disabled={actionBusy}
                onClick={() => setConfirm(null)}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
        {actionError !== null && (
          <p className="error-line" role="alert">
            {actionError}
          </p>
        )}
      </section>

      <section aria-labelledby="inst-upload">
        <h2 id="inst-upload">Upload a dataset</h2>
        <p className="hint">
          A JSON document in the SCHEMA.md shape, up to 20 MB. The document is
          validated before anything is stored: fields outside the schema,
          obvious personal columns, and student ids that are not pseudonymous
          are rejected, with every problem listed.
        </p>
        <div className="upload-row">
          <input
            type="file"
            accept=".json,application/json"
            aria-label="Dataset file, JSON only"
            disabled={uploadState.kind === 'uploading'}
            onChange={(event) => pickFile(event.target.files)}
          />
          <button
            type="button"
            className="primary-button"
            disabled={file === null || uploadState.kind === 'uploading'}
            onClick={() => void upload()}
          >
            {uploadState.kind === 'uploading' ? 'Uploading…' : 'Upload'}
          </button>
        </div>
        {file !== null && (
          <p className="hint">
            {file.name} · {formatBytes(file.size)}
          </p>
        )}
        {fileError !== null && (
          <p className="error-line" role="alert">
            {fileError}
          </p>
        )}
        {uploadState.kind === 'done' && (
          <div className="upload-result" role="status">
            <p>
              Uploaded '{uploadState.result.dataset.name}':{' '}
              {rowCountLabel(uploadState.result.validation.row_counts)}. It is
              inactive until you activate it.
            </p>
            {uploadState.result.validation.fictional && (
              <p className="hint">The document is marked fictional.</p>
            )}
            {uploadState.result.validation.counseling !== 'absent' && (
              <p className="hint">
                Counseling fields are present in this file. They are stored
                with the dataset, and the cabinet always refuses them.
              </p>
            )}
          </div>
        )}
        {uploadState.kind === 'failed' && (
          <div className="upload-errors" role="alert">
            <p>The dataset failed validation. Nothing was stored.</p>
            <ul>
              {uploadState.errors.map((problem, index) => (
                <li key={index}>{problem}</li>
              ))}
            </ul>
          </div>
        )}
      </section>
    </div>
  )
}
