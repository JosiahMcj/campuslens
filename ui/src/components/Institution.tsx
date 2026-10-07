import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'

import { ALL_ROLES, roleDisplayName, type Role } from '../auth'
import { ConnectionsSection, CONNECTIONS_SECTION_ID, SectionLoading } from './ConnectionsSection'
import { CounselingAuthorizationSection, COUNSELING_SECTION_ID } from './CounselingAuthorization'
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
import { friendlyError, friendlyLoadError } from '../errors'
import {
  draftRowsFrom,
  fetchActionOffices,
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
  newUserEmailError,
  setUserDisabled,
  setUserRole,
  userStatusLabel,
  type CreatedUser,
  type UserRow,
} from '../users'
import './Institution.css'

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
  | { kind: 'refused'; errors: string[] }
  | { kind: 'failed'; message: string }

type OfficeSaveState =
  | { kind: 'idle' }
  | { kind: 'invalid' }
  | { kind: 'saving' }
  | { kind: 'saved'; count: number }
  | { kind: 'refused'; errors: string[] }
  | { kind: 'failed'; message: string }

const USER_ROLES: readonly Role[] = ALL_ROLES

/** The section anchor the decision panel links to. */
export const OFFICES_SECTION_ID = 'inst-offices'
/** The other section anchors, for the section list at the top. */
export const USERS_SECTION_ID = 'inst-users'
export const DATA_SECTION_ID = 'inst-data'

const SECTIONS = [
  { id: USERS_SECTION_ID, label: 'Users' },
  { id: OFFICES_SECTION_ID, label: 'Offices' },
  { id: COUNSELING_SECTION_ID, label: 'Counseling' },
  { id: DATA_SECTION_ID, label: 'Data' },
  { id: CONNECTIONS_SECTION_ID, label: 'Connections' },
] as const

type SectionId = (typeof SECTIONS)[number]['id']

/** The section an address names (/institution#inst-offices), else Users. */
function sectionFromHash(hash: string): SectionId {
  const id = hash.replace(/^#/, '')
  return SECTIONS.find((section) => section.id === id)?.id ?? USERS_SECTION_ID
}

/** A button's working label: a spinner and the "…ing" words. */
function Working({ label }: { label: string }) {
  return (
    <span className="inst-busy">
      <span className="save-spinner" aria-hidden="true" />
      {label}
    </span>
  )
}

/**
 * An inline confirmation's focus: Cancel takes focus when it opens, and
 * when it closes (and nothing is busy) focus goes back to what opened it,
 * or to the fallback when that element is gone (a Disable that became
 * Enable).
 */
function useConfirmFocus(
  open: boolean,
  busy: boolean,
  cancelRef: RefObject<HTMLButtonElement | null>,
  triggerRef: RefObject<HTMLElement | null>,
  fallbackRef: RefObject<HTMLElement | null>,
) {
  const wasOpen = useRef(false)
  useEffect(() => {
    if (open) {
      if (!wasOpen.current) cancelRef.current?.focus()
      wasOpen.current = true
      return
    }
    if (wasOpen.current && !busy) {
      wasOpen.current = false
      const trigger = triggerRef.current
      ;(trigger !== null && trigger.isConnected ? trigger : fallbackRef.current)?.focus()
    }
  }, [open, busy, cancelRef, triggerRef, fallbackRef])
}

/**
 * The Institution area (admin only), one section at a time, chosen from a
 * short section list (the address keeps the section, so a link such as
 * /institution#inst-offices opens Offices, and a reload stays put): Users (roles and status, the one-time password of a
 * newly added user shown once), Offices (the address book approved
 * follow-ups are sent to, edited inline and saved whole), Counseling (the
 * counseling figure's recorded authorization) and Data (the uploads with
 * their record counts, activate / delete, and the upload form). Every
 * change that matters sits behind an inline confirmation, never a modal;
 * every failure says what happened in plain words. Backups are
 * deliberately absent: they are an operator task.
 */
export function Institution({
  institutionName,
  activeDataset,
  currentUserEmail,
  onDataChanged,
}: InstitutionProps) {
  const [activeSection, setActiveSection] = useState<SectionId>(() =>
    sectionFromHash(window.location.hash),
  )
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
  const [emailError, setEmailError] = useState<string | null>(null)
  const [addError, setAddError] = useState<string | null>(null)
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
  const focusAfterRemove = useRef<number | null>(null)
  const addOfficeRef = useRef<HTMLButtonElement>(null)
  const officeTableRef = useRef<HTMLTableElement>(null)

  const usersHeadingRef = useRef<HTMLHeadingElement>(null)
  const dataHeadingRef = useRef<HTMLHeadingElement>(null)
  const userCancelRef = useRef<HTMLButtonElement>(null)
  const userTriggerRef = useRef<HTMLElement | null>(null)
  const datasetCancelRef = useRef<HTMLButtonElement>(null)
  const datasetTriggerRef = useRef<HTMLElement | null>(null)

  useConfirmFocus(userConfirm !== null, userBusy, userCancelRef, userTriggerRef, usersHeadingRef)
  useConfirmFocus(confirm !== null, actionBusy, datasetCancelRef, datasetTriggerRef, dataHeadingRef)

  const loadOffices = useCallback(async () => {
    setOfficesState({ kind: 'loading' })
    try {
      const [book, decisionOffices, actionOffices] = await Promise.all([
        fetchOffices(),
        fetchDecisionOffices(),
        fetchActionOffices(),
      ])
      const routed = [...new Set([...decisionOffices, ...actionOffices])]
      setDecisionOffices(routed)
      setOfficeRows(draftRowsFrom(book, routed))
      setOfficeErrors({})
      setOfficesState({ kind: 'ready', data: null })
    } catch (error) {
      setOfficesState({ kind: 'error', message: friendlyLoadError(error) })
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

  const removeOfficeRow = useCallback((row: OfficeDraftRow, index: number) => {
    // An office a decision routes to stays listed, without its mailbox, so
    // the gap stays visible; any other row leaves the book on the next Save.
    focusAfterRemove.current = index
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
    if (focusKey.current !== null) {
      const index = officeRows.findIndex((row) => row.key === focusKey.current)
      const input = document.getElementById(`office-name-${index}`)
      focusKey.current = null
      input?.focus()
      return
    }
    if (focusAfterRemove.current !== null) {
      // The Remove button that had focus is gone: focus the next row's
      // Remove (or the one before it), else Add an office, never the page.
      const removed = focusAfterRemove.current
      focusAfterRemove.current = null
      const buttons = Array.from(
        officeTableRef.current?.querySelectorAll<HTMLButtonElement>('button[data-office-remove]') ??
          [],
      )
      const next =
        buttons.find((button) => Number(button.dataset.officeRemove) >= removed) ??
        buttons[buttons.length - 1]
      ;(next ?? addOfficeRef.current)?.focus()
    }
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
    try {
      const result = await saveOffices(officesForSave(officeRows))
      if (result.ok) {
        setOfficeRows(draftRowsFrom(result.offices, decisionOffices))
        setOfficeSave({ kind: 'saved', count: result.offices.length })
      } else {
        setOfficeSave({ kind: 'refused', errors: result.errors })
      }
    } catch (error) {
      setOfficeSave({ kind: 'failed', message: friendlyError(error, 'The address book') })
    }
  }, [officeSave.kind, officeRows, decisionOffices])

  const loadUsers = useCallback(async () => {
    try {
      setUsersState({ kind: 'ready', data: await fetchUsers() })
    } catch (error) {
      setUsersState({ kind: 'error', message: friendlyLoadError(error) })
    }
  }, [])

  const load = useCallback(async () => {
    try {
      setDatasetsState({ kind: 'ready', data: await fetchDatasets() })
    } catch (error) {
      setDatasetsState({ kind: 'error', message: friendlyLoadError(error) })
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
    void loadUsers()
    void loadOffices()
  }, [load, loadUsers, loadOffices])

  // The address can change the section too (a link to
  // /institution#inst-offices while the page is open).
  useEffect(() => {
    const follow = () => setActiveSection(sectionFromHash(window.location.hash))
    window.addEventListener('hashchange', follow)
    window.addEventListener('popstate', follow)
    return () => {
      window.removeEventListener('hashchange', follow)
      window.removeEventListener('popstate', follow)
    }
  }, [])

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
      setFileError(
        'That file can’t be used. Choose the student-records export your records office gave you.',
      )
      return
    }
    void picked.arrayBuffer().then(
      (bytes) => setFile({ name: picked.name, size: picked.size, bytes }),
      () => setFileError('That file could not be read. Choose it again.'),
    )
  }, [])

  const upload = useCallback(async () => {
    if (uploadState.kind === 'uploading') return
    if (file === null) {
      setFileError('Choose a file first.')
      return
    }
    setUploadState({ kind: 'uploading' })
    try {
      const result = await uploadDataset(file.bytes)
      if (result.ok) {
        setFile(null)
        setUploadState({ kind: 'done', result })
        await load()
      } else {
        setUploadState({ kind: 'refused', errors: result.errors })
      }
    } catch (error) {
      setUploadState({ kind: 'failed', message: friendlyError(error, 'The upload') })
    }
  }, [file, uploadState.kind, load])

  const runAction = useCallback(
    async (action: 'activate' | 'delete', dataset: DatasetRow) => {
      setActionBusy(true)
      setActionError(null)
      try {
        if (action === 'activate') await activateDataset(dataset.id)
        else await deleteDataset(dataset.id)
        await load()
        if (action === 'activate') onDataChanged()
      } catch (error) {
        setActionError(
          friendlyError(error, action === 'activate' ? 'The activation' : 'The deletion'),
        )
      } finally {
        setActionBusy(false)
        setConfirm(null)
      }
    },
    [load, onDataChanged],
  )

  const submitAddUser = useCallback(async () => {
    if (addBusy) return
    const problem = newUserEmailError(newEmail)
    setEmailError(problem)
    setAddError(null)
    if (problem !== null) {
      document.getElementById('new-user-email')?.focus()
      return
    }
    setAddBusy(true)
    setCreated(null)
    try {
      const user = await addUser(newEmail.trim(), newRole)
      setNewEmail('')
      setNewRole('staff')
      setCreated(user)
      setCopied(false)
      await loadUsers()
    } catch (error) {
      const message = friendlyError(error, 'The new user')
      // A refusal about the address (it already has an account) belongs
      // under the field; anything else (offline, busy) under the button.
      const status = (error as { status?: unknown }).status
      if (typeof status === 'number' && status >= 400 && status < 500 && status !== 429) {
        setEmailError(message)
      } else {
        setAddError(message)
      }
    } finally {
      setAddBusy(false)
    }
  }, [addBusy, newEmail, newRole, loadUsers])

  const runUserAction = useCallback(
    async (pending: NonNullable<UserConfirmState>) => {
      setUserBusy(true)
      setUserError(null)
      try {
        if (pending.action === 'role') await setUserRole(pending.user.id, pending.role)
        else await setUserDisabled(pending.user.id, pending.action === 'disable')
        await loadUsers()
      } catch (error) {
        setUserError(
          friendlyError(error, pending.action === 'role' ? 'The role change' : 'The change'),
        )
      } finally {
        setUserBusy(false)
        setUserConfirm(null)
      }
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

  // Opening a section shows it alone and moves focus to its heading. The
  // address records it without a new history entry, so Back still leaves
  // the page instead of walking through the sections.
  const pendingFocus = useRef<SectionId | null>(null)
  const goToSection = useCallback((id: SectionId) => {
    pendingFocus.current = id
    setActiveSection(id)
    if (window.location.hash !== `#${id}`) {
      window.history.replaceState(window.history.state, '', `#${id}`)
    }
  }, [])
  useEffect(() => {
    if (pendingFocus.current !== activeSection) return
    pendingFocus.current = null
    document.getElementById(activeSection)?.querySelector<HTMLElement>('h2')?.focus()
  }, [activeSection])

  const uploading = uploadState.kind === 'uploading'
  const emailErrorId = 'new-user-email-error'

  return (
    <div className="institution">
      <nav className="inst-toc" aria-label="Institution settings sections">
        <ul>
          {SECTIONS.map((section) => (
            <li key={section.id}>
              <a
                href={`#${section.id}`}
                aria-current={section.id === activeSection ? 'true' : undefined}
                onClick={(event) => {
                  event.preventDefault()
                  goToSection(section.id)
                }}
              >
                {section.label}
              </a>
            </li>
          ))}
        </ul>
      </nav>

      <section
        id={USERS_SECTION_ID}
        className="inst-card"
        aria-labelledby="inst-users-heading"
        hidden={activeSection !== USERS_SECTION_ID}
      >
        <h2 id="inst-users-heading" ref={usersHeadingRef} tabIndex={-1}>
          Users
        </h2>
        <p className="hint">
          Everyone who can sign in to {institutionName}. Adding a user creates
          a sign-in with a one-time password. Disabling ends access at the
          next request. The audit log records every change.
        </p>
        {usersState.kind === 'loading' && <SectionLoading label="Loading the users…" />}
        {usersState.kind === 'error' && (
          <div className="state-error" role="alert">
            <h3>We couldn’t load the users</h3>
            <p>{usersState.message}</p>
            <button type="button" className="btn-secondary" onClick={() => void loadUsers()}>
              Retry
            </button>
          </div>
        )}
        {usersState.kind === 'ready' && usersState.data.length === 0 && (
          <p className="state-empty">No one can sign in yet. Add the first user below.</p>
        )}
        {usersState.kind === 'ready' && usersState.data.length > 0 && (
          <table className="stack-table office-table dataset-table user-table">
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
                    <td data-label="Email">
                      {user.email}
                      {isYou && <span className="dataset-flag">you</span>}
                    </td>
                    <td data-label="Role">
                      <select
                        aria-label={`Role for ${user.email}`}
                        aria-describedby={isYou ? 'own-role-hint' : undefined}
                        value={user.role}
                        disabled={userBusy || isYou}
                        onChange={(event) => {
                          const role = event.target.value as Role
                          if (role !== user.role) {
                            userTriggerRef.current = event.currentTarget
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
                      {isYou && (
                        <span className="dataset-flag" id="own-role-hint">
                          You can’t change your own role
                        </span>
                      )}
                    </td>
                    <td data-label="Status">
                      {user.disabled ? (
                        userStatusLabel(user)
                      ) : (
                        <strong>{userStatusLabel(user)}</strong>
                      )}
                    </td>
                    <td data-label="Actions">
                      {!isYou && (
                        <div className="dataset-actions">
                          {user.disabled ? (
                            <button
                              type="button"
                              className="btn-secondary"
                              disabled={userBusy}
                              onClick={(event) => {
                                userTriggerRef.current = event.currentTarget
                                setUserError(null)
                                setUserConfirm({ action: 'enable', user })
                              }}
                            >
                              Enable
                            </button>
                          ) : (
                            <button
                              type="button"
                              className="btn-secondary"
                              disabled={userBusy}
                              onClick={(event) => {
                                userTriggerRef.current = event.currentTarget
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
          <div
            className="confirm-inline"
            role="alertdialog"
            aria-labelledby="user-confirm-question"
            onKeyDown={(event) => {
              if (event.key === 'Escape' && !userBusy) {
                event.stopPropagation()
                setUserConfirm(null)
              }
            }}
          >
            <p id="user-confirm-question">
              {userConfirm.action === 'disable' &&
                `Disable ${userConfirm.user.email}? They cannot sign in, and any open session ends at the next request.`}
              {userConfirm.action === 'enable' &&
                `Enable ${userConfirm.user.email}? They can sign in again.`}
              {userConfirm.action === 'role' &&
                `Change ${userConfirm.user.email} from ${roleDisplayName(userConfirm.user.role)} to ${roleDisplayName(userConfirm.role)}?`}
            </p>
            <div className="confirm-actions">
              <button
                type="button"
                className={userConfirm.action === 'disable' ? 'btn-danger' : 'btn-primary'}
                disabled={userBusy}
                aria-busy={userBusy}
                onClick={() => void runUserAction(userConfirm)}
              >
                {userBusy ? (
                  <Working label="Saving…" />
                ) : userConfirm.action === 'role' ? (
                  'Change the role'
                ) : userConfirm.action === 'disable' ? (
                  'Disable'
                ) : (
                  'Enable'
                )}
              </button>
              <button
                type="button"
                className="btn-secondary"
                ref={userCancelRef}
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
          className="inst-form"
          noValidate
          onSubmit={(event) => {
            event.preventDefault()
            void submitAddUser()
          }}
        >
          <h3>Add a user</h3>
          <div className="inst-fields">
            <div className="inst-field">
              <label htmlFor="new-user-email">Email</label>
              <input
                id="new-user-email"
                type="email"
                inputMode="email"
                autoComplete="off"
                placeholder="e.g. name@example.edu"
                value={newEmail}
                disabled={addBusy}
                aria-invalid={emailError !== null}
                aria-describedby={emailError !== null ? emailErrorId : undefined}
                onChange={(event) => {
                  setNewEmail(event.target.value)
                  setEmailError(null)
                }}
              />
              {emailError !== null && (
                <p className="field-error" id={emailErrorId}>
                  {emailError}
                </p>
              )}
            </div>
            <div className="inst-field inst-field-narrow">
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
            </div>
          </div>
          <div className="inst-actions">
            <button type="submit" className="btn-primary" disabled={addBusy} aria-busy={addBusy}>
              {addBusy ? <Working label="Adding…" /> : 'Add the user'}
            </button>
          </div>
          {addError !== null && (
            <p className="error-line inst-under-button" role="alert">
              {addError}
            </p>
          )}
        </form>
        {created !== null && (
          <div className="password-callout" role="status">
            <p>
              {created.email} was added as {roleDisplayName(created.role)}. The
              one-time password is
            </p>
            <p className="password-value">
              <code>{created.one_time_password}</code>
            </p>
            <div className="confirm-actions">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => void copyPassword(created.one_time_password)}
              >
                {copied ? 'Copied' : 'Copy the password'}
              </button>
              <button type="button" className="btn-secondary" onClick={() => setCreated(null)}>
                Done
              </button>
            </div>
            <p className="hint">Shown once. Share it privately; it is not stored.</p>
          </div>
        )}
      </section>

      <section
        id={OFFICES_SECTION_ID}
        className="inst-card"
        aria-labelledby="inst-offices-heading"
        hidden={activeSection !== OFFICES_SECTION_ID}
      >
        <h2 id="inst-offices-heading" tabIndex={-1}>
          Offices
        </h2>
        <p className="hint">
          Where approved follow-ups and staff actions are sent. Each office gets
          one mailbox. Nothing is sent until a staff member presses Send.
        </p>
        {officesState.kind === 'loading' && <SectionLoading label="Loading the office contacts…" />}
        {officesState.kind === 'error' && (
          <div className="state-error" role="alert">
            <h3>We couldn’t load the office contacts</h3>
            <p>{officesState.message}</p>
            <button type="button" className="btn-secondary" onClick={() => void loadOffices()}>
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
              <p className="state-empty office-empty">
                No office has a mailbox yet. Add the first one below.
              </p>
            ) : (
              <table
                ref={officeTableRef}
                className="stack-table office-table dataset-table office-book-table"
              >
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
                    const label =
                      row.office.trim() !== '' ? row.office.trim() : `new office ${index + 1}`
                    // Ids from the row index: row keys carry office names with
                    // spaces, and aria-describedby splits on whitespace.
                    const officeErrorId = `office-name-error-${index}`
                    const emailErrorIdForRow = `office-email-error-${index}`
                    const busy = officeSave.kind === 'saving'
                    return (
                      <tr key={row.key}>
                        <td data-label="Office">
                          {isNew ? (
                            <>
                              <input
                                id={`office-name-${index}`}
                                type="text"
                                autoComplete="off"
                                aria-label={`Office name, row ${index + 1}`}
                                aria-invalid={errors.office !== undefined}
                                aria-describedby={
                                  errors.office !== undefined ? officeErrorId : undefined
                                }
                                placeholder="e.g. Financial Aid"
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
                                  No mailbox yet. Add one so approved follow-ups can be sent.
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
                              errors.email !== undefined ? emailErrorIdForRow : undefined
                            }
                            placeholder="e.g. office@example.edu"
                            value={row.email}
                            disabled={busy}
                            onChange={(event) =>
                              editOfficeRow(row.key, 'email', event.target.value)
                            }
                          />
                          {errors.email !== undefined && (
                            <p className="field-error" id={emailErrorIdForRow}>
                              {errors.email}
                            </p>
                          )}
                        </td>
                        <td className="office-row-actions" data-label="Remove">
                          {(!row.known || row.email !== '') && (
                            <button
                              type="button"
                              className="btn-secondary"
                              data-office-remove={index}
                              aria-label={`Remove the mailbox for ${label}`}
                              disabled={busy}
                              onClick={() => removeOfficeRow(row, index)}
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
            <div className="inst-actions">
              <button
                type="button"
                className="btn-secondary"
                ref={addOfficeRef}
                disabled={officeSave.kind === 'saving'}
                onClick={addOfficeRow}
              >
                Add an office
              </button>
              <button
                type="submit"
                className="btn-primary"
                disabled={officeSave.kind === 'saving'}
                aria-busy={officeSave.kind === 'saving'}
              >
                {officeSave.kind === 'saving' ? (
                  <Working label="Saving…" />
                ) : (
                  'Save the office contacts'
                )}
              </button>
            </div>
            {officeSave.kind === 'invalid' && (
              <p className="error-line inst-under-button" role="alert">
                Nothing was saved. Fix the marked fields, then save again.
              </p>
            )}
            {officeSave.kind === 'saved' && (
              <p className="office-saved" role="status">
                Saved.{' '}
                {officeSave.count === 1 ? '1 office has' : `${officeSave.count} offices have`}{' '}
                a mailbox. The audit log records the change.
              </p>
            )}
            {officeSave.kind === 'failed' && (
              <p className="error-line inst-under-button" role="alert">
                {officeSave.message}
              </p>
            )}
            {officeSave.kind === 'refused' && (
              <div className="upload-errors" role="alert">
                <p>
                  The office contacts were not saved. The address book is unchanged;
                  check the entries, then save again.
                </p>
                {officeSave.errors.length > 0 && (
                  <details className="fold">
                    <summary>Technical detail</summary>
                    <ul>
                      {officeSave.errors.map((problem, index) => (
                        <li key={index}>{problem}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            )}
          </form>
        )}
      </section>

      <div className="inst-section" hidden={activeSection !== COUNSELING_SECTION_ID}>
        <CounselingAuthorizationSection onChanged={onDataChanged} />
      </div>

      <section
        id={DATA_SECTION_ID}
        className="inst-card"
        aria-labelledby="inst-data-heading"
        hidden={activeSection !== DATA_SECTION_ID}
      >
        <h2 id="inst-data-heading" ref={dataHeadingRef} tabIndex={-1}>
          Data
        </h2>
        <p className="hint">
          The briefing is computed from the active upload. Activating another
          one recomputes the briefing.
        </p>
        {datasetsState.kind === 'loading' && <SectionLoading label="Loading the uploads…" />}
        {datasetsState.kind === 'error' && (
          <div className="state-error" role="alert">
            <h3>We couldn’t load the uploads</h3>
            <p>{datasetsState.message}</p>
            <button type="button" className="btn-secondary" onClick={() => void load()}>
              Retry
            </button>
          </div>
        )}
        {datasetsState.kind === 'ready' && datasetsState.data.length === 0 && (
          <p className="state-empty">
            Nothing has been uploaded yet. Upload your student-records export below.
          </p>
        )}
        {datasetsState.kind === 'ready' && datasetsState.data.length > 0 && (
          <table className="stack-table office-table dataset-table">
            <caption>Uploads for {institutionName}</caption>
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Uploaded</th>
                <th scope="col">Records</th>
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
                    <td data-label="Name">
                      {dataset.name}
                      {fictional && (
                        <span className="dataset-flag">Fictional demonstration</span>
                      )}
                    </td>
                    <td data-label="Uploaded">
                      {formatTimestamp(dataset.uploaded_at)}
                      <span className="dataset-flag">by {dataset.uploaded_by}</span>
                    </td>
                    <td data-label="Records">{rowCountLabel(dataset.row_counts)}</td>
                    <td data-label="Status">
                      {dataset.is_active ? <strong>Active</strong> : 'Inactive'}
                    </td>
                    <td data-label="Actions">
                      {!dataset.is_active && (
                        <div className="dataset-actions">
                          <button
                            type="button"
                            className="btn-secondary"
                            disabled={actionBusy}
                            onClick={(event) => {
                              datasetTriggerRef.current = event.currentTarget
                              setActionError(null)
                              setConfirm({ action: 'activate', dataset })
                            }}
                          >
                            Activate
                          </button>
                          <button
                            type="button"
                            className="btn-secondary"
                            disabled={actionBusy}
                            onClick={(event) => {
                              datasetTriggerRef.current = event.currentTarget
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
          <div
            className="confirm-inline"
            role="alertdialog"
            aria-labelledby="dataset-confirm-question"
            onKeyDown={(event) => {
              if (event.key === 'Escape' && !actionBusy) {
                event.stopPropagation()
                setConfirm(null)
              }
            }}
          >
            <p id="dataset-confirm-question">
              {confirm.action === 'activate'
                ? `Activate '${confirm.dataset.name}'? The briefing will recompute.`
                : `Delete '${confirm.dataset.name}'? It is kept for 30 days, then purged. The audit log keeps its upload and deletion events.`}
            </p>
            <div className="confirm-actions">
              <button
                type="button"
                className={confirm.action === 'delete' ? 'btn-danger' : 'btn-primary'}
                disabled={actionBusy}
                aria-busy={actionBusy}
                onClick={() => void runAction(confirm.action, confirm.dataset)}
              >
                {actionBusy ? (
                  <Working label={confirm.action === 'activate' ? 'Activating…' : 'Deleting…'} />
                ) : confirm.action === 'activate' ? (
                  'Activate'
                ) : (
                  'Delete'
                )}
              </button>
              <button
                type="button"
                className="btn-secondary"
                ref={datasetCancelRef}
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

        <form
          className="inst-form"
          noValidate
          onSubmit={(event) => {
            event.preventDefault()
            void upload()
          }}
        >
          <h3>Upload new data</h3>
          <p className="hint">
            Upload your student-records export (up to 20 MB). We check it
            before saving and list anything we can&rsquo;t accept.
          </p>
          <div className="inst-field">
            <span className="inst-label" id="upload-file-label">
              Student records file
            </span>
            <div className="inst-file-row">
              <label
                className="btn-secondary inst-file-pick"
                aria-disabled={uploading || undefined}
              >
                Choose a file
                <input
                  type="file"
                  className="visually-hidden"
                  accept=".json,application/json"
                  aria-labelledby="upload-file-label"
                  aria-invalid={fileError !== null}
                  aria-describedby={fileError !== null ? 'upload-file-error' : 'upload-file-name'}
                  disabled={uploading}
                  onChange={(event) => pickFile(event.target.files)}
                />
              </label>
              <span className="inst-file-name" id="upload-file-name">
                {file !== null ? `${file.name} · ${formatBytes(file.size)}` : 'No file chosen'}
              </span>
            </div>
            {fileError !== null && (
              <p className="field-error" id="upload-file-error" role="alert">
                {fileError}
              </p>
            )}
          </div>
          <div className="inst-actions">
            {/* Secondary until a file is chosen: uploading is the less common
                job, so it stays quiet until it is ready to go. */}
            <button
              type="submit"
              className={file !== null ? 'btn-primary' : 'btn-secondary'}
              disabled={uploading}
              aria-busy={uploading}
            >
              {uploading ? <Working label="Uploading…" /> : 'Upload'}
            </button>
          </div>
          {uploadState.kind === 'failed' && (
            <p className="error-line inst-under-button" role="alert">
              {uploadState.message}
            </p>
          )}
        </form>
        {uploadState.kind === 'done' && (
          <div className="upload-result" role="status">
            <p>
              Uploaded &lsquo;{uploadState.result.dataset.name}&rsquo;:{' '}
              {rowCountLabel(uploadState.result.validation.row_counts)}. It is not
              used until you activate it.
            </p>
            {uploadState.result.validation.fictional && (
              <p className="hint">The file is marked as fictional demonstration data.</p>
            )}
            {uploadState.result.validation.counseling !== 'absent' && (
              <p className="hint">
                The file includes counseling details. They are stored with the
                upload, and CampusLens always refuses them.
              </p>
            )}
          </div>
        )}
        {uploadState.kind === 'refused' && (
          <div className="upload-errors" role="alert">
            <p>We couldn&rsquo;t accept this file. Nothing was saved.</p>
            <details className="fold">
              <summary>
                Technical detail (
                {uploadState.errors.length === 1
                  ? '1 problem to fix in the export'
                  : `${uploadState.errors.length} problems to fix in the export`}
                )
              </summary>
              <ul>
                {uploadState.errors.map((problem, index) => (
                  <li key={index}>{problem}</li>
                ))}
              </ul>
            </details>
          </div>
        )}
      </section>

      <div className="inst-section" hidden={activeSection !== CONNECTIONS_SECTION_ID}>
        <ConnectionsSection />
      </div>
    </div>
  )
}
