import { useState } from 'react'

import { canSeeInstitution, roleDisplayName, type Session } from '../auth'
import { currentTheme, setTheme, type Theme } from '../theme'
import {
  ChatIcon,
  CloseIcon,
  DocumentIcon,
  LogIcon,
  MoonIcon,
  PlusIcon,
  SettingsIcon,
  SunIcon,
  TeamIcon,
} from './icons'

export type PanelId = 'briefing' | 'agents' | 'audit'

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
  open: boolean
  onNewQuestion: (() => void) | null
  onSelectHistory: (id: number) => void
  onOpenPanel: (panel: PanelId) => void
  onNavigate: (path: string) => void
  onSignOut: () => void
  onClose: () => void
}

const PANEL_LABELS: Record<PanelId, string> = {
  briefing: 'Full briefing',
  agents: 'AI employees',
  audit: 'Audit log',
}

const PANEL_ICONS: Record<PanelId, () => React.JSX.Element> = {
  briefing: DocumentIcon,
  agents: TeamIcon,
  audit: LogIcon,
}

/**
 * The chat sidebar, LibreChat style: the brand and a New question button on
 * top, this session's questions as the history list, one-click panels for
 * everything that is not the conversation, and the account (theme switch,
 * Institution for an admin, sign out) pinned to the bottom. Below 900 px it
 * is an off-canvas drawer opened from the top bar.
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
  onNavigate,
  onSignOut,
  onClose,
}: ChatSidebarProps) {
  const [theme, setThemeState] = useState<Theme>(currentTheme)
  const nextTheme: Theme = theme === 'dark' ? 'light' : 'dark'
  const initial = session.user.email.trim().charAt(0).toUpperCase()
  const institutionName = session.user.institution?.name ?? null

  return (
    <aside className={`chat-sidebar${open ? ' open' : ''}`} aria-label="Cabinet sidebar">
      <div className="sidebar-top">
        <div className="sidebar-brand">
          <span className="brand-mark" aria-hidden="true">
            GE
          </span>
          <span className="brand-text">
            <span className="brand-name">Golden Eagle AI Cabinet</span>
            {institutionName !== null && (
              <span className="brand-sub">{institutionName}</span>
            )}
          </span>
          <button
            type="button"
            className="icon-button sidebar-close"
            aria-label="Close sidebar"
            onClick={onClose}
          >
            <CloseIcon />
          </button>
        </div>
        {onNewQuestion !== null && (
          <button type="button" className="sidebar-new" onClick={onNewQuestion}>
            <PlusIcon />
            New question
          </button>
        )}
      </div>

      <nav className="sidebar-scroll" aria-label="Cabinet">
        <p className="sidebar-label">This session</p>
        {history.length === 0 ? (
          <p className="sidebar-empty">Questions you ask appear here.</p>
        ) : (
          <ul className="sidebar-list">
            {history.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="sidebar-item"
                  onClick={() => onSelectHistory(item.id)}
                  title={item.question}
                >
                  <ChatIcon />
                  <span className="sidebar-item-text">{item.question}</span>
                  {item.refused && <span className="sidebar-tag">Refused</span>}
                  {item.restored && <span className="sidebar-tag">Last</span>}
                </button>
              </li>
            ))}
          </ul>
        )}

        <p className="sidebar-label">Open</p>
        <ul className="sidebar-list">
          {panels.map((panel) => {
            const Icon = PANEL_ICONS[panel]
            return (
              <li key={panel}>
                <button
                  type="button"
                  className="sidebar-item"
                  aria-pressed={activePanel === panel}
                  onClick={() => onOpenPanel(panel)}
                >
                  <Icon />
                  <span className="sidebar-item-text">{PANEL_LABELS[panel]}</span>
                </button>
              </li>
            )
          })}
          {canSeeInstitution(session.user.role) && (
            <li>
              <button
                type="button"
                className="sidebar-item"
                onClick={() => onNavigate('/institution')}
              >
                <SettingsIcon />
                <span className="sidebar-item-text">Institution settings</span>
              </button>
            </li>
          )}
        </ul>

        {datasetName !== null && (
          <p className="sidebar-dataset">
            Dataset: {datasetName}
            {fictional && <span className="sidebar-tag">Demo data</span>}
          </p>
        )}
      </nav>

      <div className="sidebar-account">
        <span className="account-avatar" aria-hidden="true">
          {initial}
        </span>
        <span className="account-text">
          <span className="account-email">{session.user.email}</span>
          <span className="account-role">{roleDisplayName(session.user.role)}</span>
        </span>
        <button
          type="button"
          className="icon-button"
          aria-label={`Switch to ${nextTheme} theme`}
          title={`Switch to ${nextTheme} theme`}
          onClick={() => {
            setTheme(nextTheme)
            setThemeState(nextTheme)
          }}
        >
          {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
        </button>
        <button type="button" className="account-signout" onClick={onSignOut}>
          Sign out
        </button>
      </div>
    </aside>
  )
}
