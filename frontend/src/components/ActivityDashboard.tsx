import { useEffect, useState } from 'react'
import { BarChart2, Clock, Flame } from 'lucide-react'
import { activityApi, type AppTimeEntry, type FocusSession, type HeatmapCell } from '../api/client'
import { sessionLogger } from '../utils/sessionLogger'

function fmt(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  return `${(seconds / 3600).toFixed(1)}h`
}

function AppBar({ app, seconds, max }: { app: string; seconds: number; max: number }) {
  const pct = max > 0 ? (seconds / max) * 100 : 0
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium truncate max-w-[180px]" style={{ color: 'var(--text)' }} title={app}>
          {app}
        </span>
        <span style={{ color: 'var(--text-muted)' }}>{fmt(seconds)}</span>
      </div>
      <div className="h-2 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }}>
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, background: 'var(--accent)' }}
        />
      </div>
    </div>
  )
}

function HeatmapGrid({ cells }: { cells: HeatmapCell[] }) {
  const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
  const HOURS = Array.from({ length: 24 }, (_, i) => i)

  const maxCount = Math.max(...cells.map(c => c.count), 1)
  const cellMap = new Map<string, number>()
  cells.forEach(c => cellMap.set(`${c.weekday}-${c.hour}`, c.count))

  return (
    <div className="overflow-x-auto">
      <div className="flex gap-1 min-w-[640px]">
        {/* Y labels */}
        <div className="flex flex-col gap-1 pt-6 pr-1">
          {DAYS.map(d => (
            <div key={d} className="flex h-5 items-center text-xs" style={{ color: 'var(--text-muted)' }}>
              {d}
            </div>
          ))}
        </div>

        {/* Grid */}
        <div className="flex-1">
          {/* Hour labels */}
          <div className="flex gap-1 mb-1">
            {HOURS.map(h => (
              <div key={h} className="flex w-5 justify-center text-xs" style={{ color: 'var(--text-muted)' }}>
                {h % 6 === 0 ? h : ''}
              </div>
            ))}
          </div>
          {/* Rows */}
          {DAYS.map((_, wd) => (
            <div key={wd} className="flex gap-1 mb-1">
              {HOURS.map(h => {
                const count = cellMap.get(`${wd}-${h}`) ?? 0
                const intensity = count > 0 ? 0.15 + (count / maxCount) * 0.85 : 0
                return (
                  <div
                    key={h}
                    className="h-5 w-5 rounded-sm flex-shrink-0 cursor-default"
                    style={{
                      background: count > 0
                        ? `color-mix(in srgb, var(--accent) ${Math.round(intensity * 100)}%, var(--surface-2))`
                        : 'var(--surface-2)',
                    }}
                    title={count > 0 ? `${DAYS[wd]} ${h}:00 — ${count} captures` : undefined}
                  />
                )
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function Section({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border p-6 space-y-4" style={{ borderColor: 'var(--border)', background: 'var(--surface)' }}>
      <div className="flex items-center gap-2">
        <span style={{ color: 'var(--accent)' }}>{icon}</span>
        <h2 className="font-semibold" style={{ color: 'var(--text)' }}>{title}</h2>
      </div>
      {children}
    </div>
  )
}

export function ActivityDashboard() {
  const today = new Date().toISOString().split('T')[0]
  const weekAgo = new Date(Date.now() - 7 * 86400_000).toISOString().split('T')[0]

  const [apps, setApps] = useState<AppTimeEntry[]>([])
  const [sessions, setSessions] = useState<FocusSession[]>([])
  const [heatmap, setHeatmap] = useState<HeatmapCell[]>([])
  const [selectedDate, setSelectedDate] = useState(today)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    sessionLogger.log('activity', 'load')
    Promise.all([
      activityApi.apps(weekAgo, today).then(d => { setApps(d.totals); sessionLogger.log('activity', 'apps_loaded', { count: d.totals.length }) }),
      activityApi.heatmap(4).then(d => { setHeatmap(d.cells); sessionLogger.log('activity', 'heatmap_loaded', { cells: d.cells.length }) }),
    ]).finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    sessionLogger.log('activity', 'focus_date', { date: selectedDate })
    activityApi.focus(selectedDate).then(d => { setSessions(d.sessions); sessionLogger.log('activity', 'focus_loaded', { sessions: d.sessions.length }) }).catch(() => setSessions([]))
  }, [selectedDate])

  const maxSeconds = apps[0]?.seconds ?? 1

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold" style={{ color: 'var(--text)' }}>Activity</h1>
        <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
          How you've spent your time — computed entirely from local data
        </p>
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
      ) : (
        <>
          {/* App time */}
          <Section title="Time per app (last 7 days)" icon={<BarChart2 size={16} />}>
            {apps.length === 0 ? (
              <p className="text-sm text-center py-4" style={{ color: 'var(--text-muted)' }}>
                No data yet — captures are indexed every 2 minutes
              </p>
            ) : (
              <div className="space-y-3">
                {apps.slice(0, 12).map(a => (
                  <AppBar key={a.app} app={a.app} seconds={a.seconds} max={maxSeconds} />
                ))}
              </div>
            )}
          </Section>

          {/* Focus sessions */}
          <Section title="Focus sessions" icon={<Clock size={16} />}>
            <div className="flex items-center gap-3 mb-2">
              <label className="text-xs" style={{ color: 'var(--text-muted)' }}>Date</label>
              <input
                type="date"
                value={selectedDate}
                onChange={e => setSelectedDate(e.target.value)}
                className="rounded-lg border px-2 py-1 text-xs"
                style={{
                  borderColor: 'var(--border)',
                  background: 'var(--surface-2)',
                  color: 'var(--text)',
                }}
              />
            </div>
            {sessions.length === 0 ? (
              <p className="text-sm text-center py-4" style={{ color: 'var(--text-muted)' }}>
                No focus sessions ≥ 20 min on {selectedDate}
              </p>
            ) : (
              <div className="space-y-2">
                {sessions.map((s, i) => (
                  <div
                    key={i}
                    className="flex items-center gap-3 rounded-xl border px-4 py-3"
                    style={{ borderColor: 'var(--border)', background: 'var(--surface-2)' }}
                  >
                    <div
                      className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl text-xs font-bold"
                      style={{ background: 'color-mix(in srgb, var(--accent) 15%, transparent)', color: 'var(--accent)' }}
                    >
                      {Math.round(s.duration_minutes)}m
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="font-medium text-sm truncate" style={{ color: 'var(--text)' }}>
                        {s.app}
                      </p>
                      <p className="text-xs truncate" style={{ color: 'var(--text-muted)' }}>
                        {s.start.slice(11, 16)} – {s.end.slice(11, 16)}
                        {s.window_title ? ` · ${s.window_title}` : ''}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Section>

          {/* Activity heatmap */}
          <Section title="Activity heatmap (last 4 weeks)" icon={<Flame size={16} />}>
            {heatmap.length === 0 ? (
              <p className="text-sm text-center py-4" style={{ color: 'var(--text-muted)' }}>
                Not enough data yet
              </p>
            ) : (
              <HeatmapGrid cells={heatmap} />
            )}
          </Section>
        </>
      )}
    </div>
  )
}
