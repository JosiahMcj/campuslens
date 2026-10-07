import { useCallback, useEffect, useState, type ReactNode } from 'react'

import { fetchEvents, type AuditEvent } from '../api'
import { useInboxUnread } from '../inbox'
import type { Session } from '../auth'
import { friendlyLoadError } from '../errors'
import { personaFor } from '../personas'
import { documentTitle, isPagePath, pagePath, panelFromPath, type UiFlags } from '../states'
import { AuditLog } from './AuditLog'
import { ChatSidebar, type PanelId } from './ChatSidebar'
import { ConnectionsSection } from './ConnectionsSection'
import { InboxPage } from './InboxPage'
import { AccountsPage, SessionsPage } from './ItPages'
import { LensMark } from './LensMark'
import { ProfilePanel, SettingsPanel } from './AccountPanels'
import { BackIcon, MenuIcon } from './icons'

import './Roles.css'

type ItPage = 'inbox' | 'accounts' | 'sessions' | 'connections' | 'audit' | 'profile' | 'settings'

const IT_PANELS: PanelId[] = ['inbox', 'accounts', 'sessions', 'connections', 'audit']

const TITLES: Record<ItPage, string> = {
  inbox: 'Inbox',
  accounts: 'Accounts',
  sessions: 'Sign-in activity',
  connections: 'Connections',
  audit: 'Audit log',
  profile: 'Profile',
  settings: 'Settings',
}

const INTROS: Record<ItPage, string> = {
  inbox: 'Alerts other people sent you, and the ones you sent.',
  accounts:
    'Everyone who can sign in. You add, enable and disable the department and staff accounts; an administrator changes admin, executive and IT accounts.',
  sessions: 'Who is signed in right now and when each account was last seen. No session details are shown.',
  connections: 'Whether the Ellucian import and outgoing mail are set up. Credentials are never shown.',
  audit: 'Every question, data request, refusal and change is recorded here and can never be changed.',
  profile: 'Who you are signed in as, and what your role lets you do.',
  settings: 'How CampusLens looks and moves in this browser.',
}

const SHORTCUTS: { page: ItPage; label: string; detail: string }[] = [
  { page: 'accounts', label: 'Accounts', detail: 'Add a department account or disable one' },
  { page: 'sessions', label: 'Sign-in activity', detail: 'Who is signed in right now' },
  { page: 'connections', label: 'Connections', detail: 'Ellucian import and outgoing mail' },
]

function pageFromAddress(): ItPage | null {
  const page = panelFromPath(window.location.pathname)
  return page !== null && (page in TITLES) ? (page as ItPage) : null
}

/**
 * The IT workspace: the same sidebar layout as everyone else, with only IT's
 * pages (inbox, accounts, sign-in activity, connections, the audit log).
 * Nothing here reads a figure, a briefing or a student record; the API
 * refuses IT every one of those routes too.
 */
export function ItWorkspace({
  session,
  flags,
  onSignOut,
}: {
  session: Session
  flags: UiFlags
  onSignOut: () => void
}) {
  const [page, setPage] = useState<ItPage | null>(pageFromAddress)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [events, setEvents] = useState<AuditEvent[] | null>(null)
  const [eventsError, setEventsError] = useState<string | null>(null)
  const [unread, refreshUnread] = useInboxUnread()
  const persona = personaFor(session.user.role)

  const loadEvents = useCallback(async () => {
    try {
      setEvents(await fetchEvents(flags))
      setEventsError(null)
    } catch (failure) {
      setEventsError(friendlyLoadError(failure))
    }
  }, [flags])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the fetch's setState lands after an await
    if (page === 'audit') void loadEvents()
  }, [page, loadEvents])

  // Each page has its own address, so a reload keeps it.
  useEffect(() => {
    const want = page !== null ? pagePath(page) : '/'
    if (window.location.pathname !== want && (page !== null || isPagePath(window.location.pathname))) {
      window.history.replaceState(window.history.state, '', want)
    }
    document.title = documentTitle(page !== null ? TITLES[page] : 'IT')
  }, [page])

  const open = (next: PanelId) => {
    setPage(next in TITLES ? (next as ItPage) : null)
    setSidebarOpen(false)
  }

  let body: ReactNode = null
  switch (page) {
    case 'inbox':
      body = <InboxPage onChanged={refreshUnread} onAsk={null} />
      break
    case 'accounts':
      body = <AccountsPage role={session.user.role} currentUserEmail={session.user.email} />
      break
    case 'sessions':
      body = <SessionsPage />
      break
    case 'connections':
      body = <ConnectionsSection />
      break
    case 'audit':
      body = (
        <AuditLog
          events={events}
          readOnly
          onRefresh={() => void loadEvents()}
          deniedRequest={{ kind: 'idle' }}
          onShowDeniedRequest={() => undefined}
          loadError={eventsError}
        />
      )
      break
    case 'profile':
      body = <ProfilePanel session={session} datasetName={null} onSignOut={onSignOut} />
      break
    case 'settings':
      body = <SettingsPanel isAdmin={false} onOpenInstitution={() => undefined} />
      break
    case null:
      body = null
  }

  return (
    <div className="chat-app">
      <ChatSidebar
        session={session}
        datasetName={null}
        fictional={false}
        history={[]}
        historyError={null}
        historyLoading={false}
        onRetryHistory={() => undefined}
        route="/"
        panels={IT_PANELS}
        activePanel={page}
        open={sidebarOpen}
        onNewQuestion={null}
        onSelectHistory={() => undefined}
        onOpenPanel={open}
        onNavigate={() => setPage(null)}
        onSignOut={onSignOut}
        onClose={() => setSidebarOpen(false)}
        inboxUnread={unread}
        showQuestions={false}
      />
      {sidebarOpen && <div className="sidebar-backdrop" onClick={() => setSidebarOpen(false)} />}
      <main className="chat-main" id="main-content" tabIndex={-1}>
        <header className="chat-topbar">
          <div className="topbar-row">
            <button
              type="button"
              className="icon-button menu-button"
              aria-label="Open menu"
              aria-expanded={sidebarOpen}
              aria-controls="cabinet-sidebar"
              onClick={() => setSidebarOpen(true)}
            >
              <MenuIcon />
            </button>
            <span className="topbar-title">IT</span>
          </div>
        </header>
        {page === null ? (
          <div className="chat-empty">
            <LensMark className="empty-mark" />
            <p className="persona-kicker">{persona?.name ?? 'IT'}</p>
            <h1>What needs looking after?</h1>
            <p className="empty-lede">{persona?.lede}</p>
            <div className="persona-shortcuts">
              {SHORTCUTS.map((shortcut) => (
                <button
                  key={shortcut.page}
                  type="button"
                  className="persona-shortcut"
                  onClick={() => setPage(shortcut.page)}
                >
                  <strong>{shortcut.label}</strong>
                  <span>{shortcut.detail}</span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="it-main-page">
            <button type="button" className="page-back" onClick={() => setPage(null)}>
              <BackIcon />
              <span>Back</span>
            </button>
            <h1 id="main-heading" tabIndex={-1}>
              {TITLES[page]}
            </h1>
            <p className="page-intro">{INTROS[page]}</p>
            {body}
          </div>
        )}
      </main>
    </div>
  )
}
