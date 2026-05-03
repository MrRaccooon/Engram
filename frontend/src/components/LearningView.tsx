import { useEffect, useState } from 'react'
import { Brain, Clock, FileText, FolderGit2, Lightbulb } from 'lucide-react'
import { insightsApi, type LearningTopic } from '../api/client'
import { sessionLogger } from '../utils/sessionLogger'

function parseList(raw?: string | null): string[] {
  if (!raw) return []
  try {
    const value = JSON.parse(raw)
    if (Array.isArray(value)) return value.map(String).filter(Boolean)
  } catch { /* ignore */ }
  return raw.split('\n').map(s => s.replace(/^[-*]\s*/, '').trim()).filter(Boolean)
}

function minutesLabel(minutes: number) {
  if (minutes < 60) return `${Math.round(minutes)}m`
  return `${(minutes / 60).toFixed(1)}h`
}

function MiniList({ icon, title, items }: { icon: React.ReactNode; title: string; items: string[] }) {
  if (items.length === 0) return null
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium" style={{ color: 'var(--text-muted)' }}>
        {icon}
        {title}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {items.slice(0, 4).map(item => (
          <span
            key={item}
            className="rounded-full px-2 py-0.5 text-xs"
            style={{ background: 'var(--surface-2)', color: 'var(--text)' }}
          >
            {item}
          </span>
        ))}
      </div>
    </div>
  )
}

function LearningCard({ topic }: { topic: LearningTopic }) {
  const projects = parseList(topic.projects)
  const files = parseList(topic.files_touched)
  const decisions = parseList(topic.decisions)

  return (
    <article className="engram-card engram-card-hover rounded-[1.35rem] p-5" style={{ background: 'var(--surface)' }}>
      <div className="mb-3 flex items-start gap-3">
        <div
          className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl"
          style={{ background: 'color-mix(in srgb, var(--accent) 14%, transparent)', color: 'var(--accent)' }}
        >
          <Lightbulb size={18} />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-base font-semibold" style={{ color: 'var(--text)' }}>{topic.topic}</h2>
          <div className="mt-1 flex flex-wrap gap-3 text-xs tabular" style={{ color: 'var(--text-muted)' }}>
            <span>{topic.total_sessions} session{topic.total_sessions !== 1 ? 's' : ''}</span>
            <span>{minutesLabel(topic.total_minutes)}</span>
            <span>Updated {new Date(topic.last_updated + 'Z').toLocaleDateString()}</span>
          </div>
        </div>
      </div>

      <p className="mb-4 text-sm leading-relaxed text-pretty" style={{ color: 'var(--text)' }}>
        {topic.summary || 'Engram has seen this topic, but has not built a strong summary yet.'}
      </p>

      <div className="space-y-3">
        <MiniList icon={<FolderGit2 size={12} />} title="Projects" items={projects} />
        <MiniList icon={<FileText size={12} />} title="Files" items={files} />
        <MiniList icon={<Brain size={12} />} title="Decisions" items={decisions} />
      </div>
    </article>
  )
}

export function LearningView() {
  const [topics, setTopics] = useState<LearningTopic[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    sessionLogger.log('learning', 'load')
    insightsApi.learning()
      .then(d => {
        setTopics(d.topics)
        sessionLogger.log('learning', 'loaded', { count: d.topics.length })
      })
      .catch(() => setTopics([]))
      .finally(() => setLoading(false))
  }, [])

  const totalSessions = topics.reduce((sum, t) => sum + t.total_sessions, 0)
  const totalMinutes = topics.reduce((sum, t) => sum + t.total_minutes, 0)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold" style={{ color: 'var(--text)' }}>Learning So Far</h1>
        <p className="mt-1 text-sm" style={{ color: 'var(--text-muted)' }}>
          The topics, projects, and patterns Engram has accumulated from your sessions.
        </p>
      </div>

      {!loading && topics.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-3">
          {[
            { label: 'Topics learned', value: topics.length.toLocaleString() },
            { label: 'Sessions observed', value: totalSessions.toLocaleString() },
            { label: 'Time represented', value: minutesLabel(totalMinutes) },
          ].map(item => (
            <div key={item.label} className="engram-card rounded-[1.35rem] p-4" style={{ background: 'var(--surface)' }}>
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{item.label}</p>
              <p className="mt-1 text-xl font-semibold tabular" style={{ color: 'var(--text)' }}>{item.value}</p>
            </div>
          ))}
        </div>
      )}

      {loading ? (
        <div className="grid gap-4 lg:grid-cols-2">
          {[0, 1, 2, 3].map(i => (
            <div key={i} className="rounded-[1.35rem] p-5" style={{ background: 'var(--surface)' }}>
              <div className="mb-4 flex gap-3">
                <div className="skeleton h-10 w-10 rounded-xl" />
                <div className="flex-1 space-y-2">
                  <div className="skeleton h-4 w-1/2 rounded" />
                  <div className="skeleton h-3 w-2/3 rounded" />
                </div>
              </div>
              <div className="space-y-2">
                <div className="skeleton h-3 w-full rounded" />
                <div className="skeleton h-3 w-5/6 rounded" />
                <div className="skeleton h-3 w-2/3 rounded" />
              </div>
            </div>
          ))}
        </div>
      ) : topics.length === 0 ? (
        <div className="engram-card rounded-[1.35rem] p-12 text-center" style={{ background: 'var(--surface)' }}>
          <Clock className="mx-auto mb-4" size={28} style={{ color: 'var(--text-muted)' }} />
          <p className="font-semibold" style={{ color: 'var(--text)' }}>Nothing learned yet</p>
          <p className="mt-1 text-sm" style={{ color: 'var(--text-muted)' }}>
            This fills in after consolidation builds topic threads from indexed sessions.
          </p>
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {topics.map(topic => <LearningCard key={topic.id} topic={topic} />)}
        </div>
      )}
    </div>
  )
}
