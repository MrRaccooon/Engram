import { create } from 'zustand'
import type { CaptureResult, SearchFilters, StatusResponse } from '../api/client'

type View = 'search' | 'timeline' | 'settings'

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
}))
