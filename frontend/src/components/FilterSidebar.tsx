import { motion, AnimatePresence } from 'framer-motion'
import { X } from 'lucide-react'
import { useStore } from '../store/useStore'
import { sessionLogger } from '../utils/sessionLogger'

const SOURCE_OPTIONS = [
  { value: 'screenshot', label: 'Screenshots' },
  { value: 'clipboard',  label: 'Clipboard' },
  { value: 'url',        label: 'Browser URLs' },
  { value: 'file',       label: 'Documents' },
  { value: 'audio',      label: 'Audio' },
]

function Checkbox({ checked, onChange, label }: { checked: boolean; onChange: () => void; label: string }) {
  return (
    <label className="flex cursor-pointer items-center gap-3 rounded-lg px-2 py-1.5 transition-colors hover:bg-[var(--surface-2)]">
      <div
        className="flex h-4 w-4 flex-shrink-0 items-center justify-center rounded transition-colors"
        style={{
          background: checked ? 'var(--accent)' : 'transparent',
          border: `1.5px solid ${checked ? 'var(--accent)' : 'var(--border)'}`,
        }}
        onClick={onChange}
      >
        {checked && (
          <svg width="9" height="7" viewBox="0 0 9 7" fill="none">
            <path d="M1 3.5L3.5 6L8 1" stroke="white" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        )}
      </div>
      <span className="text-sm" style={{ color: 'var(--text)' }}>{label}</span>
    </label>
  )
}

export function FilterSidebar() {
  const { filtersOpen, setFiltersOpen, filters, setFilters } = useStore()

  const toggleSourceType = (val: string) => {
    const current = filters.source_types ?? []
    const toggling = current.includes(val) ? 'off' : 'on'
    const updated = toggling === 'off'
      ? current.filter(v => v !== val)
      : [...current, val]
    sessionLogger.log('filter', 'toggle_source', { source: val, toggling })
    setFilters({ ...filters, source_types: updated.length ? updated : undefined })
  }

  const reset = () => { sessionLogger.log('filter', 'reset'); setFilters({}) }

  return (
    <AnimatePresence>
      {filtersOpen && (
        <motion.aside
          initial={{ opacity: 0, x: 24 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: 24 }}
          transition={{ duration: 0.2 }}
          className="rounded-2xl border p-5 w-64 flex-shrink-0"
          style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}
        >
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-semibold text-sm" style={{ color: 'var(--text)' }}>Filters</h3>
            <button onClick={() => setFiltersOpen(false)} style={{ color: 'var(--text-muted)' }}>
              <X size={16} />
            </button>
          </div>

          {/* Date range */}
          <div className="mb-5">
            <p className="mb-2 text-xs font-medium uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>Date Range</p>
            <input
              type="date"
              value={filters.date_from ?? ''}
              onChange={e => setFilters({ ...filters, date_from: e.target.value || undefined })}
              className="mb-2 w-full rounded-lg border px-3 py-2 text-sm outline-none"
              style={{ background: 'var(--surface-2)', borderColor: 'var(--border)', color: 'var(--text)' }}
              placeholder="From"
            />
            <input
              type="date"
              value={filters.date_to ?? ''}
              onChange={e => setFilters({ ...filters, date_to: e.target.value || undefined })}
              className="w-full rounded-lg border px-3 py-2 text-sm outline-none"
              style={{ background: 'var(--surface-2)', borderColor: 'var(--border)', color: 'var(--text)' }}
              placeholder="To"
            />
          </div>

          {/* Source type */}
          <div className="mb-5">
            <p className="mb-2 text-xs font-medium uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>Source Type</p>
            {SOURCE_OPTIONS.map(opt => (
              <Checkbox
                key={opt.value}
                label={opt.label}
                checked={(filters.source_types ?? []).includes(opt.value)}
                onChange={() => toggleSourceType(opt.value)}
              />
            ))}
          </div>

          <button
            onClick={reset}
            className="w-full rounded-xl py-2 text-sm transition-colors"
            style={{ background: 'var(--surface-2)', color: 'var(--text-muted)' }}
          >
            Reset filters
          </button>
        </motion.aside>
      )}
    </AnimatePresence>
  )
}
