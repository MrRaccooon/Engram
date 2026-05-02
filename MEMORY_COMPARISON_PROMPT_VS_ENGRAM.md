# Memory Layer Comparison: PROMPT vs Engram

> A side-by-side architectural comparison of how two projects approach "memory" for AI-powered applications. PROMPT is an Electron-based prompt optimizer that learns user facts to personalize LLM output. Engram is a local-first lifelogging engine that captures and indexes digital activity for semantic search and AI recall.

---

## Executive Summary

| Dimension | **PROMPT** | **Engram** |
|-----------|-----------|------------|
| **What it remembers** | Facts *about* the user (role, preferences, patterns) | Facts *from* the user's activity (screenshots, clipboard, URLs, files, shell) |
| **Memory metaphor** | Personality profile + preference engine | Episodic lifelog + semantic search engine |
| **How it learns** | LLM extracts facts from prompt optimization pairs | Passive collectors capture events; ML pipeline embeds them |
| **Storage** | Supabase Postgres (relational key-value) | SQLite (metadata) + ChromaDB (vectors) + filesystem (thumbnails) |
| **Vectors** | None for user memory | Dual vectors: text (384d MiniLM) + visual (512d CLIP) |
| **Retrieval** | Load all → filter by confidence → format string | Dual vector search → cross-encoder rerank → privacy filter |
| **LLM integration** | Inject memory context into optimizer system prompt | Privacy-preserving retrieval-augmented generation (RAG) |
| **User control** | Full CRUD on individual facts + learning toggle | Bulk delete by date + retention policy; no per-item edit |
| **Privacy model** | Cloud-hosted (Supabase RLS) | Fully local (all data on disk; optional masked API calls) |

---

## 1. Core Philosophy

### PROMPT: "Learn who the user is"
PROMPT's memory is a **personalization layer**. It observes the user's prompt optimization history and infers stable facts: their role, domain, preferred tone, tools, and recurring topics. These facts are injected into every future optimization to make the LLM "know" the user. It's closer to how ChatGPT's memory works — a curated set of facts about the user.

### Engram: "Remember everything the user does"
Engram's memory is a **lifelog and retrieval system**. It continuously captures the user's digital activity — every screenshot, clipboard copy, URL visit, file edit, git commit, shell command — and makes it searchable via semantic vectors. It's closer to Rewind.ai or Microsoft Recall — total capture with intelligent retrieval.

---

## 2. Data Model

### PROMPT

```
user_memory (Supabase Postgres)
├── memory_type: user | behavioral | context
├── key: "role", "preferred_tone", "primary_framework"
├── value: "Senior Backend Engineer", "Technical and concise"
├── confidence: 0.0–1.0
├── source: inferred | explicit
└── observation_count: how many times LLM confirmed this fact
```

**Shape**: Key-value pairs organized into 3 fixed categories. Each fact is a single assertion about the user. Total facts per user: typically dozens.

### Engram

```
captures (SQLite)                          ChromaDB Collections
├── source_type: screenshot|clipboard|...  ├── text_embeddings (384d)
├── content: OCR/text/diff                 ├── visual_embeddings (512d)
├── window_title, app_name, url            └── insights_embeddings (384d)
├── thumb_path, raw_path
├── phash (dedup)                          capture_edges (SQLite graph)
└── status: pending|indexed|error          capture_tags (SQLite NER)

insights (SQLite)
├── summary: LLM-generated session recap
├── topics: JSON array
└── session_start/end timestamps
```

**Shape**: Event records with full metadata, embedded into vector space for semantic search. Total records per user: thousands to millions.

---

## 3. How Memory Is Created

| Aspect | **PROMPT** | **Engram** |
|--------|-----------|------------|
| **Trigger** | After every 3rd successful prompt optimization | Continuous: screenshots every 30s, clipboard every 2s, browser every 15m, filesystem on events |
| **Creation method** | LLM extraction: raw prompt + optimized output → JSON facts | Passive collectors → OCR → chunk → embed |
| **LLM involvement** | Yes: GPT-4o-mini extracts facts via structured JSON prompt | Only for consolidation (nightly summaries) and Ask (query answering) |
| **User input** | Can manually add/edit/delete facts in Settings | Can trigger manual capture; no manual memory creation |
| **Deduplication** | `UNIQUE(user_id, memory_type, key)` — upsert on same key | Perceptual hash (screenshots), content hash (clipboard) |
| **Batch size** | 1–5 facts per extraction | 32 captures per worker batch |
| **Cost** | LLM call every 3rd optimization (~$0.001/extraction) | Free (local ML models), except optional nightly consolidation LLM |

---

## 4. Storage Architecture

| Aspect | **PROMPT** | **Engram** |
|--------|-----------|------------|
| **Primary store** | Supabase Postgres (cloud) | SQLite (local file) |
| **Vector store** | None for user memory | ChromaDB (local, persistent HNSW) |
| **Vector dimensions** | N/A | 384 (text) + 512 (visual) |
| **File storage** | None | `~/.engram/` thumbnails + optional raw screenshots |
| **Schema management** | SQL migrations in `supabase/migrations/` | Inline `CREATE TABLE IF NOT EXISTS` |
| **Multi-user** | Yes (Supabase RLS per `auth.uid()`) | No (single local user) |
| **Encryption** | Supabase manages at-rest encryption | Optional Fernet/AES-256 (not enabled by default) |
| **Hosting** | Cloud (Supabase) | Fully local (`~/.engram/`) |

---

## 5. Retrieval

| Aspect | **PROMPT** | **Engram** |
|--------|-----------|------------|
| **Method** | Load all facts → filter confidence ≥ 0.5 → format string | Embed query → dual vector search → cross-encoder rerank |
| **Scalability** | O(N) where N = all user facts (~dozens). Fine at small scale | O(log N) via HNSW. Designed for millions of vectors |
| **Ranking** | No ranking — all qualifying facts included | Cross-encoder reranking + recency decay weighting |
| **Filtering** | Confidence threshold only | Date range, source type, app, sensitivity score, excluded domains |
| **Context window** | 800 character hard cap | 2000 token cap (configurable) |
| **Cross-modal** | No | Yes: text queries can find visually similar screenshots via CLIP |

---

## 6. LLM Integration

### PROMPT: Injection into optimizer

Memory is injected as a structured text block into the `custom_instructions` field passed to the prompt optimizer:

```
[USER PERSONA — background context only. NEVER use to change the type or purpose of the prompt.]
User Identity:
  - role: Senior Backend Engineer
  - industry: Fintech

[USER PREFERENCES — apply where relevant to enrich the optimized prompt.]
User Preferences:
  - preferred_tone: Technical and concise
```

**Guardrails**: Anti-override language ("Do not let user memory override the user's explicit intent") in the optimizer system prompt. Persona facts get stricter wording than preferences.

### Engram: Retrieval-Augmented Generation (RAG)

Memory is retrieved per-query, privacy-filtered, entity-masked, and assembled into a context block:

```
Context from your digital activity:

[1] SCREENSHOT • 2026-04-30T14:23:05 • Code - Insiders
Editing api/routes/search.py — implementing dual vector retrieval...

[2] CLIPBOARD • 2026-04-30T14:45:12
"SELECT * FROM captures WHERE status = 'indexed'"

---

Question: What was I working on yesterday afternoon?
```

**Privacy pipeline**: Sensitivity filter blocks PII/financial data. Entity masker replaces names ([PERSON_1]). Optional local Ollama pre-summarization compresses chunks before any external API call.

---

## 7. User Control & CRUD

| Operation | **PROMPT** | **Engram** |
|-----------|-----------|------------|
| **View memories** | Settings > Memory tab: 3 sections with fact cards | Search, Timeline, Detail Modal, Insights |
| **Add** | Inline key/value form per section | Manual capture button (Ctrl+Shift+M) |
| **Edit** | Inline value edit → promotes to explicit (confidence 1.0) | Not supported — no per-capture text editing |
| **Delete** | Per-fact trash button | Bulk delete by date (Settings danger zone) |
| **Toggle** | "Learn from my usage" switch (stops extraction) | N/A — capture is always-on while daemon runs |
| **Search/filter** | Not supported in UI | Semantic search + filters (date, source, app) |
| **Privacy preview** | N/A — all facts visible | Full sensitivity preview before any API call |

---

## 8. Priority & Importance

### PROMPT

Explicit user-set facts always override inferred facts:
- `source: 'explicit'` → `confidence: 1.0` → never overwritten
- `source: 'inferred'` → confidence capped at `0.95` → skipped if explicit exists
- `observation_count` tracks how often a fact was independently confirmed
- Persona keys (role, occupation) get stricter injection guardrails than preferences

### Engram

No explicit priority system. Relevance is computed dynamically:
- **Cosine similarity** from dual vector search
- **Cross-encoder reranking** refines relevance
- **Recency decay**: `score × (0.7 + 0.3 × exp(-hours_ago/168))` — recent captures boosted
- **Sensitivity score** determines what reaches the LLM (not a quality signal, a privacy gate)

---

## 9. Graph / Associations

| Aspect | **PROMPT** | **Engram** |
|--------|-----------|------------|
| **Graph** | None | SQLite `capture_edges` — cosine similarity ≥ 0.60 |
| **Tags/entities** | Fixed `memory_type` enum only | spaCy NER → `capture_tags` (ORG, PERSON, TECH, TOPIC) |
| **Associations** | N/A | "Related" captures in detail modal via edge graph |
| **Visualization** | N/A | List of related captures (no network diagram) |

---

## 10. Background Processing

| Aspect | **PROMPT** | **Engram** |
|--------|-----------|------------|
| **Worker type** | Fire-and-forget JS promises | APScheduler (BackgroundScheduler) |
| **Extraction frequency** | Every 3rd optimization (~minutes apart) | Every 1 minute (worker) + continuous collectors |
| **Consolidation** | N/A | Nightly at 2 AM — session summaries |
| **Queue** | No queue — inline promise chain | SQLite `job_queue` with retry (max 3 attempts) |
| **Failure handling** | Catch + log + continue | Status → 'error', increment attempts, log |
| **State persistence** | In-memory counter (resets on restart) | `daemon/state.py` persists last-run timestamps |

---

## 11. Privacy & Security

| Aspect | **PROMPT** | **Engram** |
|--------|-----------|------------|
| **Data location** | Cloud (Supabase) | Local (`~/.engram/`) |
| **Auth** | Supabase JWT + RLS | Optional local PIN |
| **Encryption** | Supabase-managed | Optional Fernet/AES-256 (off by default) |
| **What leaves machine** | Facts injected into optimizer (always) | Only masked context for Ask queries (user-confirmed) |
| **Prompt injection defense** | `sanitizeField()` strips brackets, SYSTEM:, jailbreak phrases | Entity masking replaces PII with [PERSON_1], [ORG_1] |
| **Sensitivity filtering** | N/A — all facts are low-sensitivity by design | Configurable threshold blocks financial, PII, password chunks |
| **GDPR** | Account deletion cascades to `user_memory` | Manual bulk delete; retention policy |
| **Audit logging** | Sentry error tracking | Optional audit.log for all searches and accesses |

---

## 12. Frontend Architecture

| Aspect | **PROMPT** | **Engram** |
|--------|-----------|------------|
| **Framework** | React (in Electron) | React 19 + Vite 8 |
| **State management** | Local React state only | Zustand (single store) |
| **Styling** | Tailwind CSS | Tailwind CSS 4 + CSS custom properties |
| **Communication** | Electron IPC (`window.electronAPI.memory.*`) | Axios HTTP to localhost:8765 |
| **Live updates** | `memory:updated` IPC event from main process | Polling (status every 10s) |
| **Animations** | `transition-smooth` utilities | Framer Motion |
| **Icons** | Phosphor Icons | Lucide React |
| **Keyboard shortcuts** | N/A | Ctrl+K (search), Ctrl+J (ask), Ctrl+T (timeline) |

---

## 13. Consolidation / Long-term Memory

### PROMPT
No consolidation. Facts are permanent key-value pairs that persist indefinitely. The `observation_count` increments but no summarization or compression occurs.

### Engram
Nightly "sleep cycle" at 2 AM:
1. Group day's captures into sessions (30-min gap boundary)
2. Summarize each session via LLM (Ollama → API → heuristic fallback)
3. Write to `insights` table + embed into `insights_embeddings` collection
4. Summaries used as context in Ask pipeline and displayed in Insights view

This creates a **two-tier memory**: raw captures (short-term detail) + consolidated insights (long-term summaries).

---

## 14. Configuration

| Setting Type | **PROMPT** | **Engram** |
|-------------|-----------|------------|
| **Config format** | Env vars + DB settings | YAML file (`config/config.yaml`) |
| **Model selection** | `MEMORY_MODEL` env var | `config.yaml` intelligence section |
| **Toggle** | `user_settings.memory_enabled` in DB | N/A (daemon on/off is the toggle) |
| **Thresholds** | Hardcoded in TypeScript (confidence ≥ 0.5, 800 char cap) | YAML-configurable (sensitivity, tokens, intervals) |
| **Hot-reload** | Requires restart | Config API reads YAML on each request |

---

## 15. Scale Characteristics

| Metric | **PROMPT** | **Engram** |
|--------|-----------|------------|
| **Facts per user** | Dozens (typically 10–50) | Thousands to millions of captures |
| **Storage footprint** | <1 KB per user | GBs (screenshots + vectors + metadata) |
| **Query latency** | <50ms (load all from Supabase) | 100ms–2s (vector search + rerank) |
| **Model memory** | None (LLM runs on cloud) | ~430MB (MiniLM 80MB + CLIP 350MB) resident on CPU |
| **Cost** | ~$0.001 per extraction (cloud LLM) | Free (local ML) + optional API cost for Ask/consolidation |

---

## 16. What Each Could Learn from the Other

### PROMPT could benefit from Engram's:
- **Vector-based retrieval**: Instead of loading all facts and hard-capping at 800 chars, PROMPT could embed facts and retrieve the most relevant ones per query
- **Consolidation pattern**: Periodic summarization of inferred patterns into higher-level insights
- **Richer context assembly**: Instead of flat key-value injection, retrieval-augmented context selection
- **Configurable thresholds**: YAML-based config instead of hardcoded constants

### Engram could benefit from PROMPT's:
- **Explicit user facts**: Let users declare stable identity facts (role, preferences) that enrich every query
- **Priority system**: Explicit-overrides-inferred pattern for user-declared vs auto-extracted facts
- **Observation counting**: Track how often a pattern is confirmed for confidence building
- **Per-fact CRUD**: Individual fact editing and deletion instead of bulk-only operations
- **Live sync**: Push-based UI updates when new data is indexed (not just polling)

---

## 17. Shared Gaps

| Gap | Both Projects |
|-----|--------------|
| **No automated tests** | Neither has test suites for their memory layers |
| **No data export** | Neither provides a way to export all memory as a portable format |
| **No cross-device sync** | PROMPT is cloud-scoped to one user; Engram is local-only |
| **Limited visualization** | Neither has rich visualizations of memory (graphs, timelines with insights overlaid, etc.) |
