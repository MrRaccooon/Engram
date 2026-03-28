import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X, Clock, Monitor, Clipboard, Globe, FileText, Mic, ExternalLink } from 'lucide-react'
import { captureApi, type ContextResponse } from '../api/client'
import { useStore } from '../store/useStore'

const SOURCE_ICONS: Record<string, React.ReactNode> = {
  screenshot: <Monitor size={14} />,
  clipboard:  <Clipboard size={14} />,
  url:        <Globe size={14} />,
  file:       <FileText size={14} />,
  audio:      <Mic size={14} />,
}

const SOURCE_COLORS: Record<string, string> = {
  screenshot: '#7c6af7',
  clipboard:  '#22d3ee',
  url:        '#f59e0b',
  file:       '#4ade80',
  audio:      '#f472b6',
}

function formatFull(ts: string) {
  try { return new Date(ts + 'Z').toLocaleString() }
  catch { return ts }
}

export function DetailModal() {
  const { selectedResult, setSelectedResult } = useStore()
  const [context, setContext] = useState<ContextResponse | null>(null)
  const [loadingCtx, setLoadingCtx] = useState(false)

  useEffect(() => {
    if (!selectedResult) { setContext(null); return }
    setLoadingCtx(true)
    captureApi.context(selectedResult.capture_id, 5)
      .then(setContext)
      .catch(() => setContext(null))
      .finally(() => setLoadingCtx(false))
  }, [selectedResult])

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') setSelectedResult(null) }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [setSelectedResult])

  return (
    <AnimatePresence>
      {selectedResult && (
        <>
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-40"
            style={{ background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)' }}
            onClick={() => setSelectedResult(null)}
          />

          {/* Panel */}
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-x-4 top-12 bottom-4 z-50 mx-auto flex max-w-4xl flex-col overflow-hidden rounded-2xl border"
            style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}
          >
            {/* Header */}
            <div className="flex items-center gap-3 border-b px-6 py-4" style={{ borderColor: 'var(--border)' }}>
              <span style={{ color: SOURCE_COLORS[selectedResult.source_type] }}>
                {SOURCE_ICONS[selectedResult.source_type]}
              </span>
              <div className="flex-1 min-w-0">
                <p className="font-semibold truncate" style={{ color: 'var(--text)' }}>
                  {selectedResult.window_title || selectedResult.app_name || selectedResult.source_type}
                </p>
                <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  {formatFull(selectedResult.timestamp)} · {Math.round(selectedResult.relevance_score * 100)}% match
                </p>
              </div>
              <button
                onClick={() => setSelectedResult(null)}
                className="rounded-lg p-2 transition-colors hover:bg-[var(--surface-2)]"
                style={{ color: 'var(--text-muted)' }}
              >
                <X size={18} />
              </button>
            </div>

            {/* Body */}
            <div className="flex flex-1 overflow-hidden">
              {/* Main content */}
              <div className="flex-1 overflow-y-auto p-6 space-y-4">
                {/* Screenshot */}
                {selectedResult.thumb_path && selectedResult.source_type === 'screenshot' && (
                  <div className="overflow-hidden rounded-xl border" style={{ borderColor: 'var(--border)' }}>
                    <img
                      src={`/thumbs/${encodeURIComponent(selectedResult.thumb_path.replace(/\\/g, '/'))}`}
                      alt="screenshot"
                      className="w-full"
                    />
                  </div>
                )}

                {/* URL */}
                {selectedResult.url && (
                  <a
                    href={selectedResult.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-2 text-sm transition-colors hover:underline"
                    style={{ color: 'var(--accent)' }}
                  >
                    <ExternalLink size={14} />
                    {selectedResult.url.length > 80 ? selectedResult.url.slice(0, 80) + '…' : selectedResult.url}
                  </a>
                )}

                {/* Content */}
                {selectedResult.content_preview && (
                  <div>
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>
                      Extracted text
                    </p>
                    <p className="text-sm leading-relaxed whitespace-pre-wrap" style={{ color: 'var(--text)' }}>
                      {selectedResult.content_preview}
                    </p>
                  </div>
                )}
              </div>

              {/* Context sidebar */}
              <div
                className="w-64 flex-shrink-0 overflow-y-auto border-l p-4"
                style={{ borderColor: 'var(--border)', background: 'var(--surface-2)' }}
              >
                <div className="flex items-center gap-2 mb-3">
                  <Clock size={14} style={{ color: 'var(--text-muted)' }} />
                  <p className="text-xs font-semibold uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>
                    Context ±5 min
                  </p>
                </div>

                {loadingCtx ? (
                  <p className="text-xs text-center py-4" style={{ color: 'var(--text-muted)' }}>Loading…</p>
                ) : context ? (
                  <div className="space-y-1">
                    {context.context.map(c => (
                      <div
                        key={c.capture_id}
                        className="flex items-center gap-2 rounded-lg px-2 py-2 text-xs transition-colors"
                        style={{
                          background: c.is_center ? 'color-mix(in srgb, var(--accent) 15%, transparent)' : 'transparent',
                          borderLeft: c.is_center ? '2px solid var(--accent)' : '2px solid transparent',
                        }}
                      >
                        <span style={{ color: SOURCE_COLORS[c.source_type] ?? 'var(--text-muted)', flexShrink: 0 }}>
                          {SOURCE_ICONS[c.source_type]}
                        </span>
                        <span className="flex-1 truncate" style={{ color: c.is_center ? 'var(--text)' : 'var(--text-muted)' }}>
                          {c.window_title || c.app_name || c.source_type}
                        </span>
                        <span style={{ color: 'var(--text-muted)', flexShrink: 0 }}>
                          {new Date(c.timestamp + 'Z').toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-center py-4" style={{ color: 'var(--text-muted)' }}>No context available</p>
                )}
              </div>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
