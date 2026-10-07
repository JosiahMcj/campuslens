import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'

import { canSeeInstitution, roleDisplayName, type Session } from '../auth'
import { trapTab } from '../states'
import { effectiveTheme, setPrefs, usePrefs } from '../theme'
import { LensMark } from './LensMark'
import { GlideGroup } from './GlideGroup'
import {
  AccessNavIcon,
  ActionsNavIcon,
  AidQueueNavIcon,
  AuditNavIcon,
  BriefingNavIcon,
  DecisionNavIcon,
  EvidenceNavIcon,
  FiguresNavIcon,
  ChevronDownIcon,
  CrossSmallIcon,
  EditIcon,
  GearIcon,
  MoonIcon,
  SearchIcon,
  SidebarToggleIcon,
  SignOutIcon,
  SunIcon,
} from './icons'

export type PanelId =
  | 'briefing'
  | 'figures'
  | 'evidence'
  | 'actions'
  | 'decision'
  | 'access'
  | 'audit'
  | 'aid'
  | 'profile'
  | 'settings'

export interface HistoryItem {
  id: number
  question: string
  refused: boolean
  /** The briefing this cabinet produced before this page view: marked
   * "Latest briefing" under the question. */
  restored: boolean
  /** An Explore question (not a briefing). Not marked in the list. */
  explore: boolean
}

interface ChatSidebarProps {
  session: Session
  datasetName: string | null
  fictional: boolean
  history: HistoryItem[]
  /** The last briefing could not be loaded: the questions list says so and
   * offers Retry instead of looking empty. */
  historyError: string | null
  /** The last briefing is still loading: one skeleton line, not the empty text. */
  historyLoading: boolean
  onRetryHistory: () => void
  /** The current route: '/institution' marks the Institution row. */
  route: string
  /** The panels this role may open (the audit log only for roles that may
   * read it). */
  panels: PanelId[]
  activePanel: PanelId | null
  /** Small screens: the off-canvas drawer is open. */
  open: boolean
  onNewQuestion: (() => void) | null
  onSelectHistory: (id: number) => void
  onOpenPanel: (panel: PanelId) => void
  onNavigate: (path: string) => void
  onSignOut: () => void
  onClose: () => void
}

type NavPanel = Exclude<PanelId, 'profile' | 'settings'>

const PANEL_ROWS: Record<NavPanel, { label: string; icon: ReactNode }> = {
  briefing: { label: 'Full briefing', icon: <BriefingNavIcon /> },
  figures: { label: 'Key figures', icon: <FiguresNavIcon /> },
  evidence: { label: 'Evidence & sources', icon: <EvidenceNavIcon /> },
  actions: { label: 'Staff actions', icon: <ActionsNavIcon /> },
  decision: { label: 'Decision', icon: <DecisionNavIcon /> },
  // The page itself is titled "AI employees and data access"; the row's
  // short name fits the sidebar at every width.
  access: { label: 'Data access', icon: <AccessNavIcon /> },
  audit: { label: 'Audit log', icon: <AuditNavIcon /> },
  aid: { label: 'Financial Aid review', icon: <AidQueueNavIcon /> },
}

/** The capability groups, in sidebar order. Key figures, Evidence and the
 * AI employees' task cards are not rows: the figures sit under every
 * answer, the evidence opens from each number (and the Full briefing lists
 * it), and the employees are in "Data access". */
const NAV_GROUPS: { label: string; panels: NavPanel[] }[] = [
  { label: 'Briefing', panels: ['briefing', 'actions', 'decision'] },
  { label: 'Governance', panels: ['access', 'audit', 'aid'] },
]

const SMALL_SCREEN = '(max-width: 899px)'

function isSmallScreen(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia(SMALL_SCREEN).matches
}

/** True below 900 px, where the sidebar is an off-canvas drawer. */
function useSmallScreen(): boolean {
  const [small, setSmall] = useState(isSmallScreen)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const query = window.matchMedia(SMALL_SCREEN)
    const onChange = () => setSmall(query.matches)
    query.addEventListener?.('change', onChange)
    return () => query.removeEventListener?.('change', onChange)
  }, [])
  return small
}

/** One primary navigation row: icon, label, optional trailing note. */
function RailButton({
  icon,
  label,
  active = false,
  note,
  onClick,
}: {
  icon: ReactNode
  label: string
  active?: boolean
  note?: string
  onClick: () => void
}) {
  return (
    <button
      data-row
      type="button"
      className="rail-row"
      aria-current={active ? 'true' : undefined}
      title={label}
      onClick={onClick}
    >
      <span className="rail-icon">{icon}</span>
      <span className="sidebar-copy rail-label">{label}</span>
      {note !== undefined && <span className="sidebar-copy rail-note">{note}</span>}
    </button>
  )
}

/**
 * The workspace menu, opened from the sidebar's top row: the institution
 * (a heading: it is the only one, so there is nothing to choose), who is
 * signed in, Institution settings for an admin, and sign out. Rendered into <body> so the collapsing sidebar never
 * clips it; positioned under its trigger through the style object (CSSOM).
 */
function WorkspaceMenu({
  anchor,
  session,
  onClose,
  onNavigate,
  onSignOut,
}: {
  anchor: HTMLElement
  session: Session
  onClose: () => void
  onNavigate: (path: string) => void
  onSignOut: () => void
}) {
  const menuRef = useRef<HTMLDivElement>(null)
  const institutionName = session.user.institution?.name ?? 'Your institution'

  useLayoutEffect(() => {
    const menu = menuRef.current
    if (menu === null) return
    const rect = anchor.getBoundingClientRect()
    menu.style.top = `${rect.bottom + 6}px`
    menu.style.left = `${rect.left}px`
  }, [anchor])

  // Focus moves to the first item when the menu opens.
  useEffect(() => {
    menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus()
  }, [])

  useEffect(() => {
    const close = (event: PointerEvent) => {
      const target = event.target as Element
      if (!target.closest('[data-workspace-trigger]') && !target.closest('[data-workspace-menu]')) {
        onClose()
      }
    }
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [onClose])

  // Arrow keys move between the items; Escape and Tab close the menu, and
  // Escape returns focus to the trigger.
  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const items = [
      ...(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []),
    ]
    const index = items.indexOf(document.activeElement as HTMLElement)
    const move = (next: number) => {
      event.preventDefault()
      items[(next + items.length) % items.length]?.focus()
    }
    switch (event.key) {
      case 'ArrowDown':
        move(index + 1)
        break
      case 'ArrowUp':
        move(index - 1)
        break
      case 'Home':
        move(0)
        break
      case 'End':
        move(items.length - 1)
        break
      case 'Escape':
        event.preventDefault()
        event.stopPropagation()
        onClose()
        anchor.focus()
        break
      case 'Tab':
        // Closing unmounts the menu; keep focus on its trigger, not <body>.
        event.preventDefault()
        onClose()
        anchor.focus()
        break
    }
  }

  return createPortal(
    <div
      ref={menuRef}
      className="workspace-menu"
      data-workspace-menu
      role="menu"
      aria-label="Workspace"
      onKeyDown={onKeyDown}
    >
      <div className="menu-row menu-row-tall menu-heading" role="presentation">
        <LensMark className="menu-monogram" />
        <span className="menu-label menu-label-strong">{institutionName}</span>
      </div>
      <GlideGroup className="menu-glide">
        <div className="menu-rule" />
        <p className="menu-account">
          {session.user.email}
          <span>{roleDisplayName(session.user.role)}</span>
        </p>
        {canSeeInstitution(session.user.role) && (
          <button
            data-row
            type="button"
            role="menuitem"
            tabIndex={-1}
            className="menu-row"
            onClick={() => {
              onClose()
              onNavigate('/institution')
            }}
          >
            <span className="menu-icon">
              <GearIcon />
            </span>
            <span className="menu-label">Institution settings</span>
          </button>
        )}
        <div className="menu-rule" />
        <button
          data-row
          type="button"
          role="menuitem"
          tabIndex={-1}
          className="menu-row"
          onClick={() => {
            onClose()
            onSignOut()
          }}
        >
          <span className="menu-icon">
            <SignOutIcon />
          </span>
          <span className="menu-label">Sign out</span>
        </button>
      </GlideGroup>
    </div>,
    document.body,
  )
}

/**
 * The sidebar navigation, after the reference design: a compact workspace
 * switcher with a collapse control, the primary rows (New question and the
 * one-click panels) with a gliding hover highlight, this session's
 * questions with an expanding search, and one footer action (the theme).
 * Collapsed, it narrows to a 52 px icon rail with every icon in place.
 * Below 900 px it is an off-canvas drawer, always expanded.
 */
export function ChatSidebar({
  session,
  datasetName,
  fictional,
  history,
  historyError,
  historyLoading,
  onRetryHistory,
  route,
  panels,
  activePanel,
  open,
  onNewQuestion,
  onSelectHistory,
  onOpenPanel,
  onNavigate,
  onSignOut,
  onClose,
}: ChatSidebarProps) {
  const [collapsed, setCollapsed] = useState(false)
  // The open workspace menu's trigger (null while closed).
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null)
  const [searchOpen, setSearchOpen] = useState(false)
  const [listOpen, setListOpen] = useState(true)
  const [query, setQuery] = useState('')
  const prefs = usePrefs()
  const theme = effectiveTheme(prefs.theme)
  const searchRef = useRef<HTMLInputElement>(null)
  const searchButtonRef = useRef<HTMLButtonElement>(null)
  const expandRef = useRef<HTMLButtonElement>(null)
  const collapseRef = useRef<HTMLButtonElement>(null)
  const asideRef = useRef<HTMLElement>(null)
  const small = useSmallScreen()
  const drawer = small && open
  const admin = canSeeInstitution(session.user.role)

  const nextTheme = theme === 'dark' ? 'light' : 'dark'
  const initial = session.user.email.trim().charAt(0).toUpperCase()
  const needle = query.trim().toLowerCase()
  const visibleHistory = [...history]
    .reverse()
    .filter((item) => item.question.toLowerCase().includes(needle))

  useEffect(() => {
    if (searchOpen) searchRef.current?.focus()
  }, [searchOpen])

  // The phone drawer: focus moves in when it opens. The drawer only becomes
  // focusable once its slide starts (it is visibility: hidden while shut),
  // so try again on the next frames until focus has landed inside.
  useEffect(() => {
    if (!drawer) return
    const timers = [0, 60, 240].map((delay) =>
      window.setTimeout(() => {
        if (!asideRef.current?.contains(document.activeElement)) collapseRef.current?.focus()
      }, delay),
    )
    return () => timers.forEach((timer) => window.clearTimeout(timer))
  }, [drawer])

  const closeSearch = (refocus: boolean) => {
    setSearchOpen(false)
    setQuery('')
    // The search field hides; focus goes back to the button that opened it.
    if (refocus) window.setTimeout(() => searchButtonRef.current?.focus(), 0)
  }

  const collapse = () => {
    if (isSmallScreen()) {
      onClose()
      return
    }
    setCollapsed(true)
    setMenuAnchor(null)
    closeSearch(false)
    // Every control but the expand button hides; focus moves onto it.
    window.setTimeout(() => expandRef.current?.focus(), 0)
  }

  const expand = () => {
    setCollapsed(false)
    window.setTimeout(() => collapseRef.current?.focus(), 0)
  }

  const onDrawerKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (!drawer) return
    if (event.key === 'Escape' && !event.defaultPrevented) {
      event.preventDefault()
      event.stopPropagation()
      onClose()
      return
    }
    if (asideRef.current !== null) trapTab(event, asideRef.current)
  }

  return (
    <aside
      ref={asideRef}
      id="cabinet-sidebar"
      className={`chat-sidebar${open ? ' open' : ''}`}
      data-collapsed={collapsed ? 'true' : 'false'}
      aria-label="CampusLens navigation"
      role={drawer ? 'dialog' : undefined}
      aria-modal={drawer ? 'true' : undefined}
      // Off screen on a phone while closed: out of the tab order too.
      inert={small && !open}
      onKeyDown={onDrawerKeyDown}
    >
      <div className="sidebar-inner">
        <div className="sidebar-head">
          <button
            type="button"
            data-workspace-trigger
            className="workspace-control"
            aria-haspopup="menu"
            aria-label="CampusLens: workspace menu"
            aria-expanded={menuAnchor !== null}
            aria-hidden={collapsed && !small}
            tabIndex={collapsed && !small ? -1 : 0}
            onClick={(event) => {
              const trigger = event.currentTarget
              setMenuAnchor((current) => (current === null ? trigger : null))
            }}
          >
            <LensMark className="workspace-logo" />
            <span className="sidebar-copy workspace-name" title="CampusLens">
              CampusLens
            </span>
            <span className="sidebar-copy workspace-chevron">
              <ChevronDownIcon />
            </span>
          </button>
          {menuAnchor !== null && (
            <WorkspaceMenu
              anchor={menuAnchor}
              session={session}
              onClose={() => setMenuAnchor(null)}
              onNavigate={onNavigate}
              onSignOut={onSignOut}
            />
          )}
          <button
            ref={collapseRef}
            type="button"
            className="head-button collapse-control"
            aria-label={small ? 'Close menu' : 'Collapse sidebar'}
            title={small ? 'Close menu' : 'Collapse sidebar'}
            aria-hidden={collapsed && !small}
            tabIndex={collapsed && !small ? -1 : 0}
            onClick={collapse}
          >
            <span className="collapse-icon-wide">
              <SidebarToggleIcon />
            </span>
            <span className="collapse-icon-small">
              <CrossSmallIcon size={18} />
            </span>
          </button>
          <button
            ref={expandRef}
            type="button"
            className="head-button expand-control"
            aria-label="Expand sidebar"
            title="Expand sidebar"
            aria-hidden={!collapsed}
            tabIndex={collapsed ? 0 : -1}
            onClick={expand}
          >
            <SidebarToggleIcon />
          </button>
        </div>

        {onNewQuestion !== null && (
          <GlideGroup className="rail-group">
            <RailButton icon={<EditIcon />} label="New question" onClick={onNewQuestion} />
          </GlideGroup>
        )}

        <div className="sidebar-body">
          {NAV_GROUPS.map((group) => {
            const rows = group.panels.filter((panel) => panels.includes(panel))
            const institutionRow = group.label === 'Governance' && admin
            if (rows.length === 0 && !institutionRow) return null
            return (
              <div key={group.label} className="nav-group">
                <p className="sidebar-copy nav-group-label">{group.label}</p>
                <GlideGroup className="rail-group">
                  {rows.map((panel) => (
                    <RailButton
                      key={panel}
                      icon={PANEL_ROWS[panel].icon}
                      label={PANEL_ROWS[panel].label}
                      active={activePanel === panel}
                      onClick={() => onOpenPanel(panel)}
                    />
                  ))}
                  {institutionRow && (
                    <RailButton
                      icon={<GearIcon />}
                      label="Institution"
                      active={route === '/institution'}
                      onClick={() => onNavigate('/institution')}
                    />
                  )}
                </GlideGroup>
              </div>
            )
          })}

        <div className="sidebar-recents">
          <div className="sidebar-copy recents-head">
            <button
              type="button"
              className={`recents-title${searchOpen ? ' is-hidden' : ''}`}
              aria-expanded={listOpen}
              aria-hidden={searchOpen}
              tabIndex={searchOpen ? -1 : 0}
              onClick={() => setListOpen((value) => !value)}
            >
              <span className={`recents-chevron${listOpen ? '' : ' closed'}`}>
                <ChevronDownIcon />
              </span>
              Questions
            </button>
            <button
              ref={searchButtonRef}
              type="button"
              className={`head-button recents-search${searchOpen ? ' is-hidden' : ''}`}
              aria-label="Search questions"
              aria-expanded={searchOpen}
              tabIndex={searchOpen ? -1 : 0}
              onClick={() => {
                setListOpen(true)
                setSearchOpen(true)
              }}
            >
              <SearchIcon />
            </button>
            <div className={`recents-field${searchOpen ? ' open' : ''}`}>
              <span className="recents-field-icon">
                <SearchIcon size={15} />
              </span>
              <input
                ref={searchRef}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Escape') {
                    // The search closes, not the drawer around it.
                    event.preventDefault()
                    event.stopPropagation()
                    closeSearch(true)
                  }
                }}
                placeholder="Search questions"
                aria-label="Search this session's questions"
                tabIndex={searchOpen ? 0 : -1}
              />
              <button
                type="button"
                className="head-button"
                aria-label="Close search"
                tabIndex={searchOpen ? 0 : -1}
                onClick={() => closeSearch(true)}
              >
                <CrossSmallIcon />
              </button>
            </div>
          </div>

          {listOpen && (
            <GlideGroup className="rail-group">
              {visibleHistory.map((item) => (
                <button
                  key={item.id}
                  data-row
                  type="button"
                  className="rail-row recent-row"
                  title={item.question}
                  onClick={() => onSelectHistory(item.id)}
                >
                  <span className="sidebar-copy rail-label">{item.question}</span>
                  {(item.refused || item.restored) && (
                    <span className="sidebar-copy recent-meta">
                      {item.refused ? 'Refused' : 'Latest briefing'}
                    </span>
                  )}
                </button>
              ))}
              {historyError !== null && (
                <div className="sidebar-copy recents-empty" role="alert">
                  <p>We couldn't load your questions.</p>
                  <button type="button" className="link-button" onClick={onRetryHistory}>
                    Retry
                  </button>
                </div>
              )}
              {history.length === 0 && historyError === null && historyLoading && (
                <div className="sidebar-copy recents-empty" role="status" aria-busy="true">
                  <span className="visually-hidden">Loading your questions…</span>
                  <div className="skeleton skeleton-line short" />
                </div>
              )}
              {history.length === 0 && historyError === null && !historyLoading && (
                <p className="sidebar-copy recents-empty">Questions you ask appear here.</p>
              )}
              {history.length > 0 && needle !== '' && visibleHistory.length === 0 && (
                <p className="sidebar-copy recents-empty">No questions found</p>
              )}
            </GlideGroup>
          )}
        </div>

        </div>

        <div className="sidebar-copy sidebar-foot">
          {/* The fictional set is marked once, in the top bar; a real
              institution's dataset is named here. */}
          {datasetName !== null && !fictional && (
            <p className="foot-dataset">Dataset: {datasetName}</p>
          )}
          <div className="foot-row">
            <button
              type="button"
              className="foot-profile"
              aria-pressed={activePanel === 'profile'}
              onClick={() => onOpenPanel('profile')}
            >
              <span className="foot-avatar" aria-hidden="true">
                {initial}
              </span>
              <span className="foot-profile-text">
                <span className="foot-email">{session.user.email}</span>
                <span className="foot-role">{roleDisplayName(session.user.role)}</span>
              </span>
            </button>
            <button
              type="button"
              className="head-button foot-icon"
              aria-label="Settings"
              title="Settings"
              aria-pressed={activePanel === 'settings'}
              onClick={() => onOpenPanel('settings')}
            >
              <GearIcon size={18} />
            </button>
            <button
              type="button"
              className="head-button foot-icon"
              aria-label={`Switch to ${nextTheme} theme`}
              title={`Switch to ${nextTheme} theme`}
              onClick={() => setPrefs({ theme: nextTheme })}
            >
              {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
            </button>
          </div>
        </div>
      </div>
    </aside>
  )
}
