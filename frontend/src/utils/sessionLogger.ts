type LogCategory =
  | 'nav' | 'search' | 'ask' | 'capture' | 'timeline'
  | 'settings' | 'auth' | 'activity' | 'insights' | 'learning'
  | 'detail' | 'filter' | 'api' | 'error'

interface LogEvent {
  ts: string
  sid: string
  cat: LogCategory
  action: string
  detail?: Record<string, unknown>
  ms?: number
}

const SESSION_ID = Math.random().toString(36).slice(2, 10)
const buffer: LogEvent[] = []
let flushTimer: ReturnType<typeof setInterval> | null = null

function log(
  category: LogCategory,
  action: string,
  detail?: Record<string, unknown>,
  durationMs?: number,
) {
  const event: LogEvent = {
    ts: new Date().toISOString(),
    sid: SESSION_ID,
    cat: category,
    action,
    ...(detail && { detail }),
    ...(durationMs !== undefined && { ms: durationMs }),
  }

  const parts = [`[${event.cat}] ${event.action}`]
  if (detail) parts.push(JSON.stringify(detail))
  if (durationMs !== undefined) parts.push(`${durationMs}ms`)
  console.log(`%c[engram] ${parts.join(' ')}`, 'color:#7c6af7')

  buffer.push(event)
}

function flush() {
  if (buffer.length === 0) return
  const batch = buffer.splice(0, buffer.length)
  fetch('/api/logs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ events: batch }),
  }).catch(() => {})
}

function start() {
  if (!flushTimer) {
    flushTimer = setInterval(flush, 5000)
    window.addEventListener('beforeunload', flush)
  }
}

start()

export const sessionLogger = { log, flush }
