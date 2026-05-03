import { create } from 'zustand'
import type { CaptureResult, SearchFilters, StatusResponse } from '../api/client'

type View = 'search' | 'timeline' | 'chat' | 'activity' | 'insights' | 'learning' | 'settings'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  blocked_count?: number
  passing_count?: number
  model_used?: string
  query_time_ms?: number
}

export interface SensitivityPreview {
  masked_prompt: string
  entity_map: Record<string, string>
  blocked_count: number
  passing_count: number
  estimated_tokens: number
  system_prompt: string
}

interface EngramStore {
  // View
  view: View
  setView: (v: View) => void

  // Search
  query: string
  setQuery: (q: string) => void
  results: CaptureResult[]
  setResults: (r: CaptureResult[]) => void
  isSearching: boolean
  setIsSearching: (v: boolean) => void
  queryTimeMs: number
  setQueryTimeMs: (ms: number) => void

  // Filters
  filters: SearchFilters
  setFilters: (f: SearchFilters) => void
  filtersOpen: boolean
  setFiltersOpen: (v: boolean) => void

  // Selected result (for detail modal)
  selectedResult: CaptureResult | null
  setSelectedResult: (r: CaptureResult | null) => void

  // Timeline
  timelineDate: string
  setTimelineDate: (d: string) => void

  // Status
  status: StatusResponse | null
  setStatus: (s: StatusResponse | null) => void

  // Chat (Phase 1)
  chatMessages: ChatMessage[]
  addChatMessage: (m: ChatMessage) => void
  clearChat: () => void
  isChatLoading: boolean
  setChatLoading: (v: boolean) => void

  // Sensitivity confirmation (Phase 1)
  sensitivityPreview: SensitivityPreview | null
  setSensitivityPreview: (p: SensitivityPreview | null) => void
  pendingChatQuery: string
  setPendingChatQuery: (q: string) => void
}

export const useStore = create<EngramStore>((set) => ({
  view: 'search',
  setView: (view) => set({ view }),

  query: '',
  setQuery: (query) => set({ query }),
  results: [],
  setResults: (results) => set({ results }),
  isSearching: false,
  setIsSearching: (isSearching) => set({ isSearching }),
  queryTimeMs: 0,
  setQueryTimeMs: (queryTimeMs) => set({ queryTimeMs }),

  filters: {},
  setFilters: (filters) => set({ filters }),
  filtersOpen: false,
  setFiltersOpen: (filtersOpen) => set({ filtersOpen }),

  selectedResult: null,
  setSelectedResult: (selectedResult) => set({ selectedResult }),

  timelineDate: new Date().toISOString().split('T')[0],
  setTimelineDate: (timelineDate) => set({ timelineDate }),

  status: null,
  setStatus: (status) => set({ status }),

  chatMessages: [],
  addChatMessage: (m) => set((s) => ({ chatMessages: [...s.chatMessages, m] })),
  clearChat: () => set({ chatMessages: [] }),
  isChatLoading: false,
  setChatLoading: (isChatLoading) => set({ isChatLoading }),

  sensitivityPreview: null,
  setSensitivityPreview: (sensitivityPreview) => set({ sensitivityPreview }),
  pendingChatQuery: '',
  setPendingChatQuery: (pendingChatQuery) => set({ pendingChatQuery }),
}))
