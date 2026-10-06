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
  postExplore,
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
  canSeeAidQueue,
  canSeeAuditLog,
  canSeeInstitution,
  fetchMe,
  ApiError,
  logout,
  onSessionEnded,
  type Session,
} from './auth'
import { AidQueuePanel } from './components/AidQueuePanel'
import { type AidQueueUiState } from './components/AidQueueNotice'
import { AskDispatch, DispatchPanel } from './components/DispatchPanel'
import { AuditLog, type DeniedRequestState } from './components/AuditLog'
import {
  BriefingSections,
  DecisionSection,
  EvidenceSources,
  ExecutiveSummary,
  Limitations,
  StaffActions,
} from './components/Briefing'
import {
  DataAccessPanel,
  ProfilePanel,
  SettingsPanel,
  type AccessGrant,
} from './components/AccountPanels'
import { ChatComposer } from './components/ChatComposer'
import { ChatSidebar, type HistoryItem, type PanelId } from './components/ChatSidebar'
import { ExploreAnswer, ExploreWorking } from './components/ExploreAnswer'
import { DecisionPanel, type DispatchUiState } from './components/DecisionPanel'
import { EvidenceDrawer } from './components/EvidenceDrawer'
import { Institution, type ActiveDatasetMeta } from './components/Institution'
import { LoginScreen } from './components/LoginScreen'
import { SidePanel } from './components/SidePanel'
import { AscentMark } from './components/AscentMark'
import { MenuIcon } from './components/icons'
import { StatRow } from './components/StatRow'
import { friendlyError, friendlyLoadError, isRateLimited, retryAfterSeconds } from './errors'
import {
  canExplore,
  loadExploreHistory,
  pickExamples,
  redactQuestion,
  saveExploreHistory,
  withQuestion,
} from './explore'
import {
  APPROVED_QUESTION,
  documentTitle,
  evidenceUrl,
  eventsAfter,
  friendlyTime,
  isApprovedQuestion,
  latestQuestionEventId,
  maxEventId,
  nextPollDelay,
  normalizeRoute,
  parseFlags,
  PANEL_MOTION_MS,
  POLL_BASE_MS,
  prefersReducedMotion,
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
      window.history.pushState(null, '', next)
    }
    setRoute(next)
  }, [])

  const signedIn = useCallback((session: Session) => {
    resetBriefingOnce()
    setAuth({ kind: 'signed-in', session })
    window.history.replaceState(null, '', '/')
    setRoute('/')
  }, [])

  const signOut = useCallback(() => {
    void logout().then(() => {
      resetBriefingOnce()
      setAuth({ kind: 'signed-out', notice: null })
      // Replace, so Back never shows the workspace's address over sign-in.
      window.history.replaceState(null, '', '/login')
      setRoute('/login')
    })
  }, [])

  // The address always matches the screen: an unknown path goes to the
  // conversation, a signed-in user on /login goes to the conversation, and
  // a signed-out user anywhere sees /login.
  useEffect(() => {
    if (auth.kind === 'checking') return
    const want =
      auth.kind === 'signed-out' ? '/login' : route === '/login' ? '/' : route
    if (window.location.pathname !== want) {
      window.history.replaceState(null, '', want + window.location.search + window.location.hash)
    }
    if (want !== route) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- keeps the route in step with the address
      setRoute(want)
    }
  }, [auth.kind, route])

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

  return (
    <Shell
      session={auth.session}
      flags={flags}
      route={route}
      navigate={navigate}
      onSignOut={signOut}
    />
  )
}

interface ShellProps {
  session: Session
  flags: UiFlags
  route: string
  navigate: (path: string) => void
  onSignOut: () => void
}

/**
 * The signed-in app: the findings load once here (the conversation, the
 * panels and the Institution area all read them). One layout for every
 * route: the sidebar, and a main column that shows the conversation or,
 * at /institution, the Institution area.
 */
function Shell({ session, flags, route, navigate, onSignOut }: ShellProps) {
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
      <header className="page-header">
        <h1 id="main-heading" tabIndex={-1}>
          Institution settings
        </h1>
        <p className="lede">
          People, office mailboxes, counseling permission and the data the briefing
          is computed from, for {institutionName}.
        </p>
      </header>
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
  | { kind: 'explore-sending' }
  | { kind: 'explore'; response: ExploreResponse }

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
  // not be loaded: the decision card is replaced by a Retry line, so it never
  // offers to prepare a message that may already exist.
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
  // starts on the empty screen with the question front and centre, unless a
  // decision is still waiting on them: then the restored answer stays open.
  // Settled for good once they choose anything (ask, New question, history).
  const [restoredId, setRestoredId] = useState<number | null>(null)
  const [restoredSettled, setRestoredSettled] = useState(false)
  // Decided ONCE, when both the restored briefing and the decisions have
  // loaded: 'show' keeps the restored answer open (a decision waits, or the
  // decisions could not be checked), 'hide' starts on the empty screen.
  // Approving later never hides the answer the person is looking at.
  const [restoredView, setRestoredView] = useState<'pending' | 'show' | 'hide'>('pending')
  // The Financial Aid role's work is the review queue, so it lands there.
  const [panel, setPanel] = useState<PanelId | null>(() => (role === 'aid' ? 'aid' : null))
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
        setDispatchLoadError(friendlyLoadError(error))
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

  const openEvidence = useCallback((findingId: string) => {
    setEvidenceId(findingId)
    window.history.replaceState(null, '', evidenceUrl(window.location.search, findingId))
  }, [])

  const closeEvidence = useCallback(() => {
    setEvidenceId(null)
    window.history.replaceState(null, '', evidenceUrl(window.location.search, null))
  }, [])

  // Whether the restored answer is hidden behind the empty home screen.
  useEffect(() => {
    if (restoredView !== 'pending' || restoredId === null) return
    if (decisionsStatus.kind === 'loading') return
    const waiting =
      decisionsStatus.kind === 'error' ||
      (decisions ?? []).some((decision) => !decision.approved)
    setRestoredView(waiting ? 'show' : 'hide')
  }, [restoredView, restoredId, decisionsStatus, decisions])
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
  // A failed request leaves no row behind, exactly like a failed ask.
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
      try {
        const response = await postExplore(question, flags)
        setThread((previous) =>
          previous.map((item) =>
            item.id === exchangeId ? { ...item, state: { kind: 'explore', response } } : item,
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
        setThread((previous) => previous.filter((item) => item.id !== exchangeId))
        setAskState({
          kind: 'error',
          message: friendlyError(error, 'Your question'),
          question,
        })
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
            'Ask your administrator to check the Cabinet is up to date.',
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
    cabinetBriefing !== null && cabinetBriefing.question_id === 'spring-registration'
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
  const hasRun = lastAccepted !== null || (audit && runBaseEventId > 0) || cabinetBriefing !== null
  const panels: PanelId[] = [
    'briefing',
    'figures',
    'evidence',
    'actions',
    'decision',
    ...(hasRun ? (['agents'] as PanelId[]) : []),
    'access',
    ...(aidQueue ? (['aid'] as PanelId[]) : []),
    ...(audit ? (['audit'] as PanelId[]) : []),
  ]
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
  const askedHere = new Set(
    thread
      .filter((item) => item.state.kind === 'explore' || item.state.kind === 'explore-sending')
      .map((item) => redactQuestion(item.question)),
  )
  const storedQuestions = exploreHistory.filter((question) => !askedHere.has(question))
  const history: HistoryItem[] = [
    ...storedQuestions.map((question, index) => ({
      id: -1 - index,
      question,
      refused: false,
      restored: false,
      explore: true,
    })),
    ...thread.map((item) => ({
      id: item.id,
      question:
        item.state.kind === 'explore' || item.state.kind === 'explore-sending'
          ? redactQuestion(item.question)
          : item.question,
      refused:
        item.state.kind === 'refused' ||
        (item.state.kind === 'explore' && item.state.response.refused),
      restored: item.state.kind === 'restored',
      explore: item.state.kind === 'explore' || item.state.kind === 'explore-sending',
    })),
  ]
  const sending = askState.kind === 'sending'
  const ready = findingsState.kind === 'ready'
  const onInstitution = route === '/institution'

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
    agents: 'AI employees',
    access: 'AI employees and data access',
    audit: 'Audit log',
    aid: 'Financial Aid review',
    profile: 'Profile',
    settings: 'Settings',
  }
  const screenTitle =
    panel !== null ? panelTitle[panel] : onInstitution ? 'Institution settings' : 'Briefing'
  useEffect(() => {
    document.title = documentTitle(screenTitle)
  }, [screenTitle])

  const closePanel = useCallback(() => {
    setPanel(null)
    setAuditFocusId(null)
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
  const closeSidebar = useCallback(() => {
    setSidebarOpen(false)
    window.setTimeout(() => {
      if (menuButtonRef.current?.getClientRects().length) menuButtonRef.current.focus()
    }, 0)
  }, [])

  const openPanel = (next: PanelId) => {
    setPanel(next)
    // A refusal highlighted in the log belongs to the visit that showed it.
    setAuditFocusId(null)
    // The panel takes focus; on close it falls back to the main column.
    setSidebarOpen(false)
  }

  const focusQuestion = () =>
    window.setTimeout(() => document.getElementById('question-input')?.focus(), 0)

  const newQuestion = () => {
    if (onInstitution) navigate('/')
    setViewFrom(nextExchangeId.current)
    setRestoredSettled(true)
    setAskState((current) => (current.kind === 'error' ? { kind: 'idle' } : current))
    setPanel(null)
    setSidebarOpen(false)
    focusQuestion()
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
          Couldn't load {what}. {status.message}
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
  const decisionStatus: ResourceStatus =
    dispatchLoadError !== null ? { kind: 'error', message: dispatchLoadError } : decisionsStatus

  const decisionPanel = (title: string | undefined) =>
    decisionStatus.kind === 'loading' ? (
      notReady(decisionStatus, retryDecisions, 'the decision')
    ) : (
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
      />
    )

  const reply = (item: Exchange) => {
    const state = item.state
    if (state.kind === 'explore-sending') return <ExploreWorking />
    if (state.kind === 'explore') {
      return (
        <ExploreAnswer
          answerKey={String(item.id)}
          response={state.response}
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
      )
    }
    if (state.kind === 'sending' || state.kind === 'idle' || state.kind === 'error') {
      return (
        <div className="reply-working" role="status">
          <span className="typing" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
          {stillWorking
            ? 'Still working… the analysts are taking longer than usual.'
            : 'The Chief of Staff is assigning the analysts…'}
        </div>
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
          <button type="button" className="work-line" onClick={() => openPanel('agents')}>
            <span className="work-dot" aria-hidden="true" />
            {workLine(state)}
            <span className="work-link">View</span>
          </button>
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
            <StatRow findings={findings} onOpenEvidence={openEvidence} />
            {decisionPanel('Your decision')}
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
        examples={explorer ? examples : []}
        examplesFailed={explorer && examplesFailed}
        onRetryExamples={retryExamples}
      />
    ) : null

  // Before any question has been asked there is no briefing and no decision
  // to show: the Briefing panels and the Decision say so (and never offer
  // an approval for a briefing nobody asked for). The latest briefing is
  // kept by the API across restarts (GET /briefing), so "asked" is simply
  // "a briefing exists".
  const briefingGate = (content: () => ReactNode) =>
    cabinetBriefing === null && briefingStatus.kind !== 'ready' ? (
      notReady(briefingStatus, retryBriefing, 'the briefing')
    ) : cabinetBriefing === null ? (
      <div className="state-panel state-empty">
        <p>
          {act
            ? 'Ask the spring registration question first. The briefing and the decision appear here.'
            : 'The briefing and the decision appear here once an executive asks the spring registration question.'}
        </p>
      </div>
    ) : (
      content()
    )

  const findingsPanel = (content: () => ReactNode) =>
    ready ? content() : notReady(findingsStatus, onRetryFindings, 'the briefing')

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
      <div className="chat-layer" inert={pageCovered} style={{ display: 'contents' }}>
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
          onNewQuestion={act ? newQuestion : null}
          onSelectHistory={selectHistory}
          onOpenPanel={openPanel}
          onNavigate={(path) => {
            setPanel(null)
            if (sidebarOpen) closeSidebar()
            navigate(path)
          }}
          onSignOut={onSignOut}
          onClose={closeSidebar}
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
              {onInstitution ? 'Institution settings' : 'Student success briefing'}
            </span>
            {fictional && <span className="topbar-badge">Fictional data</span>}
          </header>

          {onInstitution && institution}

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
              <AscentMark className="empty-mark" />
              <h1>{asker ? 'What would you like to know?' : 'The Cabinet’s briefings'}</h1>
              <p className="empty-lede">
                {act
                  ? "Ask an approved briefing question, or ask anything about Demonstration University's students, courses and majors. Every number is computed from the records."
                  : explorer
                    ? "Ask anything about Demonstration University's students, courses and majors. Every number is computed from the records."
                    : briefingStatus.kind === 'error'
                      ? "We couldn't load the last briefing."
                      : 'Briefings appear here once an executive asks the Cabinet a question.'}
              </p>
              {!act && briefingStatus.kind === 'error' && (
                <button type="button" className="secondary btn-secondary" onClick={retryBriefing}>
                  Retry
                </button>
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
                      <AscentMark className="msg-avatar" />
                      <div className="msg-body">
                        <p className="msg-author">Cabinet</p>
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
        >
          {shownPanel === 'briefing' &&
            briefingGate(() => findingsPanel(() =>
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
          {shownPanel === 'agents' &&
            (audit ? (
              eventsStatus.kind === 'loading' && events === null ? (
                notReady(eventsStatus, retryEvents, 'the AI employees’ work')
              ) : (
                <DispatchPanel
                  events={events ?? []}
                  minEventId={runBaseEventId}
                  inFlight={runInFlight}
                  stillWorking={stillWorking}
                  error={
                    eventsStatus.kind === 'error' && events === null ? eventsStatus.message : null
                  }
                  onRetry={retryEvents}
                />
              )
            ) : lastAccepted !== null ? (
              <AskDispatch tasks={lastAccepted.tasks} briefing={lastAccepted.briefing} />
            ) : (
              <p className="hint">The AI employees' task cards appear after you ask a question.</p>
            ))}
          {shownPanel === 'figures' &&
            findingsPanel(() =>
              ready ? (
                <div className="panel-figures">
                  <p className="panel-text">
                    The five headline measures. Open any one to see how it is
                    worked out and the records behind it.
                  </p>
                  <StatRow findings={findingsState.data} onOpenEvidence={openEvidence} />
                </div>
              ) : null,
            )}
          {shownPanel === 'evidence' &&
            findingsPanel(() =>
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
            briefingGate(() => findingsPanel(() =>
              ready ? (
                <div className="doc panel-solo">
                  <StaffActions
                    findings={findingsState.data}
                    onOpenEvidence={openEvidence}
                    headingId={null}
                  />
                </div>
              ) : null,
            ))}
          {shownPanel === 'decision' &&
            briefingGate(() => <div className="doc panel-solo">{decisionPanel(undefined)}</div>)}
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
          {shownPanel === 'aid' && aidQueue && <AidQueuePanel canEdit={canEditAidQueue(role)} />}
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
        />
      )}
    </div>
  )
}

export default App
