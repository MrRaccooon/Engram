import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { ChevronLeft, ChevronRight, Monitor, Clipboard, Globe, FileText, Mic } from 'lucide-react'
import { searchApi, type TimelineCapture } from '../api/client'
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

function formatTime(ts: string) {
  try { return new Date(ts + 'Z').toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }
  catch { return ts }
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
  const { timelineDate, setTimelineDate } = useStore()
  const [captures, setCaptures] = useState<TimelineCapture[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    searchApi.timeline(timelineDate)
      .then(r => setCaptures(r.captures))
      .catch(() => setCaptures([]))
      .finally(() => setLoading(false))
  }, [timelineDate])

  const prevDay = () => {
    const d = new Date(timelineDate + 'T12:00:00')
    d.setDate(d.getDate() - 1)
    setTimelineDate(d.toISOString().split('T')[0])
  }
  const nextDay = () => {
    const d = new Date(timelineDate + 'T12:00:00')
    d.setDate(d.getDate() + 1)
    setTimelineDate(d.toISOString().split('T')[0])
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
        <div className="flex justify-center py-20">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent" />
        </div>
      ) : hours.length === 0 ? (
        <div className="flex flex-col items-center py-20 text-center">
          <p style={{ color: 'var(--text-muted)' }}>No captures for this day.</p>
        </div>
      ) : (
        <div className="space-y-6">
          {hours.map(hour => (
            <div key={hour}>
              <div className="mb-2 flex items-center gap-3">
                <span className="text-xs font-semibold tabular-nums" style={{ color: 'var(--text-muted)', minWidth: 40 }}>
                  {String(hour).padStart(2, '0')}:00
                </span>
                <div className="h-px flex-1" style={{ background: 'var(--border)' }} />
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{hourGroups[hour].length}</span>
              </div>
              <div className="flex flex-wrap gap-2 pl-10">
                {hourGroups[hour].map((c, i) => (
                  <motion.div
                    key={c.capture_id}
                    initial={{ opacity: 0, scale: 0.9 }}
                    animate={{ opacity: 1, scale: 1 }}
                    transition={{ delay: i * 0.03 }}
                    className="flex items-center gap-2 rounded-xl border px-3 py-2 text-sm cursor-default"
                    style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}
                    title={c.content_preview || c.window_title}
                  >
                    <span style={{ color: SOURCE_COLORS[c.source_type] ?? 'var(--text-muted)' }}>
                      {SOURCE_ICONS[c.source_type] ?? <FileText size={14} />}
                    </span>
                    <span className="max-w-[140px] truncate" style={{ color: 'var(--text)' }}>
                      {c.window_title || c.app_name || c.source_type}
                    </span>
                    <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                      {formatTime(c.timestamp)}
                    </span>
                  </motion.div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
