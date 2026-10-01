import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  fetchCabinetBriefingOnce,
  fetchDecisions,
  fetchEvents,
  fetchFindings,
  fetchQuestions,
  getFinding,
  postApprove,
  postAsk,
  postGovernanceRequest,
  resetBriefingOnce,
  type ApproveResponse,
  type ApprovedQuestion,
  type AuditEvent,
  type CabinetBriefing,
  type Decision,
  type Findings,
  type SimulatedTask,
} from './api'
import {
  canAct,
  canSeeAuditLog,
  canSeeInstitution,
  fetchMe,
  logout,
  onSessionEnded,
  type Session,
} from './auth'
import { AskDispatch, DispatchPanel } from './components/DispatchPanel'
import { AuditLog, type DeniedRequestState } from './components/AuditLog'
import { BriefingSections, Limitations } from './components/Briefing'
import { DecisionPanel } from './components/DecisionPanel'
import { EvidenceDrawer } from './components/EvidenceDrawer'
import { Institution, type ActiveDatasetMeta } from './components/Institution'
import { LoginScreen } from './components/LoginScreen'
import { Masthead } from './components/Masthead'
import { QuestionBar, type AskState } from './components/QuestionBar'
import { SectionNav } from './components/SectionNav'
import { StatRow } from './components/StatRow'
import {
  APPROVED_QUESTION,
  errorMessage,
  evidenceUrl,
  eventsAfter,
  latestQuestionEventId,
  maxEventId,
  parseFlags,
  type LoadState,
  type ModelSection,
  type UiFlags,
} from './states'

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
  const [route, setRoute] = useState(() => window.location.pathname)

  const checkSession = useCallback(async () => {
    try {
      const session = await fetchMe()
      setAuth(
        session !== null
          ? { kind: 'signed-in', session }
          : { kind: 'signed-out', notice: null },
      )
    } catch (error) {
      setAuthError(errorMessage(error))
    }
  }, [])

  useEffect(() => {
    // The session check's setState calls all land after an await.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void checkSession()
  }, [checkSession])

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
    const onPopState = () => setRoute(window.location.pathname)
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  const navigate = useCallback((path: string) => {
    window.history.pushState(null, '', path)
    setRoute(path)
  }, [])

  const signedIn = useCallback(
    (session: Session) => {
      resetBriefingOnce()
      setAuth({ kind: 'signed-in', session })
      navigate('/')
    },
    [navigate],
  )

  const signOut = useCallback(() => {
    void logout().then(() => {
      resetBriefingOnce()
      setAuth({ kind: 'signed-out', notice: null })
      navigate('/login')
    })
  }, [navigate])

  // A signed-in user landing on /login is sent to the briefing.
  useEffect(() => {
    if (auth.kind === 'signed-in' && window.location.pathname === '/login') {
      window.history.replaceState(null, '', '/')
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setRoute('/')
    }
  }, [auth.kind])

  if (auth.kind === 'checking') {
    return (
      <main className="page auth-check">
        {authError !== null ? (
          <div className="state-panel error-panel" role="alert">
            <h2>The session could not be checked</h2>
            <p>{authError}</p>
            <p>
              Check that <code>make api</code> is running on 127.0.0.1:8910.
            </p>
            <button
              type="button"
              onClick={() => {
                setAuthError(null)
                void checkSession()
              }}
            >
              Retry
            </button>
          </div>
        ) : (
          <p className="status-line" role="status">
            Checking your session…
          </p>
        )}
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
 * The signed-in app: the findings load once here (the masthead, the briefing
 * page, and the Institution area all read them), and the route picks between
 * the briefing and the Institution area.
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
      setFindingsState({ kind: 'error', message: errorMessage(error) })
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
  const institutionName = session.user.institution?.name ?? 'your institution'

  if (route === '/institution') {
    return (
      <main className="page">
        <header className="page-header">
          <h1>Institution settings</h1>
          <p className="lede">
            The datasets the briefing is computed from, for {institutionName}.
          </p>
        </header>
        <aside className="rail" aria-label="Instruments">
          <div className="rail-inner">
            <Masthead
              session={session}
              datasetName={datasetName}
              route={route}
              onNavigate={navigate}
              onSignOut={onSignOut}
            />
          </div>
        </aside>
        <div className="doc institution-doc">
          {canSeeInstitution(session.user.role) ? (
            <Institution
              institutionName={institutionName}
              activeDataset={activeDataset}
              currentUserEmail={session.user.email}
              onDataChanged={reloadFindings}
            />
          ) : (
            <div className="state-panel error-panel" role="alert">
              <h2>Institution settings are not available</h2>
              <p>Only an administrator can open Institution settings.</p>
            </div>
          )}
        </div>
      </main>
    )
  }

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
    />
  )
}

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
}: BriefingPageProps) {
  const role = session.user.role
  const act = canAct(role)
  const audit = canSeeAuditLog(role)

  const [events, setEventsState] = useState<AuditEvent[] | null>(null)
  const setEvents = useCallback((next: AuditEvent[]) => {
    setEventsState(next)
  }, [])
  const [decisions, setDecisions] = useState<Decision[] | null>(null)
  const [questions, setQuestions] = useState<ApprovedQuestion[] | null>(null)
  const [askState, setAskState] = useState<AskState>({ kind: 'idle' })
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
  const [deniedRequest, setDeniedRequest] = useState<DeniedRequestState>({
    kind: 'idle',
  })

  const loadEvents = useCallback(async () => {
    if (!audit) return
    try {
      setEvents(await fetchEvents(flags))
    } catch {
      // The findings error state already covers an unreachable API.
    }
  }, [audit, flags, setEvents])

  const loadDecisions = useCallback(async () => {
    try {
      setDecisions(await fetchDecisions(flags))
    } catch {
      // Non-critical; the decision panel shows its own loading note.
    }
  }, [flags])

  const loadQuestions = useCallback(async () => {
    try {
      setQuestions(await fetchQuestions(flags))
    } catch {
      // Non-critical; the chooser buttons simply never appear.
    }
  }, [flags])

  // The last briefing this API process produced: a reload renders it without
  // re-running anything. 404 (none yet) leaves the computed page in place.
  const loadBriefing = useCallback(async () => {
    try {
      const briefing = await fetchCabinetBriefingOnce(flags)
      if (briefing !== null) setCabinetBriefing(briefing)
    } catch {
      // Non-critical; the computed page already renders.
    }
  }, [flags])

  useEffect(() => {
    // Initial data fetch on mount: the audit log (for the roles that may
    // read it), the decisions, and any briefing this API process already
    // produced. The analyst briefing routes are deliberately NOT fetched —
    // the analysts and the Chief of Staff run only inside POST /ask, so
    // opening the page writes no audit events. The visible states already
    // start as 'loading', and these setState calls all land after an await.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadEvents()
    void loadDecisions()
    void loadQuestions()
    void loadBriefing()
  }, [loadEvents, loadDecisions, loadQuestions, loadBriefing])

  const openEvidence = useCallback((findingId: string) => {
    setEvidenceId(findingId)
    window.history.replaceState(null, '', evidenceUrl(window.location.search, findingId))
  }, [])

  const closeEvidence = useCallback(() => {
    setEvidenceId(null)
    window.history.replaceState(null, '', evidenceUrl(window.location.search, null))
  }, [])

  const ask = useCallback(
    async (question: string) => {
      setAskState({ kind: 'sending' })
      try {
        // Mark this run's base from a FRESH events fetch, awaited before the
        // POST: eventsMaxIdRef is 0 until the first /events load resolves, so
        // an early Ask would otherwise treat persisted old events (including
        // a previous run's briefing.produced) as this run's. Roles that may
        // not read the audit log skip this; their dispatch view comes from
        // the /ask response itself.
        if (audit) {
          const latest = await fetchEvents(flags)
          setEvents(latest)
          setRunBaseEventId(maxEventId(latest))
        }
        setStillWorking(false)
        setRunInFlight(true)
        const response = await postAsk(question, flags)
        setAskState(
          response.accepted
            ? { kind: 'accepted', response }
            : { kind: 'refused', refusal: response.refusal },
        )
        if (response.accepted) setCabinetBriefing(response.briefing)
        // GET /decisions answers for the latest question asked; a new run
        // can carry a different decision (each question has its own id).
        await Promise.all([loadEvents(), loadDecisions()])
      } catch (error) {
        setAskState({ kind: 'error', message: errorMessage(error) })
      } finally {
        setRunInFlight(false)
      }
    },
    [audit, flags, loadEvents, loadDecisions, setEvents],
  )

  // "Check again" in a model-unavailable section re-runs the question that
  // produced the briefing (POST /ask) — the one place the analysts and the
  // Chief of Staff ever run. Under the ?model=down demo switch the state is
  // forced client-side, so there is nothing to re-ask.
  const checkAgain = useCallback(() => {
    if (flags.modelDown) return
    void ask(cabinetBriefing?.question ?? APPROVED_QUESTION)
  }, [flags, ask, cabinetBriefing])

  // Beat 2: while a run is in flight, poll the audit log every second so the
  // dispatch panel's task cards show each grant as it lands and resolve when
  // the run completes. Only roles that may read the log poll; the executive's
  // dispatch view renders from the /ask response instead. Polling stops when
  // this run's briefing.produced appears or after 200 s; a slow fetch never
  // overlaps the next tick.
  useEffect(() => {
    if (!runInFlight || !audit) return
    const startedAt = Date.now()
    let fetchInFlight = false
    const interval = window.setInterval(() => {
      if (fetchInFlight) return
      fetchInFlight = true
      void (async () => {
        try {
          const latest = await fetchEvents(flags)
          setEvents(latest)
          const questionEventId = latestQuestionEventId(
            eventsAfter(latest, runBaseEventId),
          )
          const produced =
            questionEventId !== null &&
            latest.some(
              (event) =>
                event.type === 'briefing.produced' &&
                event.payload.question_event_id === questionEventId,
            )
          if (Date.now() - startedAt > 60_000) {
            setStillWorking(true)
          }
          if (produced || Date.now() - startedAt > 200_000) {
            setRunInFlight(false)
          }
        } catch {
          // The API hiccuped; keep polling until the run resolves or times out.
        } finally {
          fetchInFlight = false
        }
      })()
    }, 1000)
    return () => window.clearInterval(interval)
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
        setApproveError(errorMessage(error))
      } finally {
        setApproving(false)
      }
    },
    [flags, loadDecisions, loadEvents],
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
            'The gate granted the request, but this demo expects a refusal. ' +
            'Check that the API is running the current build.',
        })
        return
      }
      setDeniedRequest({
        kind: 'shown',
        reason: response.reason,
        eventId: response.event.id,
      })
      await loadEvents()
      document
        .getElementById(`event-${response.event.id}`)
        ?.scrollIntoView({ block: 'center' })
    } catch (error) {
      setDeniedRequest({ kind: 'error', message: errorMessage(error) })
    }
  }, [flags, loadEvents])

  // ?demo=refusal fires the same denied-request demo once, on load.
  const demoRefusalFired = useRef(false)
  useEffect(() => {
    if (act && flags.demoRefusal && !demoRefusalFired.current) {
      demoRefusalFired.current = true
      void showDeniedRequest()
    }
  }, [act, flags, showDeniedRequest])

  // A #audit-log deep link scrolls the log into view once it has loaded.
  const auditLinkScrolled = useRef(false)
  useEffect(() => {
    if (
      !auditLinkScrolled.current &&
      events !== null &&
      window.location.hash === '#audit-log'
    ) {
      auditLinkScrolled.current = true
      document.getElementById('audit-log')?.scrollIntoView()
    }
  }, [events])

  const drawerFinding =
    findingsState.kind === 'ready' && evidenceId !== null
      ? getFinding(findingsState.data, evidenceId)
      : undefined

  // Sections 1, 2, 3, and 7 come from the produced briefing once one exists;
  // before the first Ask each renders its computed fallback with no analyst
  // source label. Under ?model=down all three model sections show their
  // unavailable state (section 7 keeps its static fallback).
  const modelDownSection: ModelSection = {
    kind: 'unavailable',
    reason: 'Forced by the ?model=down demo switch.',
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
  const dispatchVisible = act && (askState.kind === 'sending' || askState.kind === 'accepted')
  // The rail (task cards, section list, account) renders whenever it would
  // have content; on the findings error state only the document column shows.
  // The four figures sit above the document, not in the rail.
  const railVisible = dispatchVisible || findingsState.kind !== 'error'

  return (
    <main className="page">
      <header className="page-header">
        <h1>President Weekly Student Success Briefing</h1>
        <p className="lede">
          {fictional
            ? 'Governed AI employees turning fictional student-system data into one briefing a leader can act on. Students are described as people who may need support, never as scores.'
            : "Governed AI employees turning your institution's student-system data into one briefing a leader can act on. Students are described as people who may need support, never as scores."}
        </p>
      </header>

      {act && <QuestionBar state={askState} questions={questions} onAsk={ask} />}

      {findingsState.kind === 'loading' && (
        <div className="stat-row" aria-hidden="true">
          {[0, 1, 2, 3].map((index) => (
            <div key={index} className="skeleton-figure">
              <div className="skeleton skeleton-figure-value" />
              <div className="skeleton skeleton-figure-label" />
            </div>
          ))}
        </div>
      )}
      {findingsState.kind === 'ready' && (
        <div className="stat-block">
          <StatRow findings={findingsState.data} onOpenEvidence={openEvidence} />
          {fictional && (
            <p className="demo-note">
              Demonstration data. Upload your institution's export in
              Institution settings.
            </p>
          )}
        </div>
      )}

      {railVisible && (
        <aside className="rail" aria-label="Instruments">
          <div className="rail-inner">
            <Masthead
              session={session}
              datasetName={datasetName}
              route={route}
              onNavigate={navigate}
              onSignOut={onSignOut}
            />
            {dispatchVisible &&
              (audit ? (
                <DispatchPanel
                  events={events ?? []}
                  minEventId={runBaseEventId}
                  inFlight={runInFlight}
                  stillWorking={stillWorking}
                />
              ) : askState.kind === 'accepted' ? (
                <AskDispatch
                  tasks={askState.response.tasks}
                  briefing={askState.response.briefing}
                />
              ) : (
                <p className="status-line" role="status">
                  The cabinet is working…
                </p>
              ))}
            {findingsState.kind === 'ready' && <SectionNav />}
          </div>
        </aside>
      )}

      <div className="doc">
        {findingsState.kind === 'loading' && (
          <div
            className="doc-skeleton"
            role="status"
            aria-busy="true"
            aria-live="polite"
          >
            <span className="visually-hidden">Loading the briefing…</span>
            <div className="skeleton skeleton-heading" />
            <div className="skeleton" />
            <div className="skeleton short" />
            <div className="skeleton skeleton-heading" />
            <div className="skeleton" />
            <div className="skeleton short" />
            <p className="hint">
              Reading the findings, the audit log, and any briefing this session
              already produced from the API. (<code>?slow=1</code> keeps this
              visible for demos.)
            </p>
          </div>
        )}

        {findingsState.kind === 'error' && (
          <div className="state-panel error-panel" role="alert">
            <h2>The briefing could not be loaded</h2>
            <p>{findingsState.message}</p>
            <p>
              The metrics, the evidence drawer, and the audit log all come from
              the API. Check that <code>make api</code> is running on
              127.0.0.1:8910.
            </p>
            <button type="button" onClick={onRetryFindings}>
              Retry
            </button>
          </div>
        )}

        {findingsState.kind === 'ready' && (
          <>
            <BriefingSections
              findings={findingsState.data}
              fictional={fictional}
              enrollment={enrollmentSection}
              studentSuccess={studentSuccessSection}
              chiefSummary={chiefSummary}
              onCheckAgain={act ? checkAgain : null}
              onOpenEvidence={openEvidence}
            />
            <DecisionPanel
              decisions={decisions}
              events={events ?? []}
              canApprove={act}
              approving={approving}
              approveError={approveError}
              approvedTasks={approvedTasks}
              onApprove={(id) => void approve(id)}
            />
            <Limitations
              findings={findingsState.data}
              fictional={fictional}
              chiefLimitations={chiefLimitations}
              onOpenEvidence={openEvidence}
            />
          </>
        )}

        {audit && (
          <AuditLog
            events={events}
            readOnly={!act}
            onRefresh={() => void loadEvents()}
            deniedRequest={deniedRequest}
            onShowDeniedRequest={() => void showDeniedRequest()}
          />
        )}
      </div>

      {drawerFinding !== undefined && (
        <EvidenceDrawer
          finding={drawerFinding}
          fictional={fictional}
          onClose={closeEvidence}
        />
      )}
    </main>
  )
}

export default App
