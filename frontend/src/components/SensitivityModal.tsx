import { motion, AnimatePresence } from 'framer-motion'
import { ShieldCheck, ShieldOff, Eye, X, AlertTriangle } from 'lucide-react'
import { useStore } from '../store/useStore'

interface Props {
  onConfirm: () => void
  onCancel: () => void
}

export function SensitivityModal({ onConfirm, onCancel }: Props) {
  const { sensitivityPreview } = useStore()

  return (
    <AnimatePresence>
      {sensitivityPreview && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50"
            style={{ background: 'rgba(0,0,0,0.75)', backdropFilter: 'blur(4px)' }}
            onClick={onCancel}
          />
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 16 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 16 }}
            transition={{ duration: 0.18 }}
            className="fixed inset-x-4 top-16 bottom-16 z-[60] mx-auto flex max-w-3xl flex-col overflow-hidden rounded-2xl border"
            style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}
          >
            {/* Header */}
            <div
              className="flex items-center gap-3 border-b px-6 py-4"
              style={{ borderColor: 'var(--border)' }}
            >
              <div className="flex h-9 w-9 items-center justify-center rounded-xl"
                style={{ background: 'color-mix(in srgb, #f59e0b 15%, transparent)' }}>
                <ShieldCheck size={18} style={{ color: '#f59e0b' }} />
              </div>
              <div className="flex-1">
                <p className="font-semibold" style={{ color: 'var(--text)' }}>
                  Privacy Review
                </p>
                <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  Review what will be sent to the AI before confirming
                </p>
              </div>
              <button
                onClick={onCancel}
                className="rounded-lg p-2 transition-colors hover:bg-[var(--surface-2)]"
                style={{ color: 'var(--text-muted)' }}
              >
                <X size={18} />
              </button>
            </div>

            {/* Stats bar */}
            <div
              className="flex items-center gap-6 border-b px-6 py-3 text-sm"
              style={{ borderColor: 'var(--border)', background: 'var(--surface-2)' }}
            >
              <div className="flex items-center gap-2">
                <ShieldCheck size={14} style={{ color: '#22c55e' }} />
                <span style={{ color: 'var(--text)' }}>
                  <strong>{sensitivityPreview.passing_count}</strong>
                  <span style={{ color: 'var(--text-muted)' }}> chunks passing</span>
                </span>
              </div>
              {sensitivityPreview.blocked_count > 0 && (
                <div className="flex items-center gap-2">
                  <ShieldOff size={14} style={{ color: '#ef4444' }} />
                  <span style={{ color: 'var(--text)' }}>
                    <strong>{sensitivityPreview.blocked_count}</strong>
                    <span style={{ color: 'var(--text-muted)' }}> blocked (sensitive)</span>
                  </span>
                </div>
              )}
              <div className="flex items-center gap-2">
                <Eye size={14} style={{ color: 'var(--text-muted)' }} />
                <span style={{ color: 'var(--text-muted)' }}>
                  ~{sensitivityPreview.estimated_tokens} tokens
                </span>
              </div>
              {sensitivityPreview.passing_count === 0 && (
                <div className="flex items-center gap-2 ml-auto">
                  <AlertTriangle size={14} style={{ color: '#f59e0b' }} />
                  <span style={{ color: '#f59e0b' }} className="text-xs">
                    All context blocked — answer may be limited
                  </span>
                </div>
              )}
            </div>

            {/* Entity map */}
            {Object.keys(sensitivityPreview.entity_map).length > 0 && (
              <div
                className="flex flex-wrap gap-2 border-b px-6 py-3"
                style={{ borderColor: 'var(--border)' }}
              >
                <span className="text-xs font-medium self-center" style={{ color: 'var(--text-muted)' }}>
                  Entities masked:
                </span>
                {Object.entries(sensitivityPreview.entity_map).map(([placeholder, real]) => (
                  <span
                    key={placeholder}
                    className="rounded-full px-2 py-0.5 text-xs font-mono"
                    style={{
                      background: 'color-mix(in srgb, var(--accent) 12%, transparent)',
                      color: 'var(--accent)',
                    }}
                    title={`"${real}" masked as ${placeholder}`}
                  >
                    {placeholder} = "{real}"
                  </span>
                ))}
              </div>
            )}

            {/* Prompt preview */}
            <div className="flex-1 overflow-y-auto px-6 py-4">
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>
                Exact text being sent to the AI
              </p>
              <pre
                className="rounded-xl border p-4 text-xs leading-relaxed whitespace-pre-wrap font-mono overflow-x-auto"
                style={{
                  borderColor: 'var(--border)',
                  background: 'var(--surface-2)',
                  color: 'var(--text)',
                }}
              >
                {sensitivityPreview.masked_prompt}
              </pre>
            </div>

            {/* Footer */}
            <div
              className="flex items-center justify-between border-t px-6 py-4 gap-3"
              style={{ borderColor: 'var(--border)' }}
            >
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                Your raw data stays local. Only the masked text above is transmitted.
              </p>
              <div className="flex gap-2 flex-shrink-0">
                <button
                  onClick={onCancel}
                  className="rounded-lg px-4 py-2 text-sm font-medium transition-colors"
                  style={{ background: 'var(--surface-2)', color: 'var(--text-muted)' }}
                >
                  Cancel
                </button>
                <button
                  onClick={onConfirm}
                  className="rounded-lg px-4 py-2 text-sm font-medium transition-colors"
                  style={{ background: 'var(--accent)', color: '#fff' }}
                >
                  Send to AI
                </button>
              </div>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
