import { Activity, Sparkles } from 'lucide-react'
import type { ConceptSignal, EventSignal } from '../api/client'

function cleanPrompt(prompt: string) {
  return prompt
    .replace(/^a screenshot of\s+/i, '')
    .replace(/^an image of\s+/i, '')
    .trim()
}

export function ConceptPills({ concepts = [], limit = 3 }: { concepts?: ConceptSignal[]; limit?: number }) {
  const visible = concepts.slice(0, limit)
  if (visible.length === 0) return null

  return (
    <div className="flex flex-wrap gap-1.5">
      {visible.map(c => (
        <span
          key={c.id}
          className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium"
          style={{
            background: 'color-mix(in srgb, var(--accent) 12%, transparent)',
            color: 'var(--accent)',
          }}
          title={`${c.prompt} · ${Math.round(c.confidence * 100)}%`}
        >
          <Sparkles size={10} />
          {cleanPrompt(c.prompt)}
        </span>
      ))}
      {concepts.length > limit && (
        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>+{concepts.length - limit}</span>
      )}
    </div>
  )
}

export function EventPills({ events = [], limit = 2 }: { events?: EventSignal[]; limit?: number }) {
  const visible = events.filter(e => e.change_type && e.change_type !== 'idle').slice(0, limit)
  if (visible.length === 0) return null

  return (
    <div className="flex flex-wrap gap-1.5">
      {visible.map(e => (
        <span
          key={e.id}
          className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium"
          style={{
            background: 'color-mix(in srgb, #22c55e 12%, transparent)',
            color: '#22c55e',
          }}
          title={e.changed_text || `${Math.round(e.change_magnitude * 100)}% screen changed`}
        >
          <Activity size={10} />
          {e.change_type.replaceAll('_', ' ')}
        </span>
      ))}
    </div>
  )
}
