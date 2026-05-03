import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { ChevronLeft, ChevronRight, Monitor, Clipboard, Globe, FileText, Mic } from 'lucide-react'
import { searchApi, type TimelineCapture } from '../api/client'
import { useStore } from '../store/useStore'
import { sessionLogger } from '../utils/sessionLogger'
import { ConceptPills, EventPills } from './MemorySignals'

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

function formatTime(ts: string) {
  try { return new Date(ts + 'Z').toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }
  catch { return ts }
}

function primaryText(c: TimelineCapture) {
  return c.window_title || c.app_name || c.source_type
}

function groupByHour(captures: TimelineCapture[]) {
  const groups: Record<number, TimelineCapture[]> = {}
  for (const c of captures) {
    const h = new Date(c.timestamp + 'Z').getHours()
    ;(groups[h] ??= []).push(c)
  }
  return groups
}

// Simple 7-day mini calendar
function MiniCalendar({ selected, onSelect }: { selected: string; onSelect: (d: string) => void }) {
  const today = new Date()
  const days = Array.from({ length: 14 }, (_, i) => {
    const d = new Date(today)
    d.setDate(today.getDate() - 13 + i)
    return d.toISOString().split('T')[0]
  })

  return (
    <div className="mb-4 flex flex-wrap gap-1">
      {days.map(day => {
        const isSelected = day === selected
        const isToday = day === today.toISOString().split('T')[0]
        return (
          <button
            key={day}
            onClick={() => onSelect(day)}
            title={day}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-xs transition-colors"
            style={{
              background: isSelected ? 'var(--accent)' : 'var(--surface-2)',
              color: isSelected ? '#fff' : isToday ? 'var(--accent)' : 'var(--text-muted)',
              fontWeight: isToday || isSelected ? '600' : '400',
            }}
          >
            {new Date(day + 'T12:00:00').getDate()}
          </button>
        )
      })}
    </div>
  )
}

export function TimelineView() {
  const { timelineDate, setTimelineDate, setSelectedResult } = useStore()
  const [captures, setCaptures] = useState<TimelineCapture[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true)
    sessionLogger.log('timeline', 'load', { date: timelineDate })
    searchApi.timeline(timelineDate)
      .then(r => { setCaptures(r.captures); sessionLogger.log('timeline', 'loaded', { date: timelineDate, count: r.captures.length }) })
      .catch(() => setCaptures([]))
      .finally(() => setLoading(false))
  }, [timelineDate])

  const prevDay = () => {
    const d = new Date(timelineDate + 'T12:00:00')
    d.setDate(d.getDate() - 1)
    const ds = d.toISOString().split('T')[0]
    sessionLogger.log('timeline', 'prev_day', { date: ds })
    setTimelineDate(ds)
  }
  const nextDay = () => {
    const d = new Date(timelineDate + 'T12:00:00')
    d.setDate(d.getDate() + 1)
    const ds = d.toISOString().split('T')[0]
    sessionLogger.log('timeline', 'next_day', { date: ds })
    setTimelineDate(ds)
  }

  const hourGroups = groupByHour(captures)
  const hours = Object.keys(hourGroups).map(Number).sort((a, b) => a - b)

  return (
    <div>
      {/* Date navigation */}
      <div className="mb-6 flex items-center gap-4">
        <button onClick={prevDay} className="rounded-lg p-2 transition-colors hover:bg-[var(--surface-2)]" style={{ color: 'var(--text-muted)' }}>
          <ChevronLeft size={18} />
        </button>
        <div className="flex-1">
          <h2 className="text-lg font-semibold" style={{ color: 'var(--text)' }}>
            {new Date(timelineDate + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}
          </h2>
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{captures.length} captures</p>
        </div>
        <button onClick={nextDay} className="rounded-lg p-2 transition-colors hover:bg-[var(--surface-2)]" style={{ color: 'var(--text-muted)' }}>
          <ChevronRight size={18} />
        </button>
      </div>

      <MiniCalendar selected={timelineDate} onSelect={setTimelineDate} />

      {loading ? (
        <div className="space-y-4 py-8">
          {[0, 1, 2].map(i => (
            <div key={i} className="grid gap-4 sm:grid-cols-[72px_1fr]">
              <div className="space-y-2 pt-1">
                <div className="skeleton h-4 w-12 rounded" />
                <div className="skeleton h-3 w-16 rounded" />
              </div>
              <div className="skeleton h-24 rounded-2xl" />
            </div>
          ))}
        </div>
      ) : hours.length === 0 ? (
        <div className="flex flex-col items-center py-20 text-center">
          <p style={{ color: 'var(--text-muted)' }}>No captures for this day.</p>
        </div>
      ) : (
        <div className="space-y-8">
          {hours.map(hour => (
            <section key={hour} className="grid gap-4 sm:grid-cols-[72px_1fr]">
              <div className="pt-1">
                <p className="text-sm font-semibold tabular" style={{ color: 'var(--text)' }}>
                  {String(hour).padStart(2, '0')}:00
                </p>
                <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  {hourGroups[hour].length} capture{hourGroups[hour].length !== 1 ? 's' : ''}
                </p>
              </div>
              <div className="relative space-y-3 border-l pl-4" style={{ borderColor: 'var(--border)' }}>
                {hourGroups[hour].map((c, i) => (
                  <motion.div
                    key={c.capture_id}
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: i * 0.03 }}
                    onClick={() => {
                      sessionLogger.log('timeline', 'open_capture', { captureId: c.capture_id })
                      setSelectedResult({ ...c, relevance_score: 1, chunk_index: 0 })
                    }}
                    className="engram-card engram-card-hover relative cursor-pointer rounded-[1.35rem] p-3 transition-transform hover:-translate-y-0.5"
                    style={{ background: 'var(--surface)' }}
                    title={c.content_preview || c.window_title}
                  >
                    <span
                      className="absolute -left-[21px] top-5 h-2.5 w-2.5 rounded-full border"
                      style={{ background: SOURCE_COLORS[c.source_type] ?? 'var(--accent)', borderColor: 'var(--surface)' }}
                    />
                    <div className="flex gap-3">
                      {c.thumb_path && c.source_type === 'screenshot' ? (
                        <div className="hidden h-16 w-24 flex-shrink-0 overflow-hidden rounded-xl sm:block" style={{ background: 'var(--surface-2)' }}>
                          <img
                            src={`/thumbs/${encodeURIComponent(c.thumb_path.replace(/\\/g, '/'))}`}
                            alt=""
                            className="h-full w-full object-cover object-top"
                            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                          />
                        </div>
                      ) : null}
                      <div className="min-w-0 flex-1">
                        <div className="mb-1 flex items-center gap-2">
                          <span style={{ color: SOURCE_COLORS[c.source_type] ?? 'var(--text-muted)' }}>
                            {SOURCE_ICONS[c.source_type] ?? <FileText size={14} />}
                          </span>
                          <p className="min-w-0 flex-1 truncate text-sm font-medium" style={{ color: 'var(--text)' }}>
                            {primaryText(c)}
                          </p>
                          <span className="text-xs tabular" style={{ color: 'var(--text-muted)' }}>
                            {formatTime(c.timestamp)}
                          </span>
                        </div>
                        {c.content_preview && (
                          <p className="line-clamp-2 text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                            {c.content_preview}
                          </p>
                        )}
                        {(c.concepts?.length || c.events?.length) ? (
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            <ConceptPills concepts={c.concepts} limit={2} />
                            <EventPills events={c.events} limit={1} />
                          </div>
                        ) : null}
                      </div>
                    </div>
                  </motion.div>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  )
}
