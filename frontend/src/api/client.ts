import axios from 'axios'

export const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

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
  source_type: string
  timestamp: string
  content_preview: string
  thumb_path: string | null
  window_title: string
  app_name: string
  url: string
  status: string
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
