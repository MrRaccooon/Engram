import { motion } from 'framer-motion'
import { Monitor, Clipboard, Globe, FileText, Mic } from 'lucide-react'
import type { CaptureResult } from '../api/client'
import { useStore } from '../store/useStore'

const SOURCE_META: Record<string, { icon: React.ReactNode; color: string; label: string }> = {
  screenshot: { icon: <Monitor size={12} />, color: '#7c6af7', label: 'Screen' },
  clipboard:  { icon: <Clipboard size={12} />, color: '#22d3ee', label: 'Clipboard' },
  url:        { icon: <Globe size={12} />, color: '#f59e0b', label: 'URL' },
  file:       { icon: <FileText size={12} />, color: '#4ade80', label: 'File' },
  audio:      { icon: <Mic size={12} />, color: '#f472b6', label: 'Audio' },
}

function formatTs(ts: string) {
  try {
    const d = new Date(ts + 'Z')
    const now = new Date()
    const diffMs = now.getTime() - d.getTime()
    const diffH = diffMs / 3600000
    if (diffH < 1) return `${Math.round(diffMs / 60000)}m ago`
    if (diffH < 24) return `${Math.round(diffH)}h ago`
    if (diffH < 168) return `${Math.round(diffH / 24)}d ago`
    return d.toLocaleDateString()
  } catch { return ts }
}

interface Props {
  result: CaptureResult
  index: number
}

export function ResultCard({ result, index }: Props) {
  const setSelectedResult = useStore(s => s.setSelectedResult)
  const meta = SOURCE_META[result.source_type] ?? SOURCE_META.file

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, delay: index * 0.04 }}
      onClick={() => setSelectedResult(result)}
      className="group cursor-pointer rounded-2xl border p-4 transition-all hover:border-[var(--accent)]"
      style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}
    >
      {/* Thumbnail */}
      {result.thumb_path && result.source_type === 'screenshot' ? (
        <div
          className="mb-3 overflow-hidden rounded-xl"
          style={{ height: 140, background: 'var(--surface-2)' }}
        >
          <img
            src={`/thumbs/${encodeURIComponent(result.thumb_path.replace(/\\/g, '/'))}`}
            alt="screenshot"
            className="h-full w-full object-cover object-top transition-transform group-hover:scale-105"
            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
          />
        </div>
      ) : (
        <div
          className="mb-3 flex items-center justify-center rounded-xl"
          style={{ height: 80, background: 'var(--surface-2)' }}
        >
          <span style={{ color: meta.color, opacity: 0.4, transform: 'scale(2.5)', display: 'block' }}>
            {meta.icon}
          </span>
        </div>
      )}

      {/* Source badge */}
      <div className="mb-2 flex items-center justify-between">
        <span
          className="flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium"
          style={{ background: `${meta.color}18`, color: meta.color }}
        >
          {meta.icon}
          {meta.label}
        </span>
        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
          {formatTs(result.timestamp)}
        </span>
      </div>

      {/* Content preview */}
      <p className="mb-2 line-clamp-3 text-sm leading-relaxed" style={{ color: 'var(--text)' }}>
        {result.content_preview || result.window_title || result.url || '—'}
      </p>

      {/* Footer */}
      <div className="flex items-center justify-between">
        <span className="truncate text-xs" style={{ color: 'var(--text-muted)', maxWidth: '60%' }}>
          {result.app_name || result.window_title || ''}
        </span>
        <span
          className="rounded-full px-2 py-0.5 text-xs font-semibold"
          style={{
            background: `color-mix(in srgb, var(--accent) 15%, transparent)`,
            color: 'var(--accent)',
          }}
        >
          {Math.round(result.relevance_score * 100)}%
        </span>
      </div>
    </motion.div>
  )
}

// ── Results grid wrapper ──────────────────────────────────────────────────────

export function ResultsGrid() {
  const { results, isSearching, query, queryTimeMs } = useStore()

  if (isSearching) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="flex gap-1">
          {[0, 1, 2].map(i => (
            <motion.div
              key={i}
              className="h-2 w-2 rounded-full"
              style={{ background: 'var(--accent)' }}
              animate={{ y: [0, -8, 0] }}
              transition={{ duration: 0.6, delay: i * 0.1, repeat: Infinity }}
            />
          ))}
        </div>
      </div>
    )
  }

  if (!query) return null

  if (results.length === 0) {
    return (
      <div className="flex flex-col items-center py-20 text-center">
        <p className="text-lg font-medium" style={{ color: 'var(--text)' }}>No memories found</p>
        <p className="mt-1 text-sm" style={{ color: 'var(--text-muted)' }}>
          Try a different query, or wait for more captures to be indexed.
        </p>
      </div>
    )
  }

  return (
    <div>
      <p className="mb-4 text-xs" style={{ color: 'var(--text-muted)' }}>
        {results.length} results · {queryTimeMs}ms
      </p>
      <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
        {results.map((r, i) => <ResultCard key={r.capture_id} result={r} index={i} />)}
      </div>
    </div>
  )
}
