import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Brain, Search, Clock, Settings, Zap, Bot, BarChart2, Lightbulb, Sparkles } from 'lucide-react'
import { SearchBar } from './components/SearchBar'
import { ResultsGrid } from './components/ResultCard'
import { FilterSidebar } from './components/FilterSidebar'
import { TimelineView } from './components/TimelineView'
import { DetailModal } from './components/DetailModal'
import { SettingsPanel } from './components/Settings'
import { ChatView } from './components/ChatView'
import { ActivityDashboard } from './components/ActivityDashboard'
import { InsightsView } from './components/InsightsView'
import { LockScreen } from './components/LockScreen'
import { LearningView } from './components/LearningView'
import { useStore } from './store/useStore'
import { api, captureApi, authApi } from './api/client'
import { sessionLogger } from './utils/sessionLogger'

type NavItem = { id: 'search' | 'timeline' | 'chat' | 'activity' | 'insights' | 'learning' | 'settings'; label: string; icon: React.ReactNode }

const NAV: NavItem[] = [
  { id: 'search',   label: 'Search',   icon: <Search size={16} /> },
  { id: 'chat',     label: 'Ask',      icon: <Bot size={16} /> },
  { id: 'timeline', label: 'Timeline', icon: <Clock size={16} /> },
  { id: 'activity', label: 'Activity', icon: <BarChart2 size={16} /> },
  { id: 'insights', label: 'Insights', icon: <Lightbulb size={16} /> },
  { id: 'learning', label: 'Learning', icon: <Sparkles size={16} /> },
  { id: 'settings', label: 'Settings', icon: <Settings size={16} /> },
]

function StatusDot() {
  const { status, setStatus } = useStore()
  useEffect(() => {
    const refresh = () => captureApi.status().then(setStatus).catch(() => {})
    refresh()
    const id = setInterval(refresh, 10_000)
    return () => clearInterval(id)
  }, [setStatus])

  const pending = status?.pending_queue ?? 0
  return (
    <div className="flex items-center gap-2 text-xs tabular" style={{ color: 'var(--text-muted)' }}>
      <span
        className="h-2 w-2 rounded-full"
        style={{ background: status?.daemon_running ? '#22c55e' : '#ef4444' }}
      />
      {status ? (
        <>
          <span>{status.indexed_captures.toLocaleString()} indexed</span>
          {pending > 0 && <span style={{ color: 'var(--accent)' }}>· {pending} queued</span>}
          <span>· {status.storage_mb} MB</span>
        </>
      ) : (
        <span>Connecting…</span>
      )}
    </div>
  )
}

export default function App() {
  const { view, setView, filtersOpen } = useStore()
  const [locked, setLocked] = useState(false)
  const [authChecked, setAuthChecked] = useState(false)
  const [sessionToken, setSessionToken] = useState<string | null>(null)

  // Check auth status on mount
  useEffect(() => {
    authApi.status()
      .then(s => {
        if (s.auth_enabled && s.locked) setLocked(true)
      })
      .catch(() => {})
      .finally(() => setAuthChecked(true))
  }, [])

  // Attach session token to all API calls when available
  useEffect(() => {
    if (sessionToken) {
      api.defaults.headers.common['X-Engram-Session'] = sessionToken
    }
  }, [sessionToken])

  // Global keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key === 'k') { e.preventDefault(); setView('search'); sessionLogger.log('nav', 'shortcut', { key: 'Ctrl+K', view: 'search' }) }
      if (e.ctrlKey && e.key === 't') { e.preventDefault(); setView('timeline'); sessionLogger.log('nav', 'shortcut', { key: 'Ctrl+T', view: 'timeline' }) }
      if (e.ctrlKey && e.key === 'j') { e.preventDefault(); setView('chat'); sessionLogger.log('nav', 'shortcut', { key: 'Ctrl+J', view: 'chat' }) }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [setView])

  if (!authChecked) return null

  if (locked) {
    return (
      <LockScreen
        onUnlocked={token => {
          setSessionToken(token)
          setLocked(false)
        }}
      />
    )
  }

  return (
    <div className="flex h-[100dvh] flex-col" style={{ background: 'var(--bg)' }}>
      {/* Header */}
      <header
        className="flex items-center gap-4 border-b px-6 py-3 flex-shrink-0"
        style={{ borderColor: 'var(--border)', background: 'var(--surface)' }}
      >
        {/* Logo */}
        <div className="flex items-center gap-2 mr-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-xl" style={{ background: 'var(--accent)' }}>
            <Brain size={18} color="#fff" />
          </div>
          <span className="font-bold tracking-tight" style={{ color: 'var(--text)' }}>Engram</span>
        </div>

        {/* Nav */}
        <nav className="flex items-center gap-1">
          {NAV.map(item => (
            <button
              key={item.id}
              onClick={() => { setView(item.id); sessionLogger.log('nav', 'click', { view: item.id }) }}
              className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors"
              style={{
                background: view === item.id ? 'var(--surface-2)' : 'transparent',
                color: view === item.id ? 'var(--text)' : 'var(--text-muted)',
              }}
            >
              {item.icon}
              {item.label}
            </button>
          ))}
        </nav>

        <div className="flex-1" />

        {/* Manual capture button */}
        <button
          onClick={() => { sessionLogger.log('capture', 'manual_click'); captureApi.manual().catch(() => {}) }}
          title="Capture now (Ctrl+Shift+M)"
          className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 pl-3 pr-2.5 text-xs transition-colors"
          style={{ background: 'var(--surface-2)', color: 'var(--accent)' }}
        >
          <Zap size={12} />
          Capture
        </button>

        {/* Status */}
        <StatusDot />
      </header>

      {/* Main */}
      <main className="flex-1 overflow-hidden">
        <AnimatePresence mode="wait">
          <motion.div
            key={view}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.15 }}
          className="h-full overflow-y-auto"
          >
            {view === 'search' && (
              <div className="mx-auto max-w-6xl px-6 py-8">
                <div className="mb-8">
                  <SearchBar />
                </div>
                <div className="flex gap-6 items-start">
                  <div className="flex-1 min-w-0">
                    <ResultsGrid />
                  </div>
                  {filtersOpen && <FilterSidebar />}
                </div>
              </div>
            )}

            {view === 'chat' && <ChatView />}

            {view === 'timeline' && (
              <div className="mx-auto max-w-4xl px-6 py-8">
                <TimelineView />
              </div>
            )}

            {view === 'activity' && (
              <div className="mx-auto max-w-4xl px-6 py-8">
                <ActivityDashboard />
              </div>
            )}

            {view === 'insights' && (
              <div className="mx-auto max-w-3xl px-6 py-8">
                <InsightsView />
              </div>
            )}

            {view === 'learning' && (
              <div className="mx-auto max-w-5xl px-6 py-8">
                <LearningView />
              </div>
            )}

            {view === 'settings' && (
              <div className="mx-auto max-w-2xl px-6 py-8">
                <h1 className="mb-6 text-2xl font-bold" style={{ color: 'var(--text)' }}>Settings</h1>
                <SettingsPanel />
              </div>
            )}
          </motion.div>
        </AnimatePresence>
      </main>

      {/* Detail modal */}
      <DetailModal />

      {/* Keyboard shortcut hint */}
      <div
        className="flex items-center justify-center gap-4 border-t px-6 py-2 text-xs"
        style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
      >
        <span><kbd className="rounded px-1" style={{ background: 'var(--surface-2)' }}>Ctrl+K</kbd> Search</span>
        <span><kbd className="rounded px-1" style={{ background: 'var(--surface-2)' }}>Ctrl+J</kbd> Ask</span>
        <span><kbd className="rounded px-1" style={{ background: 'var(--surface-2)' }}>Ctrl+T</kbd> Timeline</span>
        <span><kbd className="rounded px-1" style={{ background: 'var(--surface-2)' }}>Esc</kbd> Close</span>
      </div>
    </div>
  )
}
