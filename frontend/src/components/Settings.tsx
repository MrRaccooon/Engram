import { useEffect, useState } from 'react'
import { Save, Trash2, RefreshCw, AlertTriangle } from 'lucide-react'
import { configApi, captureApi, type StatusResponse } from '../api/client'
import { sessionLogger } from '../utils/sessionLogger'

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="engram-card rounded-[1.35rem] p-5" style={{ background: 'var(--surface)' }}>
      <h3 className="mb-4 font-semibold" style={{ color: 'var(--text)' }}>{title}</h3>
      <div className="space-y-4">{children}</div>
    </div>
  )
}

function Field({ label, description, children }: { label: string; description?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-6">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium" style={{ color: 'var(--text)' }}>{label}</p>
        {description && <p className="mt-0.5 text-xs" style={{ color: 'var(--text-muted)' }}>{description}</p>}
      </div>
      <div className="flex-shrink-0">{children}</div>
    </div>
  )
}

function NumberInput({ value, onChange, min = 1, max = 9999, unit = '' }: {
  value: number; onChange: (v: number) => void; min?: number; max?: number; unit?: string
}) {
  return (
    <div className="flex items-center gap-2">
      <input
        type="number"
        value={value}
        onChange={e => onChange(Number(e.target.value))}
        min={min}
        max={max}
        className="w-24 rounded-lg border px-3 py-1.5 text-sm text-right outline-none"
        style={{ background: 'var(--surface-2)', borderColor: 'var(--border)', color: 'var(--text)' }}
      />
      {unit && <span className="text-sm" style={{ color: 'var(--text-muted)' }}>{unit}</span>}
    </div>
  )
}

export function SettingsPanel() {
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null)
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [deleteDate, setDeleteDate] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [runningRetention, setRunningRetention] = useState(false)

  useEffect(() => {
    configApi.get().then(setCfg).catch(() => {})
    captureApi.status().then(setStatus).catch(() => {})
  }, [])

  const getCapture = () => (cfg?.capture as Record<string, unknown>) ?? {}
  const getStorage = () => (cfg?.storage as Record<string, unknown>) ?? {}

  const patchCapture = (key: string, val: unknown) =>
    setCfg(prev => ({ ...prev, capture: { ...(prev?.capture as object ?? {}), [key]: val } }))
  const patchStorage = (key: string, val: unknown) =>
    setCfg(prev => ({ ...prev, storage: { ...(prev?.storage as object ?? {}), [key]: val } }))

  const save = async () => {
    if (!cfg) return
    setSaving(true)
    sessionLogger.log('settings', 'save')
    try { await configApi.update(cfg); setSaved(true); setTimeout(() => setSaved(false), 2000); sessionLogger.log('settings', 'saved') }
    catch { alert('Failed to save settings'); sessionLogger.log('settings', 'save_failed') }
    finally { setSaving(false) }
  }

  const deleteData = async () => {
    if (!deleteDate) return
    if (!confirm(`Delete all captures before ${deleteDate}? This cannot be undone.`)) return
    setDeleting(true)
    sessionLogger.log('settings', 'delete_data', { before: deleteDate })
    try { await configApi.deleteData(deleteDate); alert('Data deleted successfully'); sessionLogger.log('settings', 'data_deleted') }
    catch { alert('Deletion failed'); sessionLogger.log('settings', 'delete_failed') }
    finally { setDeleting(false) }
  }

  if (!cfg) return (
    <div className="flex justify-center py-20">
      <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent" />
    </div>
  )

  return (
    <div className="space-y-4 max-w-2xl">
      {/* Status */}
      {status && (
        <Section title="System Status">
          <div className="grid grid-cols-3 gap-3">
            {[
              { label: 'Captures', value: status.indexed_captures.toLocaleString() },
              { label: 'Queue depth', value: status.pending_queue.toString() },
              { label: 'Storage', value: `${status.storage_mb} MB` },
              { label: 'Text vectors', value: status.text_vectors.toLocaleString() },
              { label: 'Visual vectors', value: status.visual_vectors.toLocaleString() },
              { label: 'Daemon', value: status.daemon_running ? '● Running' : '○ Stopped' },
            ].map(({ label, value }) => (
              <div key={label} className="rounded-xl p-3" style={{ background: 'var(--surface-2)' }}>
                <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{label}</p>
                <p className="text-sm font-semibold mt-0.5 tabular" style={{ color: 'var(--text)' }}>{value}</p>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Capture settings */}
      <Section title="Capture">
        <Field label="Idle screenshot interval" description="Adaptive capture uses a 2s tick while active, then falls back to this interval when idle">
          <NumberInput value={Number(getCapture().screenshot_interval_seconds ?? 30)} onChange={v => patchCapture('screenshot_interval_seconds', v)} min={2} max={300} unit="sec" />
        </Field>
        <Field label="Clipboard poll interval" description="How often to check the clipboard">
          <NumberInput value={Number(getCapture().clipboard_poll_seconds ?? 2)} onChange={v => patchCapture('clipboard_poll_seconds', v)} min={1} max={60} unit="sec" />
        </Field>
      </Section>

      {/* Storage settings */}
      <Section title="Storage">
        <Field label="Retention period" description="Delete captures older than this">
          <NumberInput value={Number(getStorage().retention_days ?? 90)} onChange={v => patchStorage('retention_days', v)} min={0} max={3650} unit="days" />
        </Field>
        <Field label="Storage budget" description="Auto-cleanup when storage exceeds this limit (0 = unlimited)">
          <NumberInput value={Number(getStorage().max_storage_gb ?? 10)} onChange={v => patchStorage('max_storage_gb', v)} min={0} max={1000} unit="GB" />
        </Field>
        <Field label="Thumbnail size" description="Max dimension of stored screenshot thumbnails">
          <NumberInput value={Number(getStorage().thumbnail_size ?? 400)} onChange={v => patchStorage('thumbnail_size', v)} min={100} max={1200} unit="px" />
        </Field>
        <div className="pt-2">
          <button
            onClick={async () => { sessionLogger.log('settings', 'run_retention'); setRunningRetention(true); await configApi.runRetention().catch(() => {}); setRunningRetention(false) }}
            disabled={runningRetention}
            className="flex items-center gap-2 rounded-xl px-4 py-2 text-sm transition-colors"
            style={{ background: 'var(--surface-2)', color: 'var(--text-muted)' }}
          >
            <RefreshCw size={14} className={runningRetention ? 'animate-spin' : ''} />
            Run retention now
          </button>
        </div>
      </Section>

      {/* Save button */}
      <button
        onClick={save}
        disabled={saving}
        className="flex items-center gap-2 rounded-xl px-5 py-2.5 text-sm font-medium transition-colors"
        style={{ background: saved ? '#22c55e' : 'var(--accent)', color: '#fff' }}
      >
        <Save size={14} />
        {saved ? 'Saved!' : saving ? 'Saving…' : 'Save settings'}
      </button>

      {/* Danger zone */}
      <Section title="Danger Zone">
        <div className="flex items-center gap-2 mb-2">
          <AlertTriangle size={14} style={{ color: '#ef4444' }} />
          <p className="text-sm" style={{ color: '#ef4444' }}>Delete all captures before a date</p>
        </div>
        <div className="flex items-center gap-3">
          <input
            type="date"
            value={deleteDate}
            onChange={e => setDeleteDate(e.target.value)}
            className="rounded-lg border px-3 py-2 text-sm outline-none"
            style={{ background: 'var(--surface-2)', borderColor: '#ef4444', color: 'var(--text)' }}
          />
          <button
            onClick={deleteData}
            disabled={!deleteDate || deleting}
            className="flex items-center gap-2 rounded-xl px-4 py-2 text-sm transition-colors"
            style={{ background: '#ef444420', color: '#ef4444' }}
          >
            <Trash2 size={14} />
            {deleting ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </Section>
    </div>
  )
}
