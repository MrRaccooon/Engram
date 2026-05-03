import { useRef, useCallback } from 'react'
import { Search, Sliders, Zap } from 'lucide-react'
import { useStore } from '../store/useStore'
import { searchApi } from '../api/client'
import { sessionLogger } from '../utils/sessionLogger'

export function SearchBar() {
  const {
    query, setQuery,
    filters,
    setResults, setIsSearching, setQueryTimeMs,
    filtersOpen, setFiltersOpen,
  } = useStore()

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const runSearch = useCallback(async (q: string) => {
    if (!q.trim()) { setResults([]); return }
    setIsSearching(true)
    sessionLogger.log('search', 'submit', { query: q, filters })
    try {
      const res = await searchApi.search(q, filters, 10)
      setResults(res.results)
      setQueryTimeMs(res.query_time_ms)
      sessionLogger.log('search', 'results', { count: res.results.length, candidates: res.total_candidates }, res.query_time_ms)
    } catch {
      setResults([])
      sessionLogger.log('search', 'error')
    } finally {
      setIsSearching(false)
    }
  }, [filters, setResults, setIsSearching, setQueryTimeMs])

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value
    setQuery(val)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => runSearch(val), 400)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      if (debounceRef.current) clearTimeout(debounceRef.current)
      runSearch(query)
    }
  }

  return (
    <div className="relative w-full max-w-3xl mx-auto">
      <div
        className="engram-card flex items-center gap-3 rounded-[1.35rem] px-5 py-4 transition-[box-shadow]"
        style={{
          background: 'var(--surface)',
          boxShadow: '0 0 0 0 var(--accent)',
        }}
        onFocus={(e) => {
          const el = e.currentTarget as HTMLDivElement
          el.style.borderColor = 'var(--accent)'
          el.style.boxShadow = '0 0 0 2px color-mix(in srgb, var(--accent) 20%, transparent)'
        }}
        onBlur={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node)) {
            const el = e.currentTarget as HTMLDivElement
            el.style.borderColor = 'var(--border)'
            el.style.boxShadow = 'none'
          }
        }}
      >
        <Search size={20} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
        <input
          type="text"
          value={query}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder="What are you looking for? Try: 'that React article I read last week'"
          className="flex-1 bg-transparent text-base outline-none placeholder:text-[var(--text-muted)]"
          style={{ color: 'var(--text)' }}
          autoFocus
        />
        <button
          onClick={() => setFiltersOpen(!filtersOpen)}
          title="Filters"
          className="rounded-lg p-2 transition-colors hover:bg-[var(--surface-2)]"
          style={{ color: filtersOpen ? 'var(--accent)' : 'var(--text-muted)' }}
        >
          <Sliders size={18} />
        </button>
        <button
          onClick={() => runSearch(query)}
          title="Search (Enter)"
          className="flex items-center gap-2 rounded-xl px-4 py-2 pl-4 pr-3.5 text-sm font-medium transition-colors"
          style={{ background: 'var(--accent)', color: '#fff' }}
        >
          <Zap size={14} />
          Search
        </button>
      </div>
    </div>
  )
}
