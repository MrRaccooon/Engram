import axios, { type InternalAxiosRequestConfig } from 'axios'
import { sessionLogger } from '../utils/sessionLogger'

export const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

// ── Axios interceptors: auto-log every API call ──────────────────────────────

api.interceptors.request.use((cfg: InternalAxiosRequestConfig) => {
  ;(cfg as unknown as Record<string, number>)._t0 = performance.now()
  return cfg
})

api.interceptors.response.use(
  (res) => {
    const t0 = (res.config as unknown as Record<string, number>)._t0
    const ms = t0 ? Math.round(performance.now() - t0) : undefined
    const url = res.config.url ?? ''
    if (!url.includes('/logs') && !url.includes('/health')) {
      sessionLogger.log('api', `${res.config.method?.toUpperCase()} ${url}`, {
        status: res.status,
      }, ms)
    }
    return res
  },
  (err) => {
    const cfg = err?.config
    const t0 = (cfg as unknown as Record<string, number>)?._t0
    const ms = t0 ? Math.round(performance.now() - t0) : undefined
    const url = cfg?.url ?? ''
    const status = err?.response?.status ?? 0
    const message = err?.response?.data?.detail ?? err?.message ?? 'unknown'
    if (!url.includes('/logs')) {
      sessionLogger.log('error', `${cfg?.method?.toUpperCase()} ${url} FAILED`, {
        status,
        error: message,
      }, ms)
    }
    return Promise.reject(err)
  },
)

// ── Types ─────────────────────────────────────────────────────────────────────

export interface CaptureResult {
  capture_id: string
  source_type: 'screenshot' | 'clipboard' | 'url' | 'file' | 'audio'
  timestamp: string
  content_preview: string
  thumb_path: string | null
  window_title: string
  app_name: string
  url: string
  relevance_score: number
  chunk_index: number
  concepts?: ConceptSignal[]
  events?: EventSignal[]
}

export interface ConceptSignal {
  id: string
  prompt: string
  category: string
  confidence: number
}

export interface EventSignal {
  id: string
  change_type: string
  change_magnitude: number
  changed_text: string
}

export interface SearchFilters {
  date_from?: string
  date_to?: string
  source_types?: string[]
  apps?: string[]
}

export interface SearchResponse {
  results: CaptureResult[]
  query_time_ms: number
  total_candidates: number
}

export interface TimelineCapture {
  capture_id: string
  source_type: 'screenshot' | 'clipboard' | 'url' | 'file' | 'audio'
  timestamp: string
  content_preview: string
  thumb_path: string | null
  window_title: string
  app_name: string
  url: string
  status: string
  concepts?: ConceptSignal[]
  events?: EventSignal[]
}

export interface ContextResponse {
  capture_id: string
  center_timestamp: string
  window_minutes: number
  context: (TimelineCapture & { is_center: boolean })[]
  center_index: number | null
}

export interface StatusResponse {
  daemon_running: boolean
  indexed_captures: number
  pending_queue: number
  text_vectors: number
  visual_vectors: number
  storage_mb: number
  timestamp: string
}

// ── API functions ─────────────────────────────────────────────────────────────

export const searchApi = {
  search: (query: string, filters: SearchFilters = {}, top_k = 10) =>
    api.post<SearchResponse>('/search', { query, filters, top_k }).then(r => r.data),

  timeline: (date: string) =>
    api.get<{ date: string; captures: TimelineCapture[]; count: number }>(
      '/search/timeline', { params: { date } }
    ).then(r => r.data),
}

export const captureApi = {
  manual: () =>
    api.post<{ capture_id: string; status: string; message: string }>('/capture/manual').then(r => r.data),

  status: () =>
    api.get<StatusResponse>('/status').then(r => r.data),

  context: (captureId: string, windowMinutes = 5) =>
    api.get<ContextResponse>(`/context/${captureId}`, { params: { window_minutes: windowMinutes } }).then(r => r.data),
}

export const configApi = {
  get: () => api.get<Record<string, unknown>>('/config').then(r => r.data),
  update: (data: Record<string, unknown>) => api.put<{ status: string; config: unknown }>('/config', data).then(r => r.data),
  deleteData: (before: string) => api.delete('/data', { params: { before } }).then(r => r.data),
  runRetention: () => api.post('/retention/run').then(r => r.data),
}

// ── Ask / Intelligence API (Phase 1) ─────────────────────────────────────────

export interface AskPreviewResponse {
  masked_prompt: string
  entity_map: Record<string, string>
  blocked_count: number
  passing_count: number
  estimated_tokens: number
  system_prompt: string
}

export interface AskResponse {
  answer: string
  blocked_count: number
  passing_count: number
  model_used: string
  provider: string
  query_time_ms: number
}

export interface RelatedCapture {
  capture_id: string
  source_type: string
  timestamp: string
  content_preview: string
  thumb_path: string | null
  window_title: string
  app_name: string
  url: string
  similarity: number
  edge_type: string
  concepts?: ConceptSignal[]
  events?: EventSignal[]
}

export const askApi = {
  preview: (query: string, top_k = 10) =>
    api.post<AskPreviewResponse>('/ask/preview', { query, top_k }).then(r => r.data),

  ask: (query: string, top_k = 10, deep = false) =>
    api.post<AskResponse>('/ask', { query, top_k, deep }).then(r => r.data),
}

// ── Related captures (Phase 3) ────────────────────────────────────────────────

export const relatedApi = {
  get: (captureId: string, limit = 5) =>
    api.get<{ capture_id: string; related: RelatedCapture[]; count: number }>(
      `/related/${captureId}`, { params: { limit } }
    ).then(r => r.data),
}

// ── Activity analytics (Phase 4) ─────────────────────────────────────────────

export interface AppTimeEntry { app: string; seconds: number }
export interface FocusSession {
  app: string; window_title: string; start: string; end: string; duration_minutes: number
}
export interface HeatmapCell { weekday: number; hour: number; count: number }

export const activityApi = {
  apps: (from: string, to: string) =>
    api.get<{ from: string; to: string; totals: AppTimeEntry[]; daily: unknown[] }>(
      '/activity/apps', { params: { from, to } }
    ).then(r => r.data),

  focus: (date: string) =>
    api.get<{ date: string; sessions: FocusSession[] }>(
      '/activity/focus', { params: { date } }
    ).then(r => r.data),

  heatmap: (weeks = 4) =>
    api.get<{ weeks: number; cells: HeatmapCell[] }>(
      '/activity/heatmap', { params: { weeks } }
    ).then(r => r.data),
}

// ── Insights (Phase 5) ───────────────────────────────────────────────────────

export interface InsightEntry {
  id: string; date: string; session_start: string; session_end: string
  summary: string; topics: string; consolidated_at: string
  narrative?: string | null; topics_structured?: string | null; projects?: string | null
  files_touched?: string | null; decisions?: string | null; problems?: string | null
  outcomes?: string | null; consolidation_type?: string
}

export interface LearningTopic {
  id: string
  topic: string
  summary: string
  total_sessions: number
  total_minutes: number
  projects?: string | null
  files_touched?: string | null
  decisions?: string | null
  last_updated: string
  created_at: string
}

export const insightsApi = {
  list: (date?: string) =>
    api.get<{ date?: string; insights: InsightEntry[]; count: number }>(
      '/insights', { params: date ? { date } : {} }
    ).then(r => r.data),

  latest: () =>
    api.get<{ insight: InsightEntry | null }>('/insights/latest').then(r => r.data),

  learning: () =>
    api.get<{ topics: LearningTopic[]; count: number }>('/learning/summary').then(r => r.data),
}

// ── Auth (Phase 6) ────────────────────────────────────────────────────────────

export const authApi = {
  unlock: (pin: string) =>
    api.post<{ token: string; message?: string }>('/auth/unlock', { pin }).then(r => r.data),

  lock: () => api.post('/auth/lock').then(r => r.data),

  status: () =>
    api.get<{ auth_enabled: boolean; locked: boolean; pin_configured: boolean }>(
      '/auth/status'
    ).then(r => r.data),
}
