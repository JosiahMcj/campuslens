import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import {
  fetchCabinetBriefingOnce,
  fetchDecisions,
  fetchDispatch,
  fetchEvents,
  fetchExploreCatalog,
  fetchFindings,
  fetchQuestions,
  getFinding,
  postApprove,
  postAsk,
  postComposeDispatch,
  askExplore,
  postGovernanceRequest,
  postSendDispatch,
  resetBriefingOnce,
  type ApproveResponse,
  type ApprovedQuestion,
  type AskResponse,
  type AuditEvent,
  type CabinetBriefing,
  type Decision,
  type ExploreResponse,
  type Finding,
  type Findings,
  type SimulatedTask,
} from './api'
import { postAidQueue } from './aid'
import {
  canAct,
  canEditAidQueue,
  canSearchStudents,
  canSeeAidQueue,
  canSeeAuditLog,
  canSeeInstitution,
  canReadBriefing,
  canSeeSessions,
  fetchMe,
  isDepartment,
  overviewDepartments,
  type Role,
  ApiError,
  logout,
  onSessionEnded,
  type Session,
} from './auth'
import { AidQueuePanel } from './components/AidQueuePanel'
import { type AidQueueUiState } from './components/AidQueueNotice'
import { AuditLog, type DeniedRequestState } from './components/AuditLog'
import {
  BriefingSections,
  DecisionSection,
  EvidenceSources,
  ExecutiveSummary,
  Limitations,
} from './components/Briefing'
import {
  DataAccessPanel,
  ProfilePanel,
  SettingsPanel,
  type AccessGrant,
} from './components/AccountPanels'
import { ChatComposer } from './components/ChatComposer'
import { ChatSidebar, type HistoryItem, type PanelId } from './components/ChatSidebar'
import { ExploreAnswer, ExploreWorking, Thought } from './components/ExploreAnswer'
import { DecisionPanel, type DispatchUiState } from './components/DecisionPanel'
import { EvidenceDrawer } from './components/EvidenceDrawer'
// --- department accounts and the inbox (docs/ROLES.md) ---
import { DepartmentOverview } from './components/DepartmentOverview'
import { InboxPage } from './components/InboxPage'
import { SessionsPage } from './components/ItPages'
import { ItWorkspace } from './components/ItWorkspace'
import { SendAlertDialog } from './components/SendAlertDialog'
import { useInboxUnread, type AlertSource } from './inbox'
import { personaFor } from './personas'
import { Institution, type ActiveDatasetMeta } from './components/Institution'
import { LoginScreen } from './components/LoginScreen'
import { SidePanel } from './components/SidePanel'
import { StaffActionsPage } from './components/StaffActionsPage'
import { LensMark } from './components/LensMark'
import { Thinking } from './components/Thinking'
import { BackIcon, MenuIcon } from './components/icons'
import { FirstResult } from './components/FirstResult'
import { StatRow } from './components/StatRow'
import { StudentLookup } from './components/StudentLookup'
import { friendlyError, friendlyLoadError, isRateLimited, retryAfterSeconds } from './errors'
import {
  canExplore,
  loadExploreHistory,
  pickExamples,
  redactQuestion,
  saveExploreHistory,
  withQuestion,
  type ExploreTraceEvent,
} from './explore'
import {
  APPROVED_QUESTION,
  documentTitle,
  evidenceUrl,
  dispatchTasks,
  eventsAfter,
  friendlyTime,
  isApprovedQuestion,
  latestQuestionEventId,
  maxEventId,
  nextPollDelay,
  normalizeRoute,
  isPagePath,
  pagePath,
  panelFromPath,
  parseFlags,
  PANEL_MOTION_MS,
  POLL_BASE_MS,
  prefersReducedMotion,
  roleDisplayName,
  type LoadState,
  type ModelSection,
  type UiFlags,
} from './states'

/** One question's progress in the chat. */
type AskState =
  | { kind: 'idle' }
  | { kind: 'sending' }
  | { kind: 'accepted'; response: Extract<AskResponse, { accepted: true }> }
  | { kind: 'refused'; refusal: string; eventId: number | null }
  | { kind: 'error'; message: string; question: string }

/** Whether a background list (the audit log, the decisions) has loaded. */
type ResourceStatus = { kind: 'loading' } | { kind: 'ready' } | { kind: 'error'; message: string }

/**
 * Keep a closing panel on screen for its exit animation: `shown` is what to
 * render (the last open value while closing) and `closing` marks the exit.
 */
function usePanelPresence<T>(value: T | null): { shown: T | null; closing: boolean } {
  const [shown, setShown] = useState<T | null>(value)
  const [closing, setClosing] = useState(false)
  useEffect(() => {
    if (value !== null) {
      // Opening (or switching) shows the new value at once.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setShown(value)
      setClosing(false)
      return
    }
    if (prefersReducedMotion()) {
      setShown(null)
      setClosing(false)
      return
    }
    setClosing(true)
    const timer = window.setTimeout(() => {
      setShown(null)
      setClosing(false)
    }, PANEL_MOTION_MS)
    return () => window.clearTimeout(timer)
  }, [value])
  return { shown: value ?? shown, closing: value === null && closing }
}

/** True below 900 px, where the sidebar is an off-canvas drawer. */
function useSmallScreen(): boolean {
  const query = '(max-width: 899px)'
  const [small, setSmall] = useState(
    () => typeof window.matchMedia === 'function' && window.matchMedia(query).matches,
  )
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const list = window.matchMedia(query)
    const onChange = () => setSmall(list.matches)
    list.addEventListener?.('change', onChange)
    return () => list.removeEventListener?.('change', onChange)
  }, [])
  return small
}

type AuthState =
  | { kind: 'checking' }
  | { kind: 'signed-out'; notice: string | null }
  | { kind: 'signed-in'; session: Session }

const SESSION_ENDED_NOTICE = 'Your session ended. Sign in again.'

/**
 * Every history entry the app pushes carries this marker, so the in-app
 * Back knows whether the entry before it is part of CampusLens (go back) or
 * not (a page opened straight from its address: replace instead).
 */
const APP_ENTRY = { campuslens: true } as const

function pushAppEntry(url: string) {
  window.history.pushState(APP_ENTRY, '', url)
}

/** True when the current history entry was pushed by the app. */
function previousEntryInApp(): boolean {
  const state: unknown = window.history.state
  return typeof state === 'object' && state !== null && 'campuslens' in state
}

/** Focus a field once it exists and is usable (the screen may still be
 * drawing, or a page still closing): a few tries, then the fallback (the
 * main column by default). */
function focusLater(id: string, delay = 0, fallback: string | null = 'main-content') {
  const usable = (element: HTMLElement | null): element is HTMLElement =>
    element !== null &&
    element.closest('[inert]') === null &&
    !(element as HTMLInputElement).disabled &&
    element.getClientRects().length > 0
  const tries = [0, 60, 200, 500]
  const attempt = (index: number) => {
    const field = document.getElementById(id)
    if (usable(field)) {
      field.focus()
      if (document.activeElement === field) return
    }
    if (index + 1 < tries.length) {
      window.setTimeout(() => attempt(index + 1), tries[index + 1] - tries[index])
    } else if (fallback !== null) {
      document.getElementById(fallback)?.focus()
    }
  }
  window.setTimeout(() => attempt(0), delay)
}

/** Pages of cards or rows, which use the wider column. */
const WIDE_PAGES: ReadonlySet<PanelId> = new Set<PanelId>([
  'actions',
  'audit',
  'aid',
  'figures',
  'overview',
  'inbox',
  'sessions',
])

/**
 * The one sentence under a page's title saying what the page is for. Pages
 * whose content opens with its own intro (it depends on the role or the
 * state) return undefined here.
 */
function pageIntro(page: PanelId, role: Role, fictional: boolean): string | undefined {
  switch (page) {
    case 'briefing':
      return 'The whole briefing in one document: what is happening, the evidence behind every number, what staff can do now, and the decision for leadership.'
    case 'figures':
      return 'The five headline figures. Open any one to see how it is worked out and the records behind it.'
    case 'profile':
      return 'Who you are signed in as, and what your role lets you do.'
    case 'settings':
      return 'How CampusLens looks and moves in this browser.'
    case 'evidence':
      return `Every number in this briefing is computed from ${fictional ? 'fictional source data' : "your institution's source data"} and traces to one of the figures below, each listed with the fields it reads. Open any figure to see how it is computed and the records behind it.`
    case 'actions':
      return role === 'staff' || role === 'admin'
        ? 'Work staff can start now, each with a responsible office. Track who has it, when it is due and how far it has got. No leadership approval is needed.'
        : role === 'executive'
          ? 'Work staff can start now, each with a responsible office. You can follow progress and add a note for the staff working on it.'
          : 'Work staff can start now, each with a responsible office. This view is read only.'
    case 'decision':
      return role === 'executive'
        ? 'CampusLens advises. You decide. Nothing is sent on its own.'
        : 'Leadership decides. Nothing is sent on its own.'
    case 'access':
      return "Each AI employee sees only the fields its task needs, and never a student's name or identifiers. A request outside those fields is refused before any AI employee is asked, and the refusal is logged."
    case 'audit':
      return 'Every question, data request, refusal and decision is recorded here and can never be changed. Newest entries are first.'
    case 'students':
      return 'Look up one student by name to see their program, progress, GPA, holds and advisor.'
    case 'overview':
      return role === 'executive' || role === 'admin'
        ? "Each department's headline figures for the current term, computed from the records. Groups of fewer than 10 students are withheld. Send any figure to the person who should look at it."
        : "Your department's headline figures for the current term, computed from the records. Totals only: no student is named, and groups of fewer than 10 are withheld."
    case 'inbox':
      return 'Alerts other people sent you, with what they point at, and the alerts you sent. Mark an alert reviewed once you have looked.'
    case 'sessions':
      return 'Who is signed in right now and when each account was last seen. No session details are shown.'
    case 'accounts':
    case 'connections':
      return undefined
    case 'aid':
      return canEditAidQueue(role)
        ? 'Facts for the Financial Aid office to start its own review. CampusLens decides nothing about any student; a person in the office sets each status and note.'
        : 'Facts for the Financial Aid office to start its own review. CampusLens decides nothing about any student. This view is read only.'
  }
}

/** How long each live trace line stays on screen before the next one shows
 * (none in tests, so they run at full speed). */
const TRACE_STEP_MS = import.meta.env.MODE === 'test' ? 0 : 450

/** The approved question behind the full briefing and the decision. */
const SPRING_QUESTION_ID = 'spring-registration'

/** A task's status in the working reply, in plain words. */
const TASK_STATUS_WORDS = {
  working: 'working…',
  done: 'done',
  unavailable: 'unavailable',
} as const

/**
 * The shell: sign-in first, then the app behind it. On load the session
 * is checked with GET /auth/me; a 401 from any API call anywhere in the app
 * (the session expired or the account was disabled) lands here through
 * onSessionEnded and returns the app to /login with the session-ended line.
 */
function App() {
  const flags = useMemo(() => parseFlags(window.location.search), [])
  const [auth, setAuth] = useState<AuthState>({ kind: 'checking' })
  const [authError, setAuthError] = useState<string | null>(null)
  const [route, setRoute] = useState(() => normalizeRoute(window.location.pathname))
  const retryTimer = useRef<number | null>(null)
  // Bumped to run the session check again (Retry, or after a 429's wait).
  const [checkRun, setCheckRun] = useState(0)
  // Just signed in on this screen: the question box (or the main column, for
  // a role that does not ask) takes focus once the workspace is ready.
  const [focusOnReady, setFocusOnReady] = useState(false)

  const checkSession = useCallback(async () => {
    try {
      const session = await fetchMe()
      setAuthError(null)
      setAuth(
        session !== null
          ? { kind: 'signed-in', session }
          : { kind: 'signed-out', notice: null },
      )
    } catch (error) {
      setAuthError(friendlyLoadError(error))
      // Too many requests: try again on our own once the server allows it.
      if (isRateLimited(error)) {
        retryTimer.current = window.setTimeout(
          () => setCheckRun((run) => run + 1),
          retryAfterSeconds(error) * 1000,
        )
      }
    }
  }, [])

  useEffect(() => {
    // The session check's setState calls all land after an await.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void checkSession()
    return () => {
      if (retryTimer.current !== null) window.clearTimeout(retryTimer.current)
    }
  }, [checkSession, checkRun])

  useEffect(() => {
    onSessionEnded(() => {
      resetBriefingOnce()
      setAuth({ kind: 'signed-out', notice: SESSION_ENDED_NOTICE })
      window.history.replaceState(null, '', '/login')
      setRoute('/login')
    })
    return () => onSessionEnded(null)
  }, [])

  useEffect(() => {
    const onPopState = () => setRoute(normalizeRoute(window.location.pathname))
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  const navigate = useCallback((path: string) => {
    const next = normalizeRoute(path)
    if (next !== normalizeRoute(window.location.pathname)) {
      pushAppEntry(next)
    }
    setRoute(next)
  }, [])

  const signedIn = useCallback((session: Session) => {
    resetBriefingOnce()
    setAuth({ kind: 'signed-in', session })
    window.history.replaceState(null, '', '/')
    setRoute('/')
    // The workspace focuses the question box once it has drawn it.
    setFocusOnReady(true)
  }, [])

  const signOut = useCallback(() => {
    void logout().then(() => {
      resetBriefingOnce()
      setAuth({ kind: 'signed-out', notice: null })
      // Replace, so Back never shows the workspace's address over sign-in.
      window.history.replaceState(null, '', '/login')
      setRoute('/login')
      focusLater('login-email', 0, null)
    })
  }, [])

  // The address always matches the screen: an unknown path goes to the
  // conversation, a signed-in user on /login goes to the conversation, and
  // a signed-out user anywhere sees /login.
  // A non-administrator who types /institution lands on the conversation,
  // with the address rewritten and one sentence saying why.
  const [routeNotice, setRouteNotice] = useState<string | null>(null)
  const role = auth.kind === 'signed-in' ? auth.session.user.role : null
  useEffect(() => {
    if (auth.kind === 'checking') return
    const deniedInstitution =
      route === '/institution' && role !== null && !canSeeInstitution(role)
    const want =
      auth.kind === 'signed-out'
        ? '/login'
        : route === '/login' || deniedInstitution
          ? '/'
          : route
    if (deniedInstitution) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- the redirect's one-line reason
      setRouteNotice('Only an administrator can open Institution settings.')
    }
    // A page's own address (/view/...) is kept: the workspace reads it.
    const onPage = want === '/' && auth.kind === 'signed-in' && isPagePath(window.location.pathname)
    if (window.location.pathname !== want && !onPage) {
      window.history.replaceState(
        window.history.state,
        '',
        want + window.location.search + window.location.hash,
      )
    }
    if (want !== route) {
      setRoute(want)
    }
  }, [auth.kind, route, role])

  useEffect(() => {
    if (auth.kind === 'checking') document.title = documentTitle(null)
    if (auth.kind === 'signed-out') document.title = documentTitle('Sign in')
  }, [auth.kind])

  if (auth.kind === 'checking') {
    return (
      <main className="login-page auth-check">
        <div className="login-panel">
          {authError !== null ? (
            <div className="state-error" role="alert">
              <h1>We couldn't check your sign-in</h1>
              <p>{authError}</p>
              <button
                type="button"
                className="primary-button btn-primary"
                onClick={() => {
                  if (retryTimer.current !== null) window.clearTimeout(retryTimer.current)
                  setAuthError(null)
                  setCheckRun((run) => run + 1)
                }}
              >
                Try again
              </button>
            </div>
          ) : (
            <p className="status-line" role="status">
              Checking your sign-in…
            </p>
          )}
        </div>
      </main>
    )
  }

  if (auth.kind === 'signed-out') {
    return <LoginScreen notice={auth.notice} onSignedIn={signedIn} />
  }

  // IT reads no student analytics: its own workspace, no findings fetched.
  if (!canReadBriefing(auth.session.user.role)) {
    return <ItWorkspace session={auth.session} flags={flags} onSignOut={signOut} />
  }

  return (
    <Shell
      session={auth.session}
      flags={flags}
      route={route}
      navigate={navigate}
      onSignOut={signOut}
      focusOnReady={focusOnReady}
      routeNotice={routeNotice}
      onDismissRouteNotice={() => setRouteNotice(null)}
    />
  )
}

interface ShellProps {
  session: Session
  flags: UiFlags
  route: string
  navigate: (path: string) => void
  onSignOut: () => void
  focusOnReady: boolean
  routeNotice: string | null
  onDismissRouteNotice: () => void
}

/**
 * The signed-in app: the findings load once here (the conversation, the
 * panels and the Institution area all read them). One layout for every
 * route: the sidebar, and a main column that shows the conversation or,
 * at /institution, the Institution area.
 */
function Shell({
  session,
  flags,
  route,
  navigate,
  onSignOut,
  focusOnReady,
  routeNotice,
  onDismissRouteNotice,
}: ShellProps) {
  const [findingsState, setFindingsState] = useState<LoadState<Findings>>({
    kind: 'loading',
  })

  const loadFindings = useCallback(async () => {
    try {
      const data = await fetchFindings(flags)
      setFindingsState({ kind: 'ready', data })
    } catch (error) {
      setFindingsState({ kind: 'error', message: friendlyLoadError(error) })
    }
  }, [flags])

  useEffect(() => {
    // The findings fetch's setState calls all land after an await.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadFindings()
  }, [loadFindings])

  const reloadFindings = useCallback(() => {
    resetBriefingOnce()
    setFindingsState({ kind: 'loading' })
    void loadFindings()
  }, [loadFindings])

  const retryFindings = useCallback(() => {
    setFindingsState({ kind: 'loading' })
    void loadFindings()
  }, [loadFindings])

  const meta = findingsState.kind === 'ready' ? findingsState.data.meta : null
  const fictional = meta?.fictional === true
  const datasetName = meta?.dataset?.name ?? null
  const activeDataset: ActiveDatasetMeta | null =
    meta?.dataset !== null && meta?.dataset !== undefined
      ? { id: meta.dataset.id, fictional }
      : null

  return (
    <BriefingPage
      session={session}
      flags={flags}
      findingsState={findingsState}
      fictional={fictional}
      datasetName={datasetName}
      route={route}
      navigate={navigate}
      onSignOut={onSignOut}
      onRetryFindings={retryFindings}
      focusOnReady={focusOnReady}
      routeNotice={routeNotice}
      onDismissRouteNotice={onDismissRouteNotice}
      institution={
        <InstitutionPage
          session={session}
          activeDataset={activeDataset}
          onDataChanged={reloadFindings}
        />
      }
    />
  )
}

/** The Institution area, inside the same sidebar layout as the conversation. */
function InstitutionPage({
  session,
  activeDataset,
  onDataChanged,
}: {
  session: Session
  activeDataset: ActiveDatasetMeta | null
  onDataChanged: () => void
}) {
  const institutionName = session.user.institution?.name ?? 'your institution'
  return (
    <div className="chat-center doc institution-doc">
      <p className="lede panel-intro page-intro">
        The people who can sign in, the office mailboxes, the counseling permission,
        the data the briefing is computed from and the outside connections, for{' '}
        {institutionName}.
      </p>
      {canSeeInstitution(session.user.role) ? (
        <Institution
          institutionName={institutionName}
          activeDataset={activeDataset}
          currentUserEmail={session.user.email}
          onDataChanged={onDataChanged}
        />
      ) : (
        <div className="state-panel error-panel state-error" role="alert">
          <h2>Only an administrator can open Institution settings</h2>
          <p>Ask your administrator if something here needs to change.</p>
        </div>
      )}
    </div>
  )
}

/** One question and the cabinet's reply, as the chat thread shows it. A
 * restored exchange is the briefing this API process already produced
 * (GET /briefing on load), shown without re-running anything. An Explore
 * exchange is a question answered from computed tables (POST /explore). */
type ExchangeState =
  | AskState
  | { kind: 'restored' }
  | { kind: 'explore-sending'; trace?: ExploreTraceEvent[] }
  // The answer broke off part way (network, server): the question stays in
  // the thread with a plain sentence and Ask again, and any trace so far.
  | { kind: 'explore-error'; message: string; trace?: ExploreTraceEvent[]; elapsedMs: number }
  | {
      kind: 'explore'
      response: ExploreResponse
      trace?: ExploreTraceEvent[]
      elapsedMs?: number
      live?: boolean
    }

interface Exchange {
  id: number
  question: string
  state: ExchangeState
}

type AcceptedAsk = Extract<AskResponse, { accepted: true }>

interface BriefingPageProps {
  session: Session
  flags: UiFlags
  findingsState: LoadState<Findings>
  fictional: boolean
  datasetName: string | null
  route: string
  navigate: (path: string) => void
  onSignOut: () => void
  onRetryFindings: () => void
  /** Just signed in: focus the question box (or main) once it is drawn. */
  focusOnReady: boolean
  /** Why the address was rewritten (a page this role cannot open). */
  routeNotice: string | null
  onDismissRouteNotice: () => void
  /** The Institution area, shown in the main column at /institution. */
  institution: ReactNode
}

/** The briefing page: Ask and the instruments for the roles that may use
 * them, the seven-section document, and the audit log for the roles that may
 * read it. The role gates mirror the API's table (ui/src/auth.ts); the API
 * still enforces every one of them. */
function BriefingPage({
  session,
  flags,
  findingsState,
  fictional,
  datasetName,
  route,
  navigate,
  onSignOut,
  onRetryFindings,
  focusOnReady,
  routeNotice,
  onDismissRouteNotice,
  institution,
}: BriefingPageProps) {
  const role = session.user.role
  const act = canAct(role)
  // Explore: every role but the Financial Aid office (the API answers it 403).
  const explorer = canExplore(role)
  // Anyone who may type a question: the briefing roles and the Explore roles.
  const asker = act || explorer
  const userId = session.user.id
  const audit = canSeeAuditLog(role)
  const aidQueue = canSeeAidQueue(role)
  // The directory is demonstration data: offered only with the fictional set.
  const studentSearch = canSearchStudents(role) && fictional
  // Department accounts and the inbox (docs/ROLES.md).
  const persona = personaFor(role)
  const department = isDepartment(role)
  const overviewFor = overviewDepartments(role)
  const [alertSource, setAlertSource] = useState<AlertSource | null>(null)
  const [inboxUnread, refreshInboxUnread] = useInboxUnread()

  const [events, setEventsState] = useState<AuditEvent[] | null>(audit ? null : [])
  const [eventsStatus, setEventsStatus] = useState<ResourceStatus>(
    audit ? { kind: 'loading' } : { kind: 'ready' },
  )
  const setEvents = useCallback((next: AuditEvent[]) => {
    setEventsState(next)
    setEventsStatus({ kind: 'ready' })
  }, [])
  const [decisions, setDecisions] = useState<Decision[] | null>(null)
  const [decisionsStatus, setDecisionsStatus] = useState<ResourceStatus>({ kind: 'loading' })
  // A decision's message state (draft, sent, the office mailbox) that could
  // not be loaded: the decision still shows, the error sits on its message
  // step, and a Retry line under the decision checks again.
  const [dispatchLoadError, setDispatchLoadError] = useState<string | null>(null)
  const [questions, setQuestions] = useState<ApprovedQuestion[] | null>(null)
  const [questionsFailed, setQuestionsFailed] = useState(false)
  const [askState, setAskState] = useState<AskState>({ kind: 'idle' })
  // The Explore example questions (GET /explore/catalog) for the "Try" row.
  const [examples, setExamples] = useState<string[] | null>(null)
  const [examplesFailed, setExamplesFailed] = useState(false)
  // The Explore questions asked in this tab, kept across a reload (the
  // answers are not: opening one after a reload asks it again).
  const [exploreHistory, setExploreHistory] = useState<string[]>(() =>
    loadExploreHistory(userId),
  )
  // The last briefing this API process produced (GET /briefing on load).
  const [briefingStatus, setBriefingStatus] = useState<ResourceStatus>({ kind: 'loading' })
  // The briefing one /ask produced (or the last one this API process made,
  // from GET /briefing on load). Null until then: the computed page renders,
  // and no analyst or chief run happens before the question is asked.
  const [cabinetBriefing, setCabinetBriefing] = useState<CabinetBriefing | null>(null)
  // True from Ask until briefing.produced is seen (or the 200 s poll stops).
  const [runInFlight, setRunInFlight] = useState(false)
  // True once the in-flight run has been working for 60 s (the dispatch
  // panel shows "Still working…"); set from the poll effect, which may call
  // Date.now() — a component's render may not.
  const [stillWorking, setStillWorking] = useState(false)
  // Events at or before this id belong to earlier runs; the dispatch panel
  // and the poll-stop check ignore them, so a second Ask never shows the
  // previous run's cards or stops on the previous run's briefing.produced.
  const [runBaseEventId, setRunBaseEventId] = useState(0)
  const [evidenceId, setEvidenceId] = useState<string | null>(flags.evidence)
  const [approving, setApproving] = useState(false)
  const [approveError, setApproveError] = useState<string | null>(null)
  const [approvedTasks, setApprovedTasks] = useState<
    Record<string, { task: SimulatedTask; created: boolean }>
  >({})
  // The dispatch state per decision id (the governed execution step):
  // fetched with the decisions, refreshed after every compose or send.
  const [dispatches, setDispatches] = useState<Record<string, DispatchUiState>>({})
  // Preparing the Financial Aid review queue, per decision id: in flight and
  // the last error. The queue's count itself rides on the dispatch state.
  const [aidQueues, setAidQueues] = useState<Record<string, AidQueueUiState>>({})
  const [deniedRequest, setDeniedRequest] = useState<DeniedRequestState>({
    kind: 'idle',
  })
  // The chat: every exchange this page view has seen, the first one shown
  // (New question starts a clean view without forgetting the history), the
  // open slide-over panel, and the sidebar on small screens.
  const [thread, setThread] = useState<Exchange[]>([])
  const nextExchangeId = useRef(1)
  const [viewFrom, setViewFrom] = useState(0)
  // The exchange restored on load (GET /briefing). Someone who may ask
  // always starts on the empty screen with the question front and centre.
  // Settled for good once they choose anything (ask, New question, history).
  const [restoredId, setRestoredId] = useState<number | null>(null)
  const [restoredSettled, setRestoredSettled] = useState(false)
  // Decided once the restored briefing has loaded: 'hide' starts on the
  // empty screen ('show' is kept for the type; nothing sets it now).
  const [restoredView, setRestoredView] = useState<'pending' | 'show' | 'hide'>('pending')
  // The Financial Aid role's work is the review queue, so it lands there.
  const [panel, setPanel] = useState<PanelId | null>(
    () => panelFromPath(window.location.pathname) ?? (role === 'aid' ? 'aid' : null),
  )
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const menuButtonRef = useRef<HTMLButtonElement>(null)
  const small = useSmallScreen()
  // The audit-log entry to bring into view when the log next renders.
  const [auditFocusId, setAuditFocusId] = useState<number | null>(null)

  const loadEvents = useCallback(async () => {
    if (!audit) return
    try {
      setEvents(await fetchEvents(flags))
    } catch (error) {
      setEventsStatus({ kind: 'error', message: friendlyLoadError(error) })
    }
  }, [audit, flags, setEvents])

  const retryEvents = useCallback(() => {
    setEventsStatus({ kind: 'loading' })
    void loadEvents()
  }, [loadEvents])

  /** Load one decision's message state; false when it could not be loaded. */
  const loadDispatch = useCallback(
    async (decisionId: string): Promise<boolean> => {
      try {
        const info = await fetchDispatch(decisionId, flags)
        setDispatches((previous) => ({
          ...previous,
          [decisionId]: { info, busy: null, error: null },
        }))
        return true
      } catch (error) {
        const message = friendlyLoadError(error)
        setDispatchLoadError(message)
        setDispatches((previous) => ({
          ...previous,
          [decisionId]: {
            info: previous[decisionId]?.info ?? null,
            busy: null,
            error: `We couldn't check the message to the office. ${message}`,
          },
        }))
        return false
      }
    },
    [flags],
  )

  const loadDecisions = useCallback(async () => {
    try {
      const next = await fetchDecisions(flags)
      // Each decision's dispatch state (draft, sent, the office mailbox)
      // rides along so the panel renders the whole governed step on load.
      const loaded = await Promise.all(next.map((decision) => loadDispatch(decision.id)))
      setDecisions(next)
      setDecisionsStatus({ kind: 'ready' })
      if (loaded.every(Boolean)) setDispatchLoadError(null)
    } catch (error) {
      setDecisionsStatus({ kind: 'error', message: friendlyLoadError(error) })
    }
  }, [flags, loadDispatch])

  const retryDecisions = useCallback(() => {
    setDispatchLoadError(null)
    setDecisionsStatus({ kind: 'loading' })
    void loadDecisions()
  }, [loadDecisions])

  const loadQuestions = useCallback(async () => {
    // Only the briefing roles ask the approved questions.
    if (!act) return
    try {
      setQuestions(await fetchQuestions(flags))
      setQuestionsFailed(false)
    } catch {
      // The question field still works without the starter cards; a quiet
      // line with Retry takes their place.
      setQuestionsFailed(true)
    }
  }, [act, flags])

  const loadExamples = useCallback(async () => {
    if (!explorer) return
    try {
      const catalog = await fetchExploreCatalog(flags)
      setExamples(pickExamples(catalog.examples))
      setExamplesFailed(false)
    } catch {
      // The field still takes any question; a quiet line with Retry stands in.
      setExamplesFailed(true)
    }
  }, [explorer, flags])

  const retryExamples = useCallback(() => {
    setExamplesFailed(false)
    setExamples(null)
    void loadExamples()
  }, [loadExamples])

  useEffect(() => {
    saveExploreHistory(userId, exploreHistory)
  }, [userId, exploreHistory])

  // The last briefing this API process produced: a reload renders it without
  // re-running anything. 404 (none yet) leaves the computed page in place.
  const loadBriefing = useCallback(async () => {
    try {
      const briefing = await fetchCabinetBriefingOnce(flags)
      setBriefingStatus({ kind: 'ready' })
      if (briefing !== null) {
        setCabinetBriefing(briefing)
        const id = nextExchangeId.current++
        setThread((previous) =>
          previous.length === 0
            ? [{ id, question: briefing.question, state: { kind: 'restored' } }]
            : previous,
        )
        // Only matters while nothing else has happened (restoredSettled).
        setRestoredId(id)
      }
    } catch (error) {
      setBriefingStatus({
        kind: 'error',
        message: friendlyLoadError(error),
      })
    }
  }, [flags])

  const retryBriefing = useCallback(() => {
    resetBriefingOnce()
    setBriefingStatus({ kind: 'loading' })
    void loadBriefing()
  }, [loadBriefing])

  useEffect(() => {
    // Initial data fetch on mount: the audit log (for the roles that may
    // read it), the decisions, and any briefing this API process already
    // produced. The analyst briefing routes are deliberately NOT fetched —
    // the analysts and the Chief of Staff run only inside POST /ask, so
    // opening the page writes no audit events. The visible states already
    // start as 'loading', and these setState calls all land after an await.
    void loadEvents()
    void loadDecisions()
    void loadQuestions()
    void loadBriefing()
    void loadExamples()
  }, [loadEvents, loadDecisions, loadQuestions, loadBriefing, loadExamples])

  // The evidence has its own history entry: browser Back closes it.
  const openEvidence = useCallback((findingId: string) => {
    const url = evidenceUrl(window.location.search, findingId)
    // Already open (one figure to another): swap it, one entry for the layer.
    if (new URLSearchParams(window.location.search).has('evidence')) {
      window.history.replaceState(window.history.state, '', url)
    } else {
      pushAppEntry(url)
    }
    setEvidenceId(findingId)
  }, [])

  const closeEvidence = useCallback(() => {
    if (previousEntryInApp() && new URLSearchParams(window.location.search).has('evidence')) {
      // The popstate handler clears the evidence.
      window.history.back()
      return
    }
    setEvidenceId(null)
    window.history.replaceState(
      window.history.state,
      '',
      evidenceUrl(window.location.search, null),
    )
  }, [])

  // Signing in always starts on the empty home screen with a new question.
  // The restored answer stays in the history, and a waiting decision is
  // still one click away in the decisions panel.
  useEffect(() => {
    if (restoredView !== 'pending' || restoredId === null) return
    setRestoredView('hide')
  }, [restoredView, restoredId])
  const restoredOpenable = act && restoredId !== null && !restoredSettled
  const restoredHidden = restoredOpenable && restoredView !== 'show'
  // Both loads still settling: a skeleton, not an empty screen that then
  // swaps for the answer.
  const restoredPending = restoredOpenable && restoredView === 'pending'
  const firstShown = restoredHidden && restoredId !== null ? Math.max(viewFrom, restoredId + 1) : viewFrom

  const ask = useCallback(
    async (question: string) => {
      setAskState({ kind: 'sending' })
      if (route !== '/') navigate('/')
      if (restoredHidden && restoredId !== null) setViewFrom((current) => Math.max(current, restoredId + 1))
      setRestoredSettled(true)
      const exchangeId = nextExchangeId.current++
      const settle = (state: ExchangeState) =>
        setThread((previous) =>
          previous.map((item) => (item.id === exchangeId ? { ...item, state } : item)),
        )
      setThread((previous) => [
        ...previous,
        { id: exchangeId, question, state: { kind: 'sending' } },
      ])
      try {
        // Mark this run's base from a FRESH events fetch, awaited before the
        // POST: eventsMaxIdRef is 0 until the first /events load resolves, so
        // an early Ask would otherwise treat persisted old events (including
        // a previous run's briefing.produced) as this run's. Roles that may
        // not read the audit log skip this; their dispatch view comes from
        // the /ask response itself.
        if (audit) {
          try {
            const latest = await fetchEvents(flags)
            setEvents(latest)
            setRunBaseEventId(maxEventId(latest))
          } catch {
            // The log read is only bookkeeping: ask anyway, from what is known.
            setRunBaseEventId(maxEventId(events ?? []))
          }
        }
        setStillWorking(false)
        setRunInFlight(true)
        const response = await postAsk(question, flags)
        const settled: AskState = response.accepted
          ? { kind: 'accepted', response }
          : {
              kind: 'refused',
              refusal: response.refusal,
              eventId: response.event_ids.length > 0 ? Math.max(...response.event_ids) : null,
            }
        setAskState(settled)
        settle(settled)
        if (response.accepted) setCabinetBriefing(response.briefing)
        // GET /decisions answers for the latest question asked; a new run
        // can carry a different decision (each question has its own id).
        await Promise.all([loadEvents(), loadDecisions()])
      } catch (error) {
        // A failed ask leaves no row behind: the question goes back above
        // the composer with a plain sentence and "Ask again".
        setThread((previous) => previous.filter((item) => item.id !== exchangeId))
        setAskState({
          kind: 'error',
          message: friendlyLoadError(error),
          question,
        })
      } finally {
        setRunInFlight(false)
      }
    },
    [audit, flags, loadEvents, loadDecisions, setEvents, route, navigate, restoredHidden, restoredId, events],
  )

  // An Explore question: answered from tables code computed (POST /explore).
  // It never starts a briefing run, so the dispatch poll and the decisions
  // stay as they are; the audit log is refreshed for the roles that read it.
  // A failed request stays in the thread as an interrupted answer.
  const explore = useCallback(
    async (question: string) => {
      setAskState({ kind: 'sending' })
      if (route !== '/') navigate('/')
      if (restoredHidden && restoredId !== null) setViewFrom((current) => Math.max(current, restoredId + 1))
      setRestoredSettled(true)
      const exchangeId = nextExchangeId.current++
      setThread((previous) => [
        ...previous,
        { id: exchangeId, question, state: { kind: 'explore-sending' } },
      ])
      // The live trace: each stage the server reports joins the reply, one at
      // a time. The server often finishes several stages within a few
      // milliseconds, so each line waits until the one before it has been on
      // screen for TRACE_STEP_MS; the answer waits for the last line. The
      // lines are the server's real stages, only their pace is set here.
      const startedAt = Date.now()
      let trace: ExploreTraceEvent[] = []
      let lastShownAt = 0
      let paced: Promise<void> = Promise.resolve()
      const pause = (ms: number) =>
        ms > 0 ? new Promise<void>((resolve) => window.setTimeout(resolve, ms)) : Promise.resolve()
      const onEvent = (event: ExploreTraceEvent) => {
        paced = paced.then(async () => {
          await pause(lastShownAt + TRACE_STEP_MS - Date.now())
          lastShownAt = Date.now()
          trace = [...trace, event]
          const current = trace
          setThread((previous) =>
            previous.map((item) =>
              item.id === exchangeId && item.state.kind === 'explore-sending'
                ? { ...item, state: { kind: 'explore-sending', trace: current } }
                : item,
            ),
          )
        })
      }
      try {
        const response = await askExplore(question, flags, onEvent)
        await paced
        await pause(lastShownAt + TRACE_STEP_MS - Date.now())
        const elapsedMs = Date.now() - startedAt
        setThread((previous) =>
          previous.map((item) =>
            item.id === exchangeId
              ? { ...item, state: { kind: 'explore', response, trace, elapsedMs, live: true } }
              : item,
          ),
        )
        // Only answered questions are kept for after a reload: reopening one
        // asks it again, and a refusal should not be asked (and logged) twice.
        if (!response.refused && response.answer.length > 0) {
          setExploreHistory((previous) => withQuestion(previous, question))
        }
        setAskState({ kind: 'idle' })
        if (audit) void loadEvents()
      } catch (error) {
        // The question stays where it was asked, with what went wrong and
        // Ask again under it (and the trace so far, folded).
        const failed: ExchangeState = {
          kind: 'explore-error',
          message: friendlyError(error, 'Your question'),
          trace,
          elapsedMs: Date.now() - startedAt,
        }
        setThread((previous) =>
          previous.map((item) => (item.id === exchangeId ? { ...item, state: failed } : item)),
        )
        setAskState({ kind: 'idle' })
      }
    },
    [audit, flags, loadEvents, route, navigate, restoredHidden, restoredId],
  )

  // The composer takes any question: an approved briefing question runs the
  // briefing (the roles that may ask it); anything else is an Explore question.
  const approvedTexts = useMemo(
    () => (questions !== null ? questions.map((q) => q.text) : [APPROVED_QUESTION]),
    [questions],
  )
  const submit = useCallback(
    (question: string) => {
      if (act && (isApprovedQuestion(question, approvedTexts) || !explorer)) {
        void ask(question)
      } else if (explorer) {
        void explore(question)
      }
    },
    [act, explorer, approvedTexts, ask, explore],
  )

  // "Check again" in a model-unavailable section re-runs the question that
  // produced the briefing (POST /ask) — the one place the analysts and the
  // Chief of Staff ever run. Under the ?model=down demo switch the state is
  // forced client-side, so there is nothing to re-ask.
  const checkAgain = useCallback(() => {
    if (flags.modelDown) return
    void ask(cabinetBriefing?.question ?? APPROVED_QUESTION)
  }, [flags, ask, cabinetBriefing])

  // Beat 2: while a run is in flight, poll the audit log every 3 s so the
  // dispatch panel's task cards show each grant as it lands and resolve when
  // the run completes. Only roles that may read the log poll; the executive's
  // dispatch view renders from the /ask response instead. Polling stops when
  // the run ends (this run's briefing.produced appears, the ask returns, or
  // 200 s pass). A 429 waits out the server's Retry-After and other failures
  // back off, so the poll never spends the whole request budget.
  useEffect(() => {
    if (!runInFlight || !audit) return
    const startedAt = Date.now()
    let stopped = false
    let failures = 0
    let timer: number | undefined
    const tick = async () => {
      let delay = POLL_BASE_MS
      try {
        const latest = await fetchEvents(flags)
        if (stopped) return
        failures = 0
        setEvents(latest)
        const questionEventId = latestQuestionEventId(eventsAfter(latest, runBaseEventId))
        const produced =
          questionEventId !== null &&
          latest.some(
            (event) =>
              event.type === 'briefing.produced' &&
              event.payload.question_event_id === questionEventId,
          )
        if (Date.now() - startedAt > 60_000) setStillWorking(true)
        if (produced || Date.now() - startedAt > 200_000) {
          setRunInFlight(false)
          return
        }
      } catch (error) {
        if (stopped) return
        if (Date.now() - startedAt > 200_000) {
          setRunInFlight(false)
          return
        }
        failures += 1
        delay = nextPollDelay({
          ok: false,
          rateLimited: isRateLimited(error),
          retryAfterSeconds: error instanceof ApiError ? (error.retryAfter ?? null) : null,
          failures,
        })
      }
      if (!stopped) timer = window.setTimeout(() => void tick(), delay)
    }
    timer = window.setTimeout(() => void tick(), POLL_BASE_MS)
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [runInFlight, audit, flags, runBaseEventId, setEvents])

  const approve = useCallback(
    async (decisionId: string) => {
      setApproving(true)
      setApproveError(null)
      try {
        const response: ApproveResponse = await postApprove(decisionId, flags)
        setApprovedTasks((previous) => ({
          ...previous,
          [decisionId]: { task: response.task, created: response.created },
        }))
        await Promise.all([loadDecisions(), loadEvents()])
      } catch (error) {
        setApproveError(friendlyError(error, 'Your approval'))
      } finally {
        setApproving(false)
      }
    },
    [flags, loadDecisions, loadEvents],
  )

  // The governed execution step: Prepare composes the draft on the API
  // (code, never the model) and Send is the named person's click. Both
  // refresh the dispatch state and the audit log from the API's answer.
  const prepareDispatch = useCallback(
    async (decisionId: string) => {
      setDispatches((previous) => ({
        ...previous,
        [decisionId]: {
          info: previous[decisionId]?.info ?? null,
          busy: 'compose',
          error: null,
        },
      }))
      try {
        await postComposeDispatch(decisionId, flags)
        await Promise.all([loadDispatch(decisionId), loadEvents()])
      } catch (error) {
        setDispatches((previous) => ({
          ...previous,
          [decisionId]: {
            info: previous[decisionId]?.info ?? null,
            busy: null,
            error: friendlyError(error, 'The message'),
          },
        }))
      }
    },
    [flags, loadDispatch, loadEvents],
  )

  const sendDispatch = useCallback(
    async (decisionId: string) => {
      setDispatches((previous) => ({
        ...previous,
        [decisionId]: {
          info: previous[decisionId]?.info ?? null,
          busy: 'send',
          error: null,
        },
      }))
      try {
        await postSendDispatch(decisionId, flags)
        await Promise.all([loadDispatch(decisionId), loadEvents()])
      } catch (error) {
        // A refusal (already sent, no mailbox, not the sender's role) lands
        // here as the API's detail sentence, shown with the draft. Refresh
        // the dispatch state FIRST (a failed send changes the row), then put
        // the error back on top of the fresh state.
        await loadDispatch(decisionId)
        setDispatches((previous) => ({
          ...previous,
          [decisionId]: {
            info: previous[decisionId]?.info ?? null,
            busy: null,
            error: friendlyError(error, 'The message'),
          },
        }))
      }
    },
    [flags, loadDispatch, loadEvents],
  )

  // The Financial Aid review queue: prepared on the API from the record
  // (never the model), then the decision card shows its count from the
  // refreshed dispatch state.
  const prepareAidQueue = useCallback(
    async (decisionId: string) => {
      setAidQueues((previous) => ({ ...previous, [decisionId]: { busy: true, error: null } }))
      try {
        await postAidQueue(decisionId)
        await Promise.all([loadDispatch(decisionId), loadEvents()])
        setAidQueues((previous) => ({ ...previous, [decisionId]: { busy: false, error: null } }))
      } catch (error) {
        setAidQueues((previous) => ({
          ...previous,
          [decisionId]: {
            busy: false,
            error: friendlyError(error, 'The review queue'),
          },
        }))
      }
    },
    [loadDispatch, loadEvents],
  )

  // Beat 6(a): send the Enrollment Analyst's out-of-role request to the
  // field-request gate, show its refusal sentence, and highlight the new
  // data.refused event in the log.
  const showDeniedRequest = useCallback(async () => {
    setDeniedRequest({ kind: 'sending' })
    try {
      const response = await postGovernanceRequest(flags)
      if (response.granted) {
        setDeniedRequest({
          kind: 'error',
          message:
            'The request was allowed, but this test expects a refusal. ' +
            'Ask your administrator to check CampusLens is up to date.',
        })
        return
      }
      setDeniedRequest({
        kind: 'shown',
        reason: response.reason,
        eventId: response.event.id,
      })
      await loadEvents()
      setAuditFocusId(response.event.id)
    } catch (error) {
      setDeniedRequest({ kind: 'error', message: friendlyLoadError(error) })
    }
  }, [flags, loadEvents])

  // ?demo=refusal fires the same denied-request demo once, on load.
  const demoRefusalFired = useRef(false)
  useEffect(() => {
    if (act && flags.demoRefusal && !demoRefusalFired.current) {
      demoRefusalFired.current = true
      // The panel opens once, from a URL switch read on load.
      if (audit) setPanel('audit')
      void showDeniedRequest()
    }
  }, [act, audit, flags, showDeniedRequest])

  // A #audit-log deep link opens the log once it has loaded.
  const auditLinkScrolled = useRef(false)
  useEffect(() => {
    if (
      !auditLinkScrolled.current &&
      events !== null &&
      window.location.hash === '#audit-log'
    ) {
      auditLinkScrolled.current = true
      setPanel('audit')
    }
  }, [events])

  // M9 as the produced briefing carried it: only a spring registration
  // briefing asked while the counseling authorization was on has one.
  const briefingCounseling: Finding | null =
    cabinetBriefing !== null && cabinetBriefing.question_id === SPRING_QUESTION_ID
      ? (cabinetBriefing.aggregates.M9 ?? null)
      : null

  const drawerFinding =
    findingsState.kind === 'ready' && evidenceId !== null
      ? (getFinding(findingsState.data, evidenceId) ??
        (evidenceId === 'M9' ? (briefingCounseling ?? undefined) : undefined))
      : undefined

  // Sections 1, 2, 3, and 7 come from the produced briefing once one exists;
  // before the first Ask each renders its computed fallback with no analyst
  // source label. Under ?model=down all three model sections show their
  // unavailable state (section 7 keeps its static fallback).
  const modelDownSection: ModelSection = {
    kind: 'unavailable',
    reason: 'The AI analysts are switched off for this demonstration.',
  }
  const chiefSummary: ModelSection | null = flags.modelDown
    ? modelDownSection
    : (cabinetBriefing?.sections[1] ?? null)
  const chiefLimitations: ModelSection | null = flags.modelDown
    ? null
    : (cabinetBriefing?.sections[7] ?? null)
  const enrollmentSection: ModelSection | null = flags.modelDown
    ? modelDownSection
    : (cabinetBriefing?.sections[2] ?? null)
  const studentSuccessSection: ModelSection | null = flags.modelDown
    ? modelDownSection
    : (cabinetBriefing?.sections[3] ?? null)
  // The latest accepted run's response: the AI employees' panel and the
  // reply's work line read its tasks (the executive's dispatch view).
  const lastAccepted: AcceptedAsk | null = (() => {
    for (let index = thread.length - 1; index >= 0; index -= 1) {
      const state = thread[index].state
      if (state.kind === 'accepted') return state.response
    }
    return null
  })()
  // The exchange that carries the full reply (summary, figures, decision):
  // the newest answered one. Earlier answers keep their summary only.
  const currentAnswerId = (() => {
    for (let index = thread.length - 1; index >= 0; index -= 1) {
      const kind = thread[index].state.kind
      if (kind === 'accepted' || kind === 'restored') return thread[index].id
    }
    return null
  })()
  // A department account sees its own department's pages only; everyone
  // else keeps the briefing pages, with the inbox (and, for the president
  // and the admin, every department's overview) added.
  const panels: PanelId[] = department
    ? ['overview', 'inbox']
    : [
        'inbox',
        ...(overviewFor.length > 0 ? (['overview'] as PanelId[]) : []),
        'briefing',
        'figures',
        'evidence',
        'actions',
        'decision',
        'access',
        ...(aidQueue ? (['aid'] as PanelId[]) : []),
        ...(studentSearch ? (['students'] as PanelId[]) : []),
        ...(audit ? (['audit'] as PanelId[]) : []),
        ...(canSeeSessions(role) && role === 'executive' ? (['sessions'] as PanelId[]) : []),
      ]
  // An address for a page this role does not have goes back to the conversation.
  const pageAllowed = panel === null || panel === 'profile' || panel === 'settings' || panels.includes(panel)
  useEffect(() => {
    if (!pageAllowed) setPanel(null)
  }, [pageAllowed])
  // What each AI employee was granted on the latest run: from the ask
  // response when this page asked, else from the audit log's task.assigned
  // events for the newest question that dispatched tasks.
  const grants: AccessGrant[] | null = (() => {
    if (lastAccepted !== null) {
      return lastAccepted.tasks.map((task) => ({
        role: task.role,
        fields: task.granted_fields,
        findings: task.findings,
        aggregate: task.level === 'aggregate',
      }))
    }
    const assigned = (events ?? []).filter((event) => event.type === 'task.assigned')
    const runOf = (event: AuditEvent) => String(event.payload.task_id ?? '').split('-').at(-1)
    const latest = assigned.at(-1)
    if (latest === undefined) return null
    const run = runOf(latest)
    const list = (value: unknown) =>
      Array.isArray(value) ? value.map((item) => String(item)) : []
    return assigned
      .filter((event) => runOf(event) === run)
      .map((event) => ({
        role: String(event.payload.role ?? event.actor),
        fields: list(event.payload.fields),
        findings: list(event.payload.findings),
        aggregate: event.payload.level === 'aggregate',
      }))
  })()
  const visible = thread.filter((item) => item.id >= firstShown)
  // Explore questions from earlier in this tab (before a reload) that this
  // page view has not asked yet: listed first, with negative ids; opening
  // one asks it again.
  const isExplore = (state: ExchangeState) =>
    state.kind === 'explore' || state.kind === 'explore-sending' || state.kind === 'explore-error'
  const askedHere = new Set(
    thread.filter((item) => isExplore(item.state)).map((item) => redactQuestion(item.question)),
  )
  const storedQuestions = exploreHistory.filter((question) => !askedHere.has(question))
  const allHistory: HistoryItem[] = [
    ...storedQuestions.map((question, index) => ({
      id: -1 - index,
      question,
      refused: false,
      restored: false,
      explore: true,
    })),
    ...thread.map((item) => ({
      id: item.id,
      question: isExplore(item.state) ? redactQuestion(item.question) : item.question,
      refused:
        item.state.kind === 'refused' ||
        (item.state.kind === 'explore' && item.state.response.refused),
      restored: item.state.kind === 'restored',
      explore: isExplore(item.state),
    })),
  ]
  // One row per question: asking the same question again (or the restored
  // briefing's question) keeps only the newest exchange.
  const history: HistoryItem[] = allHistory.filter(
    (item, index) =>
      !allHistory.slice(index + 1).some((later) => later.question === item.question),
  )
  const sending = askState.kind === 'sending'
  const ready = findingsState.kind === 'ready'
  const onInstitution = route === '/institution'

  // Just signed in: the question box takes focus once it is drawn (the
  // main column for a role that does not ask). Once per sign-in.
  const signInFocused = useRef(false)
  // Ready means settled: the last briefing and (for the briefing roles) the
  // decisions have loaded, so the screen is not about to swap its composer.
  const homeReady =
    ready &&
    briefingStatus.kind !== 'loading' &&
    (!act || decisionsStatus.kind !== 'loading') &&
    !restoredPending
  useEffect(() => {
    if (!focusOnReady || signInFocused.current || !homeReady) return
    signInFocused.current = true
    focusLater(asker && panel === null && !onInstitution ? 'question-input' : 'main-content')
  }, [focusOnReady, homeReady, asker, panel, onInstitution])

  // A new exchange brings the START of that exchange into view (the
  // question, then the answer from its top), never the end of the answer.
  const threadLength = thread.length
  const lastExchangeId = thread.at(-1)?.id
  const lastStateKind = thread.at(-1)?.state.kind
  useEffect(() => {
    if (lastExchangeId === undefined) return
    document
      .getElementById(`exchange-${lastExchangeId}`)
      ?.scrollIntoView({ block: 'start', behavior: prefersReducedMotion() ? 'auto' : 'smooth' })
  }, [threadLength, lastExchangeId, lastStateKind])

  // The browser tab names the screen or the open panel.
  const panelTitle: Record<PanelId, string> = {
    briefing: 'Full briefing',
    figures: 'Key figures',
    evidence: 'Evidence & sources',
    actions: 'Staff actions',
    decision: 'Decision',
    access: 'AI employees and data access',
    audit: 'Audit log',
    aid: 'Financial Aid review',
    students: 'Find a student',
    profile: 'Profile',
    settings: 'Settings',
    overview: department && persona !== null ? persona.name : 'Department overviews',
    inbox: 'Inbox',
    accounts: 'Accounts',
    sessions: 'Sign-in activity',
    connections: 'Connections',
  }
  const screenTitle =
    panel !== null ? panelTitle[panel] : onInstitution ? 'Institution settings' : 'Ask'
  useEffect(() => {
    document.title = documentTitle(screenTitle)
  }, [screenTitle])

  // Each page has its own address, so Back and Forward and a reload work.
  // Browser Back and Forward restore the page AND the evidence over it.
  const firstSync = useRef(true)
  // The next address change replaces the entry instead of adding one (the
  // in-app Back on a page that was opened straight from its address).
  const replaceNext = useRef(false)
  useEffect(() => {
    const onPop = () => {
      setPanel(panelFromPath(window.location.pathname))
      setEvidenceId(new URLSearchParams(window.location.search).get('evidence'))
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  useEffect(() => {
    const here = window.location.pathname
    const want = panel !== null ? pagePath(panel) : isPagePath(here) ? (onInstitution ? '/institution' : '/') : here
    // After a popstate the address already matches, so nothing is pushed.
    if (want !== here) {
      if (firstSync.current || replaceNext.current) {
        window.history.replaceState(window.history.state, '', want)
      } else {
        pushAppEntry(want)
      }
    }
    replaceNext.current = false
    firstSync.current = false
  }, [panel, onInstitution])

  // The page's Back: the previous entry when it is part of CampusLens (as
  // the browser's Back would), else the conversation in place of this entry.
  const closePanel = useCallback(() => {
    setAuditFocusId(null)
    if (previousEntryInApp() && isPagePath(window.location.pathname)) {
      window.history.back()
      return
    }
    replaceNext.current = true
    setPanel(null)
  }, [])
  const { shown: shownPanel, closing: panelClosing } = usePanelPresence(panel)
  const { shown: shownEvidence, closing: evidenceClosing } = usePanelPresence(
    drawerFinding ?? null,
  )
  // The evidence is the top layer while it is open: the panel under it and
  // the page are inert. A closing layer no longer covers anything, so focus
  // can return to the number that opened it.
  const evidenceOpen = drawerFinding !== undefined
  const pageCovered = panel !== null || evidenceOpen

  // Leaving the phone drawer: focus goes back to the button that opened it
  // (never left on a row inside the now hidden, inert drawer).
  // (On a page the menu button that opened it is the page header's.)
  const closeSidebar = useCallback(() => {
    setSidebarOpen(false)
    window.setTimeout(() => {
      const pageMenu = document.querySelector<HTMLButtonElement>(
        '.panel-overlay:not(.is-closing) .page-menu-button',
      )
      const opener = pageMenu ?? menuButtonRef.current
      if (opener?.getClientRects().length) opener.focus()
    }, 0)
  }, [])

  const openPanel = (next: PanelId) => {
    setPanel(next)
    // A refusal highlighted in the log belongs to the visit that showed it.
    setAuditFocusId(null)
    // The evidence belongs to the page it was opened from.
    setEvidenceId(null)
    onDismissRouteNotice()
    // The panel takes focus; on close it falls back to the main column.
    setSidebarOpen(false)
  }

  // Institution's Back: the previous entry when it is part of CampusLens,
  // else the conversation in place of this entry.
  const leaveInstitution = () => {
    if (previousEntryInApp()) {
      window.history.back()
      return
    }
    window.history.replaceState(window.history.state, '', '/')
    navigate('/')
  }

  const newQuestion = () => {
    if (onInstitution) navigate('/')
    setViewFrom(nextExchangeId.current)
    setRestoredSettled(true)
    setAskState((current) => (current.kind === 'error' ? { kind: 'idle' } : current))
    // A page closing hands focus back as it starts its exit; the question
    // box takes it once the page has gone.
    const pageOpen = panel !== null
    setPanel(null)
    setEvidenceId(null)
    if (new URLSearchParams(window.location.search).has('evidence')) {
      window.history.replaceState(
        window.history.state,
        '',
        evidenceUrl(window.location.search, null),
      )
    }
    setSidebarOpen(false)
    onDismissRouteNotice()
    focusLater(
      'question-input',
      pageOpen ? (prefersReducedMotion() ? 50 : PANEL_MOTION_MS + 50) : 0,
    )
  }

  const selectHistory = (id: number) => {
    if (id < 0) {
      // A question from before a reload: its answer was not kept, so ask again.
      const question = storedQuestions[-1 - id]
      if (sidebarOpen) closeSidebar()
      if (question !== undefined && !sending) submit(question)
      return
    }
    if (onInstitution) navigate('/')
    setViewFrom((current) => Math.min(firstShown, current, id))
    setRestoredSettled(true)
    if (sidebarOpen) closeSidebar()
    window.setTimeout(
      () => document.getElementById(`exchange-${id}`)?.scrollIntoView({ block: 'start' }),
      0,
    )
  }

  const seeRefusal = (eventId: number | null) => {
    openPanel('audit')
    if (eventId !== null) {
      // The log scrolls to this entry and highlights it.
      setAuditFocusId(eventId)
      const refusal = thread.find(
        (item) => item.state.kind === 'refused' && item.state.eventId === eventId,
      )
      if (refusal !== undefined && refusal.state.kind === 'refused') {
        // The log highlights the entry and opens its detail.
        setDeniedRequest({ kind: 'shown', reason: refusal.state.refusal, eventId })
      }
    }
    void loadEvents()
  }

  // The run in flight, task by task, from the audit events polled while it
  // works (roles that may read the log). Shown inline in the working reply.
  const liveTasks = (() => {
    if (!runInFlight || events === null) return []
    const questionEventId = latestQuestionEventId(eventsAfter(events, runBaseEventId))
    return questionEventId === null ? [] : dispatchTasks(events, questionEventId)
  })()

  const workLine = (state: ExchangeState) => {
    if (state.kind === 'accepted') {
      const count = state.response.tasks.length
      return `${count} AI employees worked on this. Every step is in the audit log.`
    }
    const produced =
      cabinetBriefing !== null
        ? (events ?? []).find((event) => event.id === cabinetBriefing.question_event_id)
        : undefined
    const when = produced !== undefined ? friendlyTime(produced.ts) : null
    return when !== null
      ? `Showing the latest briefing, from ${when}.`
      : 'Showing the latest briefing.'
  }

  // The skeleton or the error a panel shows while its data is not ready.
  const notReady = (status: ResourceStatus, onRetry: () => void, what: string) =>
    status.kind === 'error' ? (
      <div className="state-panel error-panel state-error" role="alert">
        <p>
          We couldn't load {what}. {status.message}
        </p>
        <button type="button" className="secondary btn-secondary" onClick={onRetry}>
          Retry
        </button>
      </div>
    ) : (
      <div role="status" aria-busy="true" className="panel-skeleton">
        <span className="visually-hidden">Loading {what}…</span>
        <div className="skeleton skeleton-line skeleton-heading" />
        <div className="skeleton skeleton-line" />
        <div className="skeleton skeleton-line short" />
      </div>
    )
  const findingsStatus: ResourceStatus =
    findingsState.kind === 'error'
      ? { kind: 'error', message: findingsState.message }
      : findingsState.kind === 'loading'
        ? { kind: 'loading' }
        : { kind: 'ready' }
  // A message state that could not be checked does not hide the decision:
  // its error sits on the message step (dispatches[id].error).
  const decisionStatus: ResourceStatus = decisionsStatus

  const decisionPanel = (title: string | undefined, progress = false) =>
    decisionStatus.kind === 'loading' ? (
      notReady(decisionStatus, retryDecisions, 'the decision')
    ) : (
      <>
      <DecisionPanel
        {...(title !== undefined ? { title } : { headingId: null })}
        decisions={decisionStatus.kind === 'error' ? null : decisions}
        loadError={decisionStatus.kind === 'error' ? decisionStatus.message : null}
        onRetry={retryDecisions}
        events={events ?? []}
        canApprove={act}
        role={role}
        userEmail={session.user.email}
        approving={approving}
        approveError={approveError}
        approvedTasks={approvedTasks}
        dispatches={dispatches}
        onApprove={(id) => void approve(id)}
        onPrepareDispatch={(id) => void prepareDispatch(id)}
        onSendDispatch={(id) => void sendDispatch(id)}
        aidQueues={aidQueues}
        onPrepareAidQueue={(id) => void prepareAidQueue(id)}
        onOpenAidQueue={aidQueue ? () => openPanel('aid') : null}
        progress={progress}
      />
      {dispatchLoadError !== null && decisionStatus.kind === 'ready' && (
        <div className="reply-actions">
          <button type="button" className="secondary btn-secondary" onClick={retryDecisions}>
            Check the message again
          </button>
        </div>
      )}
      </>
    )

  const reply = (item: Exchange) => {
    const state = item.state
    if (state.kind === 'explore-sending') return <ExploreWorking trace={state.trace} mark={false} />
    if (state.kind === 'explore-error') {
      return (
        <>
          {state.trace !== undefined && state.trace.length > 0 && (
            <Thought trace={state.trace} elapsedMs={state.elapsedMs} />
          )}
          <div className="state-panel error-panel state-error interrupted" role="alert">
            <p>
              This answer was interrupted. {state.message}
            </p>
            <button
              type="button"
              className="secondary btn-secondary"
              disabled={sending}
              onClick={() => {
                // The new attempt takes the interrupted one's place.
                setThread((previous) => previous.filter((other) => other.id !== item.id))
                submit(item.question)
              }}
            >
              Ask again
            </button>
          </div>
        </>
      )
    }
    if (state.kind === 'explore') {
      return (
        <>
        <ExploreAnswer
          answerKey={String(item.id)}
          response={state.response}
          trace={state.trace}
          elapsedMs={state.elapsedMs}
          live={state.live}
          fallbackSuggestions={examples ?? []}
          onAsk={submit}
          busy={sending}
          onSeeAuditLog={
            audit
              ? () => {
                  openPanel('audit')
                  void loadEvents()
                }
              : null
          }
        />
        {!state.response.refused && state.response.answer.length > 0 && (
          <div className="alert-action">
            <button
              type="button"
              className="link-button"
              onClick={() =>
                setAlertSource({
                  kind: 'explore',
                  question: item.question,
                  answer: state.response.answer.slice(0, 6).map((sentence) => sentence.text),
                })
              }
            >
              Send alert about this answer
            </button>
          </div>
        )}
        </>
      )
    }
    if (state.kind === 'sending' || state.kind === 'idle' || state.kind === 'error') {
      return (
        <Thinking
          mark={false}
          detail={
            <div className="work-text">
              <p className="thinking-step">
                {stillWorking
                  ? 'Still working… the analysts are taking longer than usual.'
                  : liveTasks.length > 0
                    ? 'The AI employees are working on it'
                    : 'The Chief of Staff is assigning the analysts'}
              </p>
              {liveTasks.length > 0 && (
                <ul className="work-progress">
                  {liveTasks.map((task) => (
                    <li key={task.task_id} data-status={task.status}>
                      {roleDisplayName(task.role)}: {TASK_STATUS_WORDS[task.status]}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          }
        />
      )
    }
    if (state.kind === 'refused') {
      return (
        <div className="refusal-card" role="alert">
          <h3>Refused</h3>
          <p>{state.refusal}</p>
          {audit && (
            <button
              type="button"
              className="link-button"
              onClick={() => seeRefusal(state.eventId)}
            >
              See the refusal in the audit log
            </button>
          )}
        </div>
      )
    }
    if (!ready) return null
    const findings = findingsState.data
    const current = item.id === currentAnswerId
    const summarySection: ModelSection | null =
      state.kind === 'accepted' && !current
        ? state.response.briefing.sections[1]
        : chiefSummary
    return (
      <>
        {current && (
          <button type="button" className="work-line" onClick={() => openPanel('access')}>
            <span className="work-dot" aria-hidden="true" />
            {workLine(state)}
            <span className="work-link">See who worked on this</span>
          </button>
        )}
        {current && (
          <FirstResult
            findings={findings}
            fictional={fictional}
            role={role}
            decision={decisions?.[0] ?? null}
            dispatch={decisions?.[0] != null ? (dispatches[decisions[0].id]?.info ?? null) : null}
            onOpenEvidence={openEvidence}
            onReviewNextSteps={() => {
              const target = document.getElementById('reply-next-steps')
              if (target === null) return
              target.scrollIntoView({
                behavior: prefersReducedMotion() ? 'auto' : 'smooth',
                block: 'start',
              })
              target.focus({ preventScroll: true })
            }}
          />
        )}
        <ExecutiveSummary
          findings={findings}
          chiefSummary={summarySection}
          onOpenEvidence={openEvidence}
          headingId={null}
          title={current ? 'Summary' : 'Earlier answer'}
        />
        {current && (
          <>
            <div id="reply-next-steps" className="reply-next-steps" tabIndex={-1}>
              {decisionPanel('Your decision')}
            </div>
            <div className="reply-actions">
              <button type="button" className="link-button" onClick={() => openPanel('briefing')}>
                Read the full briefing
              </button>
            </div>
          </>
        )}
      </>
    )
  }

  const composer = (starters: boolean) =>
    asker ? (
      <ChatComposer
        questions={act ? questions : []}
        sending={sending}
        starters={starters}
        onAsk={submit}
        error={askState.kind === 'error' ? askState.message : null}
        onAskAgain={
          askState.kind === 'error' ? () => submit(askState.question) : null
        }
        questionsFailed={act && questionsFailed && questions === null}
        onRetryQuestions={() => void loadQuestions()}
        examples={explorer ? (persona !== null && persona.examples.length > 0 ? persona.examples : examples) : []}
        examplesFailed={explorer && examplesFailed}
        onRetryExamples={retryExamples}
        quietNote={!starters}
      />
    ) : null

  // Before any question has been asked there is no briefing and no decision
  // to show: the Briefing panels and the Decision say so (and never offer
  // an approval for a briefing nobody asked for). The latest briefing is
  // kept by the API across restarts (GET /briefing), so "asked" is simply
  // "a briefing exists".
  // The approved question the Briefing panels and the Decision start from.
  const springQuestion = (questions ?? []).find((item) => item.id === SPRING_QUESTION_ID)
  const briefingGate = (content: () => ReactNode) =>
    cabinetBriefing === null && briefingStatus.kind !== 'ready' ? (
      notReady(briefingStatus, retryBriefing, 'the last briefing')
    ) : cabinetBriefing === null ? (
      <div className="state-panel state-empty">
        <p>
          {act
            ? 'Ask the spring registration question first. The briefing and the decision appear here.'
            : 'The briefing and the decision appear here once an executive asks the spring registration question.'}
        </p>
        {act && springQuestion !== undefined && (
          <div className="state-actions">
            <button
              type="button"
              className="btn-primary"
              disabled={sending}
              aria-busy={sending ? 'true' : undefined}
              onClick={() => void submit(springQuestion.text)}
            >
              {sending && <span className="spinner" aria-hidden="true" />}
              {sending ? 'Asking…' : 'Ask it now'}
            </button>
          </div>
        )}
        {act && springQuestion !== undefined && askState.kind === 'error' &&
          askState.question === springQuestion.text && (
            // Under the button it belongs to; the composer announces the error.
            <p className="error-line">{askState.message}</p>
          )}
      </div>
    ) : (
      content()
    )

  // `what` names the page's own content in its loading and error lines.
  const findingsPanel = (what: string, content: () => ReactNode) =>
    ready ? content() : notReady(findingsStatus, onRetryFindings, what)

  const blocked = pageCovered || (sidebarOpen && small)

  return (
    <div className="chat-app">
      <a
        href={asker && !onInstitution ? '#question-input' : '#main-content'}
        className="skip-link visually-hidden"
        onFocus={(event) => event.currentTarget.classList.remove('visually-hidden')}
        onBlur={(event) => event.currentTarget.classList.add('visually-hidden')}
        onClick={(event) => {
          event.preventDefault()
          const question = asker && !onInstitution ? document.getElementById('question-input') : null
          if (question !== null && !question.closest('[inert]')) question.focus()
          else document.getElementById('main-content')?.focus()
        }}
      >
        {asker && !onInstitution ? 'Skip to question' : 'Skip to main content'}
      </a>
      <div className="chat-layer" style={{ display: 'contents' }}>
        <ChatSidebar
          session={session}
          datasetName={datasetName}
          fictional={fictional}
          history={history}
          historyError={briefingStatus.kind === 'error' ? briefingStatus.message : null}
          historyLoading={briefingStatus.kind === 'loading'}
          onRetryHistory={retryBriefing}
          route={route}
          panels={panels}
          activePanel={panel}
          open={sidebarOpen}
          onNewQuestion={act || department ? newQuestion : null}
          onSelectHistory={selectHistory}
          onOpenPanel={openPanel}
          onNavigate={(path) => {
            setPanel(null)
            if (sidebarOpen) closeSidebar()
            navigate(path)
          }}
          onSignOut={onSignOut}
          onClose={closeSidebar}
          inboxUnread={inboxUnread}
        />
        {sidebarOpen && <div className="sidebar-backdrop" onClick={closeSidebar} />}

        <main
          className="chat-main"
          data-route={route}
          id="main-content"
          tabIndex={-1}
          inert={blocked}
        >
          <header className="chat-topbar">
            <div className="topbar-row">
            <button
              ref={menuButtonRef}
              type="button"
              className="icon-button menu-button"
              aria-label="Open menu"
              aria-expanded={sidebarOpen}
              aria-controls="cabinet-sidebar"
              onClick={() => setSidebarOpen(true)}
            >
              <MenuIcon />
            </button>
            <span className="topbar-title">
              {department && persona !== null ? persona.name : 'Student success briefing'}
            </span>
            {fictional && <span className="topbar-badge">Fictional data</span>}
            </div>
            {routeNotice !== null && (
              // In the sticky bar, so the answer scrolling into view never hides it.
              <div className="route-notice" role="status">
                <p>{routeNotice}</p>
                <button type="button" className="link-button" onClick={onDismissRouteNotice}>
                  Dismiss
                </button>
              </div>
            )}
          </header>

          {onInstitution && (
            <div className="institution-page">
              {/* The shared page header: Back and the title at the same
                  place as every other page's. The title is the focus
                  target after navigation. */}
              <div className="side-panel-header">
                <div className="page-header-controls">
                  <button type="button" className="page-back" onClick={leaveInstitution}>
                    <BackIcon />
                    <span>Back</span>
                  </button>
                </div>
                <h1 id="main-heading" tabIndex={-1}>
                  Institution settings
                </h1>
              </div>
              {institution}
            </div>
          )}

          {!onInstitution && findingsState.kind === 'loading' && (
            <div className="chat-center" role="status" aria-busy="true" aria-live="polite">
              <span className="visually-hidden">Loading the briefing…</span>
              <div className="skeleton skeleton-heading" />
              <div className="skeleton" />
              <div className="skeleton short" />
            </div>
          )}

          {!onInstitution && findingsState.kind === 'error' && (
            <div className="chat-center">
              <div className="state-panel error-panel state-error" role="alert">
                <h2>We couldn't load the briefing</h2>
                <p>{findingsState.message}</p>
                <button type="button" className="primary-button btn-primary" onClick={onRetryFindings}>
                  Retry
                </button>
              </div>
            </div>
          )}

          {!onInstitution && ready && visible.length === 0 && restoredPending && (
            <div className="chat-center" role="status" aria-busy="true">
              <span className="visually-hidden">Loading your last briefing…</span>
              <div className="skeleton skeleton-heading" />
              <div className="skeleton" />
              <div className="skeleton short" />
            </div>
          )}

          {!onInstitution && ready && visible.length === 0 && !restoredPending && (
            <div className="chat-empty">
              <LensMark className="empty-mark" />
              {persona !== null && <p className="persona-kicker">{persona.name}</p>}
              <h1>{asker ? 'What would you like to know?' : 'CampusLens briefings'}</h1>
              {(asker || briefingStatus.kind !== 'error') && (
                <p className="empty-lede">
                  {persona !== null && !act
                    ? persona.lede
                    : act
                    ? "Ask an approved briefing question, or ask anything about Demonstration University's students, courses and majors. Every number is computed from the records."
                    : explorer
                      ? "Ask anything about Demonstration University's students, courses and majors. Every number is computed from the records."
                      : 'Briefings appear here once an executive asks CampusLens a question.'}
                </p>
              )}
              {briefingStatus.kind === 'error' && (
                // A failed load is never "nothing yet": say so, with Retry.
                <div className="state-panel error-panel state-error home-load-error" role="alert">
                  <p>
                    We couldn't load the last briefing. {briefingStatus.message}
                  </p>
                  <button type="button" className="secondary btn-secondary" onClick={retryBriefing}>
                    Retry
                  </button>
                </div>
              )}
              {storedQuestions.length > 0 &&
                thread.every((item) => item.state.kind === 'restored') && (
                <p className="reload-note">
                  Answers from before you reloaded were not kept; your questions are in the
                  sidebar.
                </p>
              )}
              {composer(true)}
            </div>
          )}

          {!onInstitution && ready && visible.length > 0 && (
            <>
              <div className="chat-thread" aria-live="polite">
                {visible.map((item) => (
                  <div key={item.id} id={`exchange-${item.id}`} className="exchange">
                    <div className="msg msg-user">
                      <p>{item.question}</p>
                    </div>
                    <div className="msg msg-cabinet">
                      <LensMark
                        className="msg-avatar"
                        thinking={item.state.kind === 'sending' || item.state.kind === 'explore-sending'}
                      />
                      <div className="msg-body">
                        <p className="msg-author">CampusLens</p>
                        {reply(item)}
                      </div>
                    </div>
                  </div>
                ))}
                <div className="thread-end" />
              </div>
              {asker && <div className="chat-dock">{composer(false)}</div>}
            </>
          )}
        </main>
      </div>

      {shownPanel !== null && (
        <SidePanel
          title={panelTitle[shownPanel]}
          onClose={closePanel}
          closing={panelClosing}
          covered={evidenceOpen}
          wide={WIDE_PAGES.has(shownPanel)}
          intro={pageIntro(shownPanel, role, fictional)}
          onOpenMenu={() => setSidebarOpen(true)}
          menuOpen={sidebarOpen}
          blocked={sidebarOpen && small}
        >
          {shownPanel === 'briefing' &&
            briefingGate(() => findingsPanel('the briefing', () =>
              ready ? (
                <div className="doc">
                  <BriefingSections
                    findings={findingsState.data}
                    fictional={fictional}
                    enrollment={enrollmentSection}
                    studentSuccess={studentSuccessSection}
                    chiefSummary={chiefSummary}
                    onCheckAgain={act ? checkAgain : null}
                    onOpenEvidence={openEvidence}
                    counselingFigure={briefingCounseling}
                    onOpenStaffActions={() => openPanel('actions')}
                  />
                  <DecisionSection
                    decisions={decisionStatus.kind === 'error' ? null : decisions}
                    loadError={decisionStatus.kind === 'error' ? decisionStatus.message : null}
                    onRetry={retryDecisions}
                    onOpenDecision={() => setPanel('decision')}
                  />
                  <Limitations
                    findings={findingsState.data}
                    fictional={fictional}
                    chiefLimitations={chiefLimitations}
                    onOpenEvidence={openEvidence}
                  />
                </div>
              ) : null,
            ))}
          {shownPanel === 'figures' &&
            findingsPanel('the key figures', () =>
              ready ? (
                <div className="panel-figures">
                  <StatRow findings={findingsState.data} onOpenEvidence={openEvidence} />
                </div>
              ) : null,
            )}
          {shownPanel === 'evidence' &&
            findingsPanel('the evidence', () =>
              ready ? (
                <div className="doc panel-solo">
                  <EvidenceSources
                    findings={findingsState.data}
                    fictional={fictional}
                    onOpenEvidence={openEvidence}
                    headingId={null}
                    counselingFigure={briefingCounseling}
                  />
                </div>
              ) : null,
            )}
          {shownPanel === 'actions' &&
            findingsPanel('the staff actions', () => (
              <StaffActionsPage
                onOpenEvidence={openEvidence}
                onOpenInstitution={
                  canSeeInstitution(role)
                    ? () => {
                        setPanel(null)
                        navigate('/institution')
                      }
                    : null
                }
              />
            ))}
          {shownPanel === 'decision' &&
            briefingGate(() => <div className="doc panel-solo">{decisionPanel(undefined, true)}</div>)}
          {shownPanel === 'access' &&
            (lastAccepted === null && events === null && eventsStatus.kind === 'loading' ? (
              notReady(eventsStatus, retryEvents, 'what each AI employee could see')
            ) : (
              <DataAccessPanel
                grants={grants}
                error={
                  lastAccepted === null && events === null && eventsStatus.kind === 'error'
                    ? eventsStatus.message
                    : null
                }
                onRetry={retryEvents}
              />
            ))}
          {shownPanel === 'aid' && aidQueue && (
            <AidQueuePanel canEdit={canEditAidQueue(role)} onOpenDecision={() => openPanel('decision')} />
          )}
          {shownPanel === 'students' && studentSearch && <StudentLookup />}
          {shownPanel === 'overview' && overviewFor.length > 0 && (
            <DepartmentOverview departments={overviewFor} onSendAlert={setAlertSource} />
          )}
          {shownPanel === 'inbox' && (
            <InboxPage
              onChanged={refreshInboxUnread}
              onAsk={
                explorer
                  ? (question) => {
                      closePanel()
                      submit(question)
                    }
                  : null
              }
            />
          )}
          {shownPanel === 'sessions' && canSeeSessions(role) && <SessionsPage />}
          {shownPanel === 'profile' && (
            <ProfilePanel session={session} datasetName={datasetName} onSignOut={onSignOut} />
          )}
          {shownPanel === 'settings' && (
            <SettingsPanel
              isAdmin={canSeeInstitution(session.user.role)}
              onOpenInstitution={() => {
                setPanel(null)
                navigate('/institution')
              }}
            />
          )}
          {shownPanel === 'audit' && audit && (
            <div className="panel-solo">
              <AuditLog
                events={events}
                readOnly={!act}
                onRefresh={eventsStatus.kind === 'error' ? retryEvents : () => void loadEvents()}
                deniedRequest={deniedRequest}
                onShowDeniedRequest={() => void showDeniedRequest()}
                highlightEventId={auditFocusId}
                loadError={eventsStatus.kind === 'error' ? eventsStatus.message : null}
              />
            </div>
          )}
        </SidePanel>
      )}

      {shownEvidence !== null && (
        <EvidenceDrawer
          finding={shownEvidence}
          fictional={fictional}
          onClose={closeEvidence}
          closing={evidenceClosing}
          onSendAlert={() =>
            setAlertSource({
              kind: 'finding',
              ref: shownEvidence.id,
              label: `${shownEvidence.title}: ${shownEvidence.display}`,
            })
          }
        />
      )}
      {alertSource !== null && (
        <SendAlertDialog source={alertSource} onClose={() => setAlertSource(null)} />
      )}
    </div>
  )
}

export default App
