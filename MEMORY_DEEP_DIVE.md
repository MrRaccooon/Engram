# Engram Memory Layer — Complete Technical Documentation

> **Scope**: Engram is a local-first, passive lifelogging engine that captures digital activity (screenshots, clipboard, browser history, files, shell history, git diffs), processes it through OCR and dual embeddings, stores it in SQLite + ChromaDB, and enables semantic search, temporal browsing, LLM-powered Q&A, and nightly consolidation into session insights. This document covers every aspect of how "memory" works — from capture to storage to retrieval to deletion.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [What Constitutes "Memory"](#2-what-constitutes-memory)
3. [Database Layer — SQLite (Metadata)](#3-database-layer--sqlite-metadata)
4. [Database Layer — ChromaDB (Vectors)](#4-database-layer--chromadb-vectors)
5. [Semantic Graph Layer](#5-semantic-graph-layer)
6. [Collectors — How Memories Are Created](#6-collectors--how-memories-are-created)
7. [Ingestion Pipeline — The Cold Path](#7-ingestion-pipeline--the-cold-path)
8. [Embedding Engine](#8-embedding-engine)
9. [Text Chunker](#9-text-chunker)
10. [Retrieval Pipeline — Search & Ask](#10-retrieval-pipeline--search--ask)
11. [Intelligence Pipeline — Privacy-Preserving LLM Q&A](#11-intelligence-pipeline--privacy-preserving-llm-qa)
12. [Consolidation Worker — The "Sleep Cycle"](#12-consolidation-worker--the-sleep-cycle)
13. [Retention & Deletion](#13-retention--deletion)
14. [Background Jobs & Scheduling](#14-background-jobs--scheduling)
15. [API Surface](#15-api-surface)
16. [MCP Server](#16-mcp-server)
17. [Frontend](#17-frontend)
18. [Configuration Reference](#18-configuration-reference)
19. [File Inventory](#19-file-inventory)
20. [Known Gaps & Limitations](#20-known-gaps--limitations)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            COLLECTORS (Hot Path)                             │
│                                                                             │
│  screenshot.py  clipboard.py  browser_history.py  filesystem.py             │
│  git_diff.py    shell_history.py   window_context.py                        │
│                                                                             │
│  Each fires on a schedule or event → deduplicates via perceptual hash →     │
│  saves thumbnail to disk → inserts into SQLite captures + job_queue         │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         INGESTION PIPELINE (Cold Path)                       │
│                                                                             │
│  queue_manager.py → worker.py (APScheduler, every 1 min)                    │
│                                                                             │
│  For each pending capture:                                                  │
│    1. OCR (RapidOCR) + screenshot_analyzer → enriched searchable text       │
│    2. context_parser → parse window title into project/file/activity        │
│    3. chunker → split into 512-token overlapping windows                    │
│    4. embedder → text vectors (MiniLM, 384d) + visual vectors (CLIP, 512d) │
│    5. vector_db → upsert into ChromaDB text + visual collections            │
│    6. entity_masker → spaCy NER → capture_tags in SQLite                    │
│    7. graph_db → nearest-neighbor edges → capture_edges in SQLite           │
│    8. Update status → 'indexed'                                             │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              STORAGE LAYER                                   │
│                                                                             │
│  ┌─────────────────────┐  ┌──────────────────────┐  ┌───────────────────┐  │
│  │   SQLite             │  │   ChromaDB            │  │   Filesystem      │  │
│  │   (~/.engram/meta.db)│  │   (~/.engram/chroma/) │  │   (~/.engram/)    │  │
│  │                      │  │                       │  │                   │  │
│  │   • captures         │  │   • text_embeddings   │  │   • thumbnails/   │  │
│  │   • job_queue        │  │     (384d, cosine)    │  │   • raw/ (opt.)   │  │
│  │   • insights         │  │   • visual_embeddings │  │                   │  │
│  │   • capture_edges    │  │     (512d, cosine)    │  │                   │  │
│  │   • capture_tags     │  │   • insights_embeddings│ │                   │  │
│  └─────────────────────┘  └──────────────────────┘  └───────────────────┘  │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           RETRIEVAL LAYER                                    │
│                                                                             │
│  Search: dual vector query → cross-encoder rerank → dedupe → respond        │
│  Ask:    retrieval → sensitivity filter → entity mask → optional local       │
│          compress → prompt assembly → frontier API → entity unmask           │
│  Timeline: chronological SQLite query for a date                             │
│  Related: graph_db.get_related → capture_edges SQL join                      │
│  Insights: consolidated session summaries from insights table                │
│  Activity: app time, focus sessions, heatmap from captures                   │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PRESENTATION LAYER                                   │
│                                                                             │
│  FastAPI (localhost:8765)        React SPA (Vite)       MCP Server (:8766)  │
│  /api/search                    SearchBar + ResultCard   engram_search       │
│  /api/ask, /api/ask/preview     ChatView + SensModal     engram_ask          │
│  /api/search/timeline           TimelineView             engram_timeline     │
│  /api/related/{id}              DetailModal              engram_insights     │
│  /api/insights                  InsightsView                                │
│  /api/activity/*                ActivityDashboard                           │
│  /api/config, /api/data         Settings                                    │
└─────────────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────────────────────────┐
                    │    CONSOLIDATION (Nightly, 2 AM)    │
                    │                                     │
                    │  Fetch day's captures → group into  │
                    │  sessions (30 min gap) → summarize  │
                    │  via Ollama/API/heuristic → write   │
                    │  to insights table + ChromaDB       │
                    └─────────────────────────────────────┘
```

---

## 2. What Constitutes "Memory"

Engram does not use a classical `Memory` model with key-value facts. **Memory = captured digital activity**. The system has four conceptual memory layers:

| Layer | What It Stores | Where | Analogy |
|-------|---------------|-------|---------|
| **Raw captures** | Individual events: screenshots, clipboard copies, URLs visited, files opened, git diffs, shell commands | SQLite `captures` + filesystem thumbnails | Episodic memory — "what happened at time X" |
| **Searchable form** | Text chunks + CLIP image embeddings of those captures | ChromaDB `text_embeddings` + `visual_embeddings` | Indexed long-term memory — "find something about topic Y" |
| **Consolidated insights** | Nightly LLM-generated session summaries | SQLite `insights` + ChromaDB `insights_embeddings` | Semantic memory — "what was I working on last Tuesday?" |
| **Associative graph** | Similarity edges between captures + NER tags | SQLite `capture_edges` + `capture_tags` | Associative memory — "what else was I doing around the same topic?" |

---

## 3. Database Layer — SQLite (Metadata)

**File**: `storage/metadata_db.py`

All tables are created inline via `CREATE TABLE IF NOT EXISTS` (no migration files). Database uses WAL journal mode and foreign keys.

### 3.1 Table: `captures`

The core record of every captured event.

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| `id` | TEXT | PK | UUID string |
| `timestamp` | TEXT | NOT NULL | ISO datetime of capture |
| `source_type` | TEXT | NOT NULL, CHECK IN (screenshot, clipboard, url, file, audio) | What collector created it |
| `raw_path` | TEXT | nullable | Path to full-resolution screenshot (if `keep_raw_screenshots: true`) |
| `thumb_path` | TEXT | nullable | Path to thumbnail (always kept) |
| `content` | TEXT | nullable | Text content (clipboard text, file content, OCR output) |
| `phash` | TEXT | nullable | Perceptual hash for deduplication |
| `window_title` | TEXT | nullable | Active window title at capture time |
| `app_name` | TEXT | nullable | Active application name |
| `url` | TEXT | nullable | Browser URL if applicable |
| `status` | TEXT | NOT NULL, DEFAULT 'pending', CHECK IN (pending, indexed, skipped, error) | Processing state |

**Indexes**: `timestamp`, `source_type`, `status`.

### 3.2 Table: `job_queue`

Processing queue for the cold-path worker.

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| `capture_id` | TEXT | NOT NULL, FK → captures(id) ON DELETE CASCADE | Links to the capture |
| `created_at` | TEXT | DEFAULT datetime('now') | When the job was enqueued |
| `attempts` | INTEGER | DEFAULT 0 | Retry count (max 3 in worker) |
| `error` | TEXT | nullable | Last error message |

### 3.3 Table: `insights`

Nightly consolidated session summaries.

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| `id` | TEXT | PK | UUID string |
| `date` | TEXT | NOT NULL | Date of the session (YYYY-MM-DD) |
| `session_start` | TEXT | NOT NULL | ISO timestamp of first capture in session |
| `session_end` | TEXT | NOT NULL | ISO timestamp of last capture in session |
| `summary` | TEXT | NOT NULL | LLM-generated or heuristic summary |
| `topics` | TEXT | nullable | JSON array of topic strings |
| `consolidated_at` | TEXT | NOT NULL | When the insight was created |

### 3.4 Table: `capture_edges`

Similarity-based associations between captures (the "semantic graph").

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| `source_id` | TEXT | NOT NULL | First capture UUID |
| `target_id` | TEXT | NOT NULL | Second capture UUID |
| `similarity` | REAL | NOT NULL | Cosine similarity score (0–1) |
| `edge_type` | TEXT | NOT NULL | Always "semantic" currently |

**PK**: `(source_id, target_id)`. Indexes on both `source_id` and `target_id` for bidirectional lookup.

### 3.5 Table: `capture_tags`

NER-extracted named entities associated with captures.

| Column | Type | Constraints | Purpose |
|--------|------|-------------|---------|
| `capture_id` | TEXT | NOT NULL | Capture UUID |
| `tag` | TEXT | NOT NULL | Entity text (e.g., "React", "John Smith") |
| `tag_type` | TEXT | NOT NULL | Entity type (ORG, PERSON, TECH, TOPIC) |

**PK**: `(capture_id, tag)`. Indexes on both `capture_id` and `tag`.

### 3.6 Key Query Helpers

- `fetch_pending_jobs(limit)` — Joins `job_queue` + `captures` where status = 'pending' and attempts < 3
- `fetch_captures_in_window(center_ts, window_minutes)` — Temporal context: all captures ±N minutes from a point
- `fetch_captures_for_day(date_str)` — All captures for a day, chronologically
- `fetch_recent_captures(minutes, limit)` — Last N indexed captures (used by Ask for session context)
- `fetch_top_apps(hours, limit)` — Most-used apps by capture count
- `fetch_top_window_titles(hours, limit)` — Recent distinct window titles
- `fetch_related_captures(capture_id, limit)` — Bidirectional graph join on `capture_edges`
- `delete_captures_before(cutoff_iso)` — Bulk age-based deletion (SQLite only)

---

## 4. Database Layer — ChromaDB (Vectors)

**File**: `storage/vector_db.py`

Three persistent collections using HNSW indexing with cosine distance:

### 4.1 Collection: `text_embeddings`

- **Dimensionality**: 384 (from `all-MiniLM-L6-v2`)
- **Distance**: Cosine
- **One document per text chunk** (a capture can have multiple chunks)
- **Metadata per vector**: `capture_id`, `timestamp`, `source_type`, `content_preview` (≤300 chars), `chunk_index`, `total_chunks`, `window_title`, `app_name`, `url`

### 4.2 Collection: `visual_embeddings`

- **Dimensionality**: 512 (from CLIP ViT-B/32)
- **Distance**: Cosine
- **One document per screenshot capture**
- **Metadata per vector**: `capture_id`, `timestamp`, `thumb_path`, `window_title`, `app_name`

### 4.3 Collection: `insights_embeddings`

- **Dimensionality**: 384 (same text model as above)
- **Distance**: Cosine
- **One document per consolidated insight**
- **Metadata per vector**: `insight_id`, `date`, `summary_preview` (≤300 chars), `topics`, `source_type` (always "insight")

### 4.4 Recovery Mechanism

ChromaDB can suffer on-disk HNSW corruption. The module has a one-shot recovery mechanism:
- Detects "nothing found on disk", "hnsw segment reader", or "internal error" exceptions
- Drops and recreates the affected collection
- Sets a per-collection `_recovery_attempted` flag to prevent infinite loops
- New captures will repopulate the index naturally

### 4.5 Key Operations

- `upsert_text(...)` — Writes a text chunk embedding with full metadata
- `upsert_visual(...)` — Writes a CLIP image embedding
- `upsert_insight(...)` — Writes a consolidated insight embedding
- `query_text(embedding, top_k, where)` — Nearest-neighbor text search with optional metadata filters
- `query_visual(embedding, top_k, where)` — Nearest-neighbor visual search
- `query_insights(embedding, top_k, where)` — Query insight summaries semantically
- `get_nearest_text_neighbors(embedding, top_k, exclude_id)` — Used by graph builder
- `delete_by_capture_ids(capture_ids)` — Removes vectors from both text + visual collections

---

## 5. Semantic Graph Layer

**File**: `storage/graph_db.py`

A lightweight associative memory layer built on SQLite tables (`capture_edges` + `capture_tags`). Not a full property graph database.

### 5.1 Edge Building

After each capture is indexed, the worker calls `build_edges_for_capture(capture_id, embedding, top_k=5)`:

1. Queries ChromaDB for the 5 nearest text vectors (excluding self)
2. For each neighbor with cosine similarity ≥ **0.60** (`SIMILARITY_THRESHOLD`):
   - Upserts an edge into `capture_edges` with `edge_type="semantic"`
3. Returns the count of edges created

### 5.2 Tag Building

After OCR/text extraction, the worker calls `entity_masker.extract_tags(text)`:
- Uses spaCy `en_core_web_sm` for NER
- Extracts entities typed as ORG, PERSON, TECH, TOPIC
- Inserts into `capture_tags` via `upsert_tags()`

### 5.3 Graph Queries

- `get_related(capture_id, limit)` → Returns related captures via bidirectional edge join, ordered by similarity
- `get_by_tag(tag, limit)` → Returns captures associated with a specific tag

---

## 6. Collectors — How Memories Are Created

**Directory**: `collectors/`

Each collector produces `captures` rows via `queue_manager.enqueue()` → `metadata_db.insert_capture()`.

| Collector | File | Trigger | What It Captures |
|-----------|------|---------|-----------------|
| Screenshot | `screenshot.py` | Interval (30s default, adaptive) | Active window screenshot → thumbnail + perceptual hash dedup |
| Clipboard | `clipboard.py` | Poll (2s default) | Text clipboard changes, deduped by content |
| Browser History | `browser_history.py` | Interval (15 min) | New URLs from Chrome/Firefox/Edge history DBs |
| Filesystem | `filesystem.py` | Watchdog events | File open/modify events in watched directories for watched extensions |
| Git Diff | `git_diff.py` | On commit detection | Git diff summaries for tracked repos |
| Shell History | `shell_history.py` | Periodic | New commands from bash/zsh/PowerShell history |
| Window Context | `window_context.py` | Passive | Active window title + app name (used to enrich other captures) |

### 6.1 Deduplication

Screenshots use **perceptual hashing** (`imagehash` library) to skip duplicate or near-duplicate frames. If the phash of a new screenshot is within the Hamming distance threshold of the most recent capture, it's skipped.

---

## 7. Ingestion Pipeline — The Cold Path

**File**: `pipeline/worker.py`

The worker runs every 1 minute (configurable) via APScheduler. For each pending capture:

### 7.1 Step 1: Text Extraction

- **Screenshots**: `context_parser.parse_window()` enriches window title into structured text (project, file, activity). Then `ocr_fast.extract_text()` (RapidOCR) + `screenshot_analyzer.analyze()` extracts and categorizes screen content. Error patterns in OCR text are detected and prepended as "ERRORS VISIBLE:" for better searchability.
- **Other types**: Use the `content` field directly.

### 7.2 Step 2: Chunk + Text Embed

- Split text via `chunker.chunk()` (512 tokens, 64 token overlap)
- Embed each chunk via `embedder.embed_texts()` (MiniLM, 384d)
- Upsert into ChromaDB `text_embeddings` with `doc_id = "{capture_id}_t{chunk_idx}"`

### 7.3 Step 3: Visual Embed

- Screenshots only: `embedder.embed_image_path()` (CLIP ViT-B/32, 512d)
- Upsert into ChromaDB `visual_embeddings` with `doc_id = "{capture_id}_v0"`

### 7.4 Step 4: NER Tagging

- `entity_masker.extract_tags(text)` via spaCy
- `graph_db.upsert_tags(capture_id, tags)`

### 7.5 Step 5: Semantic Graph Edges

- Embed first 512 chars of text
- `graph_db.build_edges_for_capture(capture_id, embedding, top_k=5)`
- Creates edges for neighbors with similarity ≥ 0.60

### 7.6 Step 6: Status Update

- On success: `status = 'indexed'`
- On failure: `status = 'error'`, error message logged on `job_queue`
- Max 3 attempts per capture before giving up

---

## 8. Embedding Engine

**File**: `pipeline/embedder.py`

### 8.1 Text Embedder

- **Model**: `all-MiniLM-L6-v2` (sentence-transformers)
- **Dimensions**: 384
- **Device**: CPU
- **Size**: ~80MB
- **Normalization**: Enabled (unit-length vectors for cosine similarity)
- **Batch processing**: Configurable batch size (default 16)
- **Lazy loading**: Model loaded on first use, cached for process lifetime

### 8.2 Visual Embedder (CLIP)

- **Model**: `ViT-B-32` (open-clip-torch)
- **Pretrained**: OpenAI weights
- **Dimensions**: 512
- **Device**: CPU
- **Size**: ~350MB
- **Normalization**: Manual L2 normalization after encoding
- **Capabilities**:
  - `embed_image_path(path)` — Encode a thumbnail image
  - `embed_query_text_clip(query)` — Encode a text query in CLIP's text space (for cross-modal search: text query → visual results)

### 8.3 Recovery

If CLIP initialization fails (e.g., meta-device issue in open_clip 3.x + torch 2.11), it sets `_clip_broken = True` and all subsequent visual embedding calls are skipped gracefully. Text embedding continues to work.

---

## 9. Text Chunker

**File**: `pipeline/chunker.py`

- **Chunk size**: 512 tokens (word approximation: 1 token ≈ 0.75 words)
- **Overlap**: 64 tokens between consecutive chunks
- **Algorithm**: Sliding window over word array with step = `max_words - overlap_words`
- **Short texts**: Returned as a single chunk without splitting
- **Empty texts**: Return empty list

---

## 10. Retrieval Pipeline — Search & Ask

**File**: `api/routes/search.py`

### 10.1 Semantic Search (`POST /api/search`)

1. **Dual vector retrieval**: Embed the query as both text (MiniLM) and CLIP text, then query both ChromaDB collections. Merge results, deduplicating by doc_id.
2. **Cross-encoder reranking**: `reranker.rerank()` using `cross-encoder/ms-marco-MiniLM-L-6-v2`. Produces a sigmoid-normalized `rerank_score`.
3. **Deduplicate by capture**: Multiple chunks from the same capture are collapsed to the highest-scoring one.
4. **Return top-K**: Enrich with metadata from SQLite (thumbnail paths etc.).

### 10.2 Timeline (`GET /api/search/timeline?date=`)

Simple chronological query: `metadata_db.fetch_captures_for_day(date)`. No vectors involved.

### 10.3 Related (`GET /api/related/{capture_id}`)

Graph query: `graph_db.get_related()` → bidirectional join on `capture_edges` → returns captures with similarity scores.

### 10.4 Context (`GET /api/context/{capture_id}`)

Temporal context window: `metadata_db.fetch_captures_in_window(center_ts, window_minutes)`. Shows what was happening ±N minutes around a capture.

---

## 11. Intelligence Pipeline — Privacy-Preserving LLM Q&A

**File**: `pipeline/intelligence.py` + `api/routes/ask.py`

This is the most complex retrieval path. It adds a full privacy pipeline before any data leaves the machine.

### 11.1 Full Pipeline

```
User question
    │
    ├─ 1. Dual vector retrieval (text + CLIP)
    ├─ 2. Cross-encoder reranking
    ├─ 3. Self-reference removal (clipboard entries matching the query)
    ├─ 4. Recency boosting (exponential decay, half-weight at ~1 week)
    │
    ├─ 5. Sensitivity filtering (pipeline/sensitivity.py)
    │     → Blocks chunks with PII, financial data, passwords
    │     → Blocks captures from excluded apps/domains
    │     → Threshold configurable (default 0.65)
    │
    ├─ 6. Entity masking (pipeline/entity_masker.py)
    │     → spaCy NER replaces names → [PERSON_1], orgs → [ORG_1], etc.
    │     → Returns entity_map for later restoration
    │
    ├─ 7. Local pre-summarization (optional, via Ollama)
    │     → Compresses long chunks before sending to API
    │
    ├─ 8. Prompt assembly
    │     → Context block + query + session context
    │     → Token cap (default 2000)
    │
    ├─── PREVIEW MODE STOPS HERE (POST /api/ask/preview) ───
    │
    ├─ 9. Frontier API call
    │     → Anthropic / OpenAI / OpenRouter
    │     → System prompt positions Engram as "personal AI memory"
    │     → Session context injected (recent projects, files, apps, insights)
    │
    └─ 10. Entity re-substitution
          → [PERSON_1] → real name in the response
```

### 11.2 Session Context Builder (`ask.py: _build_session_context()`)

Assembles a short natural-language description of the user's current focus:
- Last 60 min of captures → parse window titles → extract projects, files, activities
- Top apps over last 6 hours
- Last 3 days of consolidated insights
- Injected into the system prompt as "CURRENT USER CONTEXT"

### 11.3 Recency Boosting

```python
decay = math.exp(-hours_ago / 168)  # half-weight at ~1 week
c["rerank_score"] = base * (0.7 + 0.3 * decay)
```

Recent captures get up to 30% boost. Captures older than ~1 week contribute at most 70% of their base relevance.

### 11.4 System Prompt

```
You are Engram, a personal AI memory system embedded in the user's computer.
You observe everything the user does — files edited, websites visited, code written,
research done — and build a continuously-updated understanding of their work.

Your job is to answer questions about the user's past activity, current focus,
and accumulated knowledge as if you were a highly observant personal assistant
who has been watching over their shoulder.

Rules:
- Answer directly and specifically. Avoid vague hedging like "it seems you may have".
- If you know the answer from context, state it confidently.
- If context is insufficient, say exactly what you DO know and what's missing.
- Some entities appear as placeholders like [PERSON_1] or [ORG_1] — use them
  naturally; they will be restored to real names before the user sees your answer.
- Never invent facts not present in the context.
```

---

## 12. Consolidation Worker — The "Sleep Cycle"

**File**: `pipeline/consolidation_worker.py`

Runs nightly at 2 AM (configurable). Transforms raw captures into higher-level session summaries.

### 12.1 Algorithm

1. **Fetch** all indexed captures for each un-consolidated day
2. **Group** into sessions: captures separated by ≥ 30 min gap start a new session
3. **Summarize** each session (sessions with < 2 captures are skipped):
   - **Priority 1**: Local Ollama model (free, private)
   - **Priority 2**: Frontier API (OpenRouter/OpenAI)
   - **Priority 3**: Heuristic from app names + window titles + duration
4. **Write** insight to SQLite `insights` table
5. **Embed** summary text and upsert into ChromaDB `insights_embeddings`

### 12.2 Consolidation Prompt

```
You are summarizing a person's digital work session.
Given the following screen captures from a single session, write a concise 2-3 sentence
summary covering: (1) what the person was doing, (2) key topics or projects involved,
(3) any notable decisions or outcomes visible.

Be factual, impersonal, and brief. Output ONLY the summary — no preamble.
```

### 12.3 Heuristic Fallback

When no LLM is available:
```
Session of {duration} minutes primarily in {top_app}. Windows: {title_sample}.
```

### 12.4 Catch-up

On startup, `daemon.state` tracks the last consolidation run. If days were missed, all missing days are consolidated automatically.

---

## 13. Retention & Deletion

**File**: `storage/retention.py`

### 13.1 Age-Based Deletion

Configurable via `storage.retention_days` (default 90). Calls `metadata_db.delete_captures_before(cutoff)`.

**Caveat**: This SQLite-only deletion does **not** call `vector_db.delete_by_capture_ids()`, so ChromaDB vectors for aged-out captures may become orphaned.

### 13.2 Storage Budget Enforcement

Configurable via `storage.max_storage_gb` (default 10). When the `~/.engram` directory exceeds the budget:
1. Fetches oldest captures in batches of 50
2. For each batch, calls `_delete_captures()` which properly removes:
   - ChromaDB vectors (both text + visual collections)
   - SQLite rows (cascades to job_queue)
   - Thumbnail files from disk

### 13.3 Manual Deletion

- `DELETE /api/data?before=` — Only calls `metadata_db.delete_captures_before()` (SQLite). Same orphan caveat as age-based deletion.
- `POST /api/retention/run` — Triggers the full retention policy (age + budget).

---

## 14. Background Jobs & Scheduling

**File**: `daemon/scheduler.py`

Uses APScheduler with `BackgroundScheduler`:

| Job | Schedule | What |
|-----|----------|------|
| Embedding worker | Every `worker_interval_minutes` (default 1 min) | `worker.process_batch()` — drains pending job_queue |
| Consolidation | Cron at `run_hour:run_minute` (default 02:00) | `consolidation_worker.run_consolidation()` |
| Collectors | Various intervals | Screenshots (30s), clipboard (2s), browser history (15m), etc. |

**Note**: Retention is **not** registered as a cron job in the scheduler. It's manual-only via `POST /api/retention/run`.

**State tracking** (`daemon/state.py`): Persists last-run timestamps for catch-up logic.

---

## 15. API Surface

**File**: `api/main.py` + `api/routes/*.py`

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/search` | Semantic search (text + CLIP, rerank) |
| GET | `/api/search/timeline?date=` | Chronological captures for a date |
| GET | `/api/related/{capture_id}` | Graph-connected captures |
| GET | `/api/context/{capture_id}` | Temporal window ±N minutes |
| POST | `/api/ask/preview` | Privacy pipeline preview (no API call) |
| POST | `/api/ask` | Full retrieval + LLM answer |
| POST | `/api/capture/manual` | Force immediate screenshot + clipboard capture |
| GET | `/api/status` | System stats (counts, queue, storage) |
| GET | `/api/insights` | Consolidated session summaries (optional `?date=`) |
| GET | `/api/insights/latest` | Most recent insight |
| GET | `/api/activity/apps?from=&to=` | App usage time breakdown |
| GET | `/api/activity/focus?date=` | Focus sessions for a day |
| GET | `/api/activity/heatmap?weeks=` | Capture count heatmap grid |
| GET | `/api/config` | Read config.yaml |
| PUT | `/api/config` | Update config.yaml |
| DELETE | `/api/data?before=` | Bulk delete captures before a date |
| POST | `/api/retention/run` | Execute retention policy |
| GET | `/api/auth/status` | Auth status (PIN configured, locked) |
| POST | `/api/auth/unlock` | Unlock with PIN |
| POST | `/api/auth/lock` | Lock the session |
| POST | `/api/logs` | Frontend session logging |

### 15.1 Auth Middleware

**File**: `api/middleware/auth.py`

Optional PIN-based local auth. When `privacy.require_local_auth: true`:
- Frontend shows a LockScreen
- `POST /api/auth/unlock` with correct PIN returns a session token
- Token attached to all subsequent requests via `X-Engram-Session` header

---

## 16. MCP Server

**File**: `mcp_server.py`

Optional MCP tool server (port 8766) exposing Engram's retrieval to Claude Desktop, Cursor, etc.

| Tool | Purpose |
|------|---------|
| `engram_search` | Semantic search via the same dual-vector + rerank pipeline |
| `engram_ask` | Full Ask pipeline with privacy filtering |
| `engram_timeline` | Day timeline of captures |
| `engram_insights` | Consolidated session summaries |

---

## 17. Frontend

**Stack**: React 19 + Vite 8 + TypeScript 5.9 + Tailwind CSS 4 + Zustand + Framer Motion

### 17.1 Views

| View | Component | Purpose |
|------|-----------|---------|
| Search | `SearchBar` + `ResultCard` + `FilterSidebar` | Semantic search with source/date filters |
| Ask | `ChatView` + `SensitivityModal` | LLM Q&A with privacy preview |
| Timeline | `TimelineView` | Day-by-day chronological browse |
| Activity | `ActivityDashboard` | App time bars, focus sessions, capture heatmap |
| Insights | `InsightsView` | Expandable session summary cards |
| Settings | `Settings` | Capture intervals, retention, storage, danger zone |
| Detail | `DetailModal` | Full capture view with context window + related captures |
| Auth | `LockScreen` | PIN entry when local auth is enabled |

### 17.2 State Management

Single Zustand store (`store/useStore.ts`) managing: current view, search query/results/loading, filters, selected capture, timeline date, daemon status, chat messages, and sensitivity preview state.

### 17.3 API Client

`api/client.ts` — Axios with interceptors for timing and logging. Proxied to `localhost:8765` in dev.

---

## 18. Configuration Reference

**File**: `config/config.yaml`

### Capture

| Key | Default | Purpose |
|-----|---------|---------|
| `screenshot_interval_seconds` | 30 | Screenshot collector interval |
| `clipboard_poll_seconds` | 2 | Clipboard poll interval |
| `manual_hotkey` | `ctrl+shift+m` | Global hotkey for immediate capture |
| `excluded_apps` | [1password, keepass, bitwarden, kwallet] | Never capture these apps |
| `excluded_domains` | [bankofamerica.com, chase.com, *.gov, localhost] | Never index these URLs |
| `suppress_incognito` | true | Skip incognito/private browser windows |
| `watched_directories` | [~/Documents, ~/Desktop, ~/Downloads] | Filesystem watcher paths |
| `watched_extensions` | [.txt, .md, .pdf, .docx, .py, .js, ...] | File types to index |

### Storage

| Key | Default | Purpose |
|-----|---------|---------|
| `base_path` | `~/.engram` | Root for all data |
| `keep_raw_screenshots` | false | Keep full-res screenshots after processing |
| `thumbnail_size` | 1024 | Max thumbnail dimension (px) |
| `retention_days` | 90 | Auto-delete captures older than this |
| `max_storage_gb` | 10 | Storage budget cap |

### Embedding

| Key | Default | Purpose |
|-----|---------|---------|
| `text_model` | `all-MiniLM-L6-v2` | Sentence-transformers model |
| `visual_model` | `ViT-B-32` | CLIP model |
| `visual_pretrained` | `openai` | CLIP pretrained weights |
| `reranker_model` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Cross-encoder for reranking |
| `batch_size` | 16 | Embedding batch size |
| `worker_interval_minutes` | 1 | Cold-path worker frequency |
| `retrieval_top_k` | 50 | Candidates fetched from ChromaDB |
| `result_top_n` | 10 | Final results after reranking |

### Chunking

| Key | Default | Purpose |
|-----|---------|---------|
| `chunk_size` | 512 | Max tokens per chunk |
| `chunk_overlap` | 64 | Overlap between chunks |

### Intelligence

| Key | Default | Purpose |
|-----|---------|---------|
| `api_provider` | `openrouter` | Provider: openrouter, anthropic, openai, disabled |
| `api_model` | `openai/gpt-4o-mini` | Default model |
| `api_model_deep` | `openai/gpt-4o-mini` | Deep mode model |
| `local_summarizer` | `""` | Ollama model for pre-summarization |
| `sensitivity_threshold` | 0.65 | Chunk blocking threshold |
| `max_context_tokens` | 2000 | Token cap for API context |
| `confirm_before_send` | true | Show preview before API call |

### Consolidation

| Key | Default | Purpose |
|-----|---------|---------|
| `run_hour` | 2 | Nightly run hour (24h) |
| `run_minute` | 0 | Nightly run minute |
| `session_gap_minutes` | 30 | Gap that starts a new session |

### MCP

| Key | Default | Purpose |
|-----|---------|---------|
| `enabled` | false | Enable MCP server |
| `host` | `127.0.0.1` | MCP bind address |
| `port` | 8766 | MCP port |

---

## 19. File Inventory

### Storage

| File | Role |
|------|------|
| `storage/metadata_db.py` | SQLite: captures, job_queue, insights, edges, tags — schema + CRUD |
| `storage/vector_db.py` | ChromaDB: text, visual, insights collections — upsert + query + recovery |
| `storage/graph_db.py` | Semantic graph: edge building, tag writes, related queries |
| `storage/retention.py` | Age + budget-based cleanup across SQLite, ChromaDB, and filesystem |

### Pipeline

| File | Role |
|------|------|
| `pipeline/worker.py` | Cold-path batch processor: OCR → chunk → embed → tag → graph → status |
| `pipeline/embedder.py` | Dual embedding engine: MiniLM (text) + CLIP (visual) |
| `pipeline/chunker.py` | Text chunking: 512-token sliding window with 64-token overlap |
| `pipeline/ocr.py` / `pipeline/ocr_fast.py` | OCR extraction (RapidOCR) |
| `pipeline/screenshot_analyzer.py` | Content-type classification and structured text extraction from screenshots |
| `pipeline/context_parser.py` | Window title → project/file/activity parsing |
| `pipeline/reranker.py` | Cross-encoder reranking: ms-marco-MiniLM-L-6-v2 |
| `pipeline/intelligence.py` | Privacy-preserving LLM pipeline: filter → mask → compress → call → unmask |
| `pipeline/sensitivity.py` | Sensitivity scoring and chunk blocking |
| `pipeline/entity_masker.py` | spaCy NER: extraction, masking ([PERSON_1]), and restoration |
| `pipeline/consolidation_worker.py` | Nightly session grouping → LLM/heuristic summaries → insights |
| `pipeline/queue_manager.py` | Enqueue helper for collectors |
| `pipeline/encryptor.py` | Optional Fernet/AES-256 encryption at rest |

### Collectors

| File | Role |
|------|------|
| `collectors/screenshot.py` | Periodic screenshot capture with phash dedup |
| `collectors/clipboard.py` | Clipboard polling with content dedup |
| `collectors/browser_history.py` | Chrome/Firefox/Edge history scraping |
| `collectors/filesystem.py` | Watchdog-based file event indexing |
| `collectors/git_diff.py` | Git commit diff capture |
| `collectors/shell_history.py` | Bash/zsh/PowerShell history capture |
| `collectors/window_context.py` | Active window title + app tracking |

### API

| File | Role |
|------|------|
| `api/main.py` | FastAPI app, CORS, static files, router mounting |
| `api/routes/search.py` | Search + timeline + related + context endpoints |
| `api/routes/ask.py` | Ask preview + full Ask endpoints |
| `api/routes/capture.py` | Manual capture + status endpoints |
| `api/routes/insights.py` | Insight listing endpoints |
| `api/routes/activity.py` | App time, focus sessions, heatmap endpoints |
| `api/routes/config.py` | Config read/write + data deletion + retention |
| `api/routes/logs.py` | Frontend session log ingestion |
| `api/middleware/auth.py` | PIN-based local authentication |

### Daemon

| File | Role |
|------|------|
| `daemon/scheduler.py` | APScheduler: worker, consolidation, collectors |
| `daemon/state.py` | Persistent state for catch-up logic |
| `daemon/tray.py` | System tray icon (pystray) |

### Frontend

| File | Role |
|------|------|
| `frontend/src/App.tsx` | Main app shell, routing, header, keyboard shortcuts |
| `frontend/src/api/client.ts` | Axios API client with all endpoint functions |
| `frontend/src/store/useStore.ts` | Zustand store for all app state |
| `frontend/src/components/SearchBar.tsx` | Search input with filters toggle |
| `frontend/src/components/ResultCard.tsx` | Result cards grid |
| `frontend/src/components/DetailModal.tsx` | Full capture detail + context + related |
| `frontend/src/components/FilterSidebar.tsx` | Date range + source type filters |
| `frontend/src/components/TimelineView.tsx` | Day timeline browser |
| `frontend/src/components/ChatView.tsx` | Ask/chat interface |
| `frontend/src/components/SensitivityModal.tsx` | Privacy preview before API call |
| `frontend/src/components/ActivityDashboard.tsx` | App time, focus, heatmap |
| `frontend/src/components/InsightsView.tsx` | Session summaries |
| `frontend/src/components/Settings.tsx` | Configuration + danger zone |
| `frontend/src/components/LockScreen.tsx` | PIN entry |

### Root

| File | Role |
|------|------|
| `main.py` | Entry point: init DBs, start collectors, scheduler, API, tray |
| `mcp_server.py` | MCP tool server for external AI integration |
| `config/config.yaml` | All configuration |
| `requirements.txt` | Python dependencies |
| `scripts/install_windows.py` | Windows Task Scheduler installer |

---

## 20. Known Gaps & Limitations

| Gap | Description |
|-----|-------------|
| **Orphaned ChromaDB vectors** | Age-based deletion (`delete_captures_before`) and `DELETE /api/data` only remove SQLite rows — ChromaDB vectors for those captures persist indefinitely |
| **Orphaned graph edges/tags** | `capture_edges` and `capture_tags` reference deleted captures without cascade cleanup in the age-delete path |
| **No automated tests** | No test files found in the project |
| **Retention not scheduled** | `retention.run` is not registered as a cron job in the scheduler; it's manual-only via API |
| **`query_insights` unused** | The vector query over insight summaries is defined but never called from any route or service |
| **No per-capture edit** | Users cannot manually edit a capture's text or metadata; the only write operation is deletion |
| **No graph visualization** | The semantic graph exists in SQLite but is only shown as a list in the UI — no network diagram |
| **Single-user only** | No multi-user support — all data is for the local user |
| **CPU-only ML** | Both embedding models (MiniLM + CLIP) run on CPU; no GPU acceleration configured |
| **`.env.example` is unrelated** | Contains Gmail placeholders from a different project, not Engram API keys |
| **Thumbnail proxy gap** | `api/main.py` may not mount `/thumbs` as static files — thumbnails could fail to load in the frontend dev server |
| **No encryption by default** | `encrypt_at_rest: false` — the `encryptor.py` module exists but is not wired into the default pipeline |
