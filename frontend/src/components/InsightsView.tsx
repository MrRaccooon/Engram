import { useEffect, useState } from 'react'
import { Lightbulb, ChevronDown, ChevronRight, Calendar } from 'lucide-react'
import { insightsApi, type InsightEntry } from '../api/client'
import { sessionLogger } from '../utils/sessionLogger'

function TopicPill({ topic }: { topic: string }) {
  return (
    <span
      className="rounded-full px-2 py-0.5 text-xs font-medium"
      style={{
        background: 'color-mix(in srgb, var(--accent) 12%, transparent)',
        color: 'var(--accent)',
      }}
    >
      {topic}
    </span>
  )
}

function InsightCard({ insight }: { insight: InsightEntry }) {
  const [expanded, setExpanded] = useState(false)

  let topics: string[] = []
  try { topics = JSON.parse(insight.topics || '[]') } catch { /* ignore */ }

  const startTime = insight.session_start.slice(11, 16)
  const endTime = insight.session_end.slice(11, 16)

  return (
    <div
      className="rounded-2xl border overflow-hidden transition-all"
      style={{ borderColor: 'var(--border)', background: 'var(--surface)' }}
    >
      <button
        className="flex w-full items-start gap-4 px-5 py-4 text-left transition-colors hover:bg-[var(--surface-2)]"
        onClick={() => { setExpanded(e => !e); sessionLogger.log('insights', 'toggle_card', { id: insight.id }) }}
      >
        {/* Time indicator */}
        <div
          className="flex flex-col items-center flex-shrink-0 pt-0.5"
          style={{ color: 'var(--text-muted)', minWidth: '3.5rem' }}
        >
          <span className="text-xs font-mono">{startTime}</span>
          <div className="h-4 w-px my-0.5" style={{ background: 'var(--border)' }} />
          <span className="text-xs font-mono">{endTime}</span>
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <p className="text-sm leading-relaxed" style={{ color: 'var(--text)' }}>
            {insight.summary}
          </p>
          {topics.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {topics.slice(0, 5).map(t => <TopicPill key={t} topic={t} />)}
            </div>
          )}
        </div>

        {/* Expand toggle */}
        <div className="flex-shrink-0 pt-0.5" style={{ color: 'var(--text-muted)' }}>
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </div>
      </button>

      {expanded && (
        <div
          className="border-t px-5 py-4"
          style={{ borderColor: 'var(--border)', background: 'var(--surface-2)' }}
        >
          <p className="text-xs mb-2" style={{ color: 'var(--text-muted)' }}>
            Session: {insight.session_start.slice(0, 19).replace('T', ' ')} →{' '}
            {insight.session_end.slice(11, 19)}
          </p>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            Consolidated: {new Date(insight.consolidated_at + 'Z').toLocaleString()}
          </p>
          {topics.length > 0 && (
            <div className="mt-3">
              <p className="text-xs font-medium mb-1.5" style={{ color: 'var(--text-muted)' }}>
                All topics
              </p>
              <div className="flex flex-wrap gap-1.5">
                {topics.map(t => <TopicPill key={t} topic={t} />)}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function DayGroup({ date, insights }: { date: string; insights: InsightEntry[] }) {
  const label = (() => {
    try {
      return new Date(date + 'T00:00:00').toLocaleDateString(undefined, {
        weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
      })
    } catch { return date }
  })()

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <Calendar size={14} style={{ color: 'var(--text-muted)' }} />
        <h3 className="text-sm font-semibold" style={{ color: 'var(--text-muted)' }}>{label}</h3>
        <div className="flex-1 h-px" style={{ background: 'var(--border)' }} />
        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{insights.length} session{insights.length !== 1 ? 's' : ''}</span>
      </div>
      {insights.map(i => <InsightCard key={i.id} insight={i} />)}
    </div>
  )
}

export function InsightsView() {
  const [insights, setInsights] = useState<InsightEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedDate, setSelectedDate] = useState('')

  const load = (date?: string) => {
    setLoading(true)
    sessionLogger.log('insights', 'load', { date: date ?? 'last_7_days' })
    insightsApi.list(date || undefined)
      .then(d => { setInsights(d.insights); sessionLogger.log('insights', 'loaded', { count: d.insights.length }) })
      .catch(() => setInsights([]))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const handleDateChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const d = e.target.value
    setSelectedDate(d)
    load(d || undefined)
  }

  // Group by date
  const grouped = insights.reduce<Record<string, InsightEntry[]>>((acc, i) => {
    if (!acc[i.date]) acc[i.date] = []
    acc[i.date].push(i)
    return acc
  }, {})
  const sortedDates = Object.keys(grouped).sort((a, b) => b.localeCompare(a))

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold" style={{ color: 'var(--text)' }}>Insights</h1>
          <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
            Nightly summaries of your digital sessions — synthesized locally
          </p>
        </div>
        <input
          type="date"
          value={selectedDate}
          onChange={handleDateChange}
          className="rounded-xl border px-3 py-2 text-sm"
          style={{
            borderColor: 'var(--border)',
            background: 'var(--surface)',
            color: 'var(--text)',
          }}
          placeholder="Filter by date"
        />
      </div>

      {loading ? (
        <div className="flex justify-center py-16">
          <div className="flex gap-1">
            {[0, 1, 2].map(i => (
              <div
                key={i}
                className="h-2 w-2 rounded-full"
                style={{
                  background: 'var(--accent)',
                  animation: `pulse 1.2s ${i * 0.2}s ease-in-out infinite`,
                }}
              />
            ))}
          </div>
        </div>
      ) : insights.length === 0 ? (
        <div
          className="flex flex-col items-center gap-4 rounded-2xl border p-12 text-center"
          style={{ borderColor: 'var(--border)', background: 'var(--surface)' }}
        >
          <div
            className="flex h-16 w-16 items-center justify-center rounded-2xl"
            style={{ background: 'var(--surface-2)' }}
          >
            <Lightbulb size={32} style={{ color: 'var(--text-muted)' }} />
          </div>
          <div>
            <p className="font-semibold" style={{ color: 'var(--text)' }}>No insights yet</p>
            <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
              The nightly consolidation worker runs at 2 AM and generates<br />
              summaries of your day's sessions. Check back tomorrow.
            </p>
          </div>
        </div>
      ) : (
        <div className="space-y-8">
          {sortedDates.map(date => (
            <DayGroup key={date} date={date} insights={grouped[date]} />
          ))}
        </div>
      )}
    </div>
  )
}
