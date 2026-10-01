import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

import { canSeeInstitution, roleDisplayName, type Session } from '../auth'
import { effectiveTheme, setPrefs, usePrefs } from '../theme'
import { AscentMark } from './AscentMark'
import { GlideGroup } from './GlideGroup'
import {
  AccessNavIcon,
  ActionsNavIcon,
  AuditNavIcon,
  BriefingNavIcon,
  DecisionNavIcon,
  EvidenceNavIcon,
  FiguresNavIcon,
  RefusalNavIcon,
  CheckSmallIcon,
  ChevronDownIcon,
  CrossSmallIcon,
  EditIcon,
  GearIcon,
  MoonIcon,
  SearchIcon,
  SidebarToggleIcon,
  SignOutIcon,
  SunIcon,
  TeamNavIcon,
} from './icons'

export type PanelId =
  | 'briefing'
  | 'figures'
  | 'evidence'
  | 'actions'
  | 'decision'
  | 'agents'
  | 'access'
  | 'audit'
  | 'profile'
  | 'settings'

export interface HistoryItem {
  id: number
  question: string
  refused: boolean
  /** The briefing this cabinet produced before this page view. */
  restored: boolean
}

interface ChatSidebarProps {
  session: Session
  datasetName: string | null
  fictional: boolean
  history: HistoryItem[]
  /** The panels this role may open (the audit log only for roles that may
   * read it; the AI employees once a run exists). */
  panels: PanelId[]
  activePanel: PanelId | null
  /** Small screens: the off-canvas drawer is open. */
  open: boolean
  onNewQuestion: (() => void) | null
  onSelectHistory: (id: number) => void
  onOpenPanel: (panel: PanelId) => void
  /** Run the refusal demo (roles that may act); null hides the row. */
  onTestRefusal: (() => void) | null
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
  agents: { label: 'AI employees', icon: <TeamNavIcon /> },
  access: { label: 'Data access', icon: <AccessNavIcon /> },
  audit: { label: 'Audit log', icon: <AuditNavIcon /> },
}

/** The capability groups, in sidebar order. */
const NAV_GROUPS: { label: string; panels: NavPanel[] }[] = [
  { label: 'Briefing', panels: ['briefing', 'figures', 'evidence', 'actions', 'decision'] },
  { label: 'Governance', panels: ['agents', 'access', 'audit'] },
]

const SMALL_SCREEN = '(max-width: 899px)'

function isSmallScreen(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia(SMALL_SCREEN).matches
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
 * (checked, the only one), who is signed in, Institution settings for an
 * admin, and sign out. Rendered into <body> so the collapsing sidebar never
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

  useEffect(() => {
    const close = (event: PointerEvent) => {
      const target = event.target as Element
      if (!target.closest('[data-workspace-trigger]') && !target.closest('[data-workspace-menu]')) {
        onClose()
      }
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('pointerdown', close)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', close)
      window.removeEventListener('keydown', onKey)
    }
  }, [onClose])

  return createPortal(
    <div ref={menuRef} className="workspace-menu" data-workspace-menu role="menu">
      <GlideGroup className="menu-glide">
        <button data-row type="button" role="menuitem" className="menu-row menu-row-tall" onClick={onClose}>
          <AscentMark className="menu-monogram" />
          <span className="menu-label menu-label-strong">{institutionName}</span>
          <span className="menu-check">
            <CheckSmallIcon />
          </span>
        </button>
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
  panels,
  activePanel,
  open,
  onNewQuestion,
  onSelectHistory,
  onOpenPanel,
  onTestRefusal,
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

  const nextTheme = theme === 'dark' ? 'light' : 'dark'
  const initial = session.user.email.trim().charAt(0).toUpperCase()
  const needle = query.trim().toLowerCase()
  const visibleHistory = [...history]
    .reverse()
    .filter((item) => item.question.toLowerCase().includes(needle))

  useEffect(() => {
    if (searchOpen) searchRef.current?.focus()
  }, [searchOpen])

  const closeSearch = () => {
    setSearchOpen(false)
    setQuery('')
  }

  const collapse = () => {
    if (isSmallScreen()) {
      onClose()
      return
    }
    setCollapsed(true)
    setMenuAnchor(null)
    closeSearch()
  }

  return (
    <aside
      className={`chat-sidebar${open ? ' open' : ''}`}
      data-collapsed={collapsed ? 'true' : 'false'}
      aria-label="Cabinet navigation"
    >
      <div className="sidebar-inner">
        <div className="sidebar-head">
          <button
            type="button"
            data-workspace-trigger
            className="workspace-control"
            aria-haspopup="menu"
            aria-expanded={menuAnchor !== null}
            aria-hidden={collapsed}
            tabIndex={collapsed ? -1 : 0}
            onClick={(event) => {
              const trigger = event.currentTarget
              setMenuAnchor((current) => (current === null ? trigger : null))
            }}
          >
            <AscentMark className="workspace-logo" />
            <span className="sidebar-copy workspace-name">Golden Eagle</span>
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
            type="button"
            className="head-button collapse-control"
            aria-label="Collapse sidebar"
            title="Collapse sidebar"
            aria-hidden={collapsed}
            tabIndex={collapsed ? -1 : 0}
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
            type="button"
            className="head-button expand-control"
            aria-label="Expand sidebar"
            title="Expand sidebar"
            aria-hidden={!collapsed}
            tabIndex={collapsed ? 0 : -1}
            onClick={() => setCollapsed(false)}
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
            const refusal = group.label === 'Governance' && onTestRefusal !== null
            if (rows.length === 0 && !refusal) return null
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
                  {refusal && (
                    <RailButton
                      icon={<RefusalNavIcon />}
                      label="Test a refusal"
                      onClick={onTestRefusal}
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
                  if (event.key === 'Escape') closeSearch()
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
                onClick={closeSearch}
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
                  {item.refused && <span className="sidebar-copy rail-note">Refused</span>}
                  {item.restored && <span className="sidebar-copy rail-note">Last</span>}
                </button>
              ))}
              {history.length === 0 && (
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
          {datasetName !== null && (
            <p className="foot-dataset">
              {fictional ? 'Demo data' : 'Dataset'}: {datasetName}
            </p>
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
