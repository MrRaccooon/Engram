# Memory layer detailed exploration
_Exported on 5/2/2026 at 17:41:21 GMT+5:30 from Cursor (3.2.16)_

---

**User**

Read everything about the memory layer and how it works. I want to know every inch of it. we need everything in detail from end to end no superficial overview a deep dive only

---

**Cursor**

I'll do a comprehensive deep dive into the memory layer. Let me explore the entire codebase systematically to understand every aspect of it.

Now let me read the remaining critical files that weren't fully captured in the exploration.

Now let me also check how the overlay path injects memory.

Now I have a complete picture. Here's the full deep dive.

---

# The Memory Layer: Complete Deep Dive

## 1. Architecture Overview

The memory layer is a **user personalization system** that learns facts about the user from their prompt optimization history and injects those facts back into future optimizations. It operates across four layers:

| Layer | Technology | Role |
|---|---|---|
| **Database** | Supabase Postgres (`user_memory` table) | Persistent storage of key-value facts, scoped per user via RLS |
| **Electron Main Process** | TypeScript (`MemoryService`, `MemoryAnalyzer`) | CRUD persistence, context building, orchestration of extraction |
| **Python Service** | FastAPI + LiteLLM (`/extract-memory-facts`) | LLM-powered fact extraction from raw prompts + optimized output |
| **Frontend** | React (`MemoryView`) | Settings UI for viewing, editing, adding, deleting facts + toggle |

There are **no vectors, no embeddings, no semantic search** on user memory. It's a straightforward key-value store with confidence scoring and source priority. The `pgvector` / embedding infrastructure elsewhere in the repo is for **code intelligence** (Spaces), not this feature.

---

## 2. Database Schema

### Table: `user_memory`

```1:53:c:\Users\Prabhat\Desktop\PROMPT\supabase\migrations\20260315120000_add_user_memory.sql
-- Migration: User Memory System
-- Date: 2026-03-15
-- ...
CREATE TABLE IF NOT EXISTS user_memory (
  id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  memory_type  TEXT        NOT NULL CHECK (memory_type IN ('user', 'behavioral', 'context')),
  key          TEXT        NOT NULL,
  value        TEXT        NOT NULL,
  confidence   FLOAT       NOT NULL DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
  source       TEXT        NOT NULL DEFAULT 'inferred' CHECK (source IN ('inferred', 'explicit')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_user_memory UNIQUE (user_id, memory_type, key)
);
-- ...RLS, index, trigger, permissions
```

**Columns explained:**

- `id` - UUID primary key, auto-generated
- `user_id` - FK to `auth.users`, cascading delete (user deletes account, all memory goes)
- `memory_type` - Enum with 3 buckets:
  - `user` - Identity facts (role, domain, occupation, preferences the user explicitly sets)
  - `behavioral` - Inferred usage patterns (writing style, preferred tone, model preferences)
  - `context` - Recurring topics detected across prompts (frameworks, tools, languages)
- `key` - Snake_case label within a bucket (e.g., `role`, `primary_language`, `preferred_tone`)
- `value` - The factual statement (e.g., "Full-stack developer", "React with TypeScript")
- `confidence` - Float 0.0-1.0. Explicit entries are always 1.0. Inferred entries are capped at 0.95
- `source` - Either `inferred` (auto-extracted by LLM) or `explicit` (user set it manually)
- `observation_count` - How many times the LLM independently observed/confirmed this fact
- `created_at` / `updated_at` - Timestamps, `updated_at` auto-maintained by a PG trigger

**Constraints and indexes:**

- **Unique constraint**: `(user_id, memory_type, key)` - one value per user per type per key, upsert-friendly
- **Index**: `(user_id, memory_type)` for efficient per-user queries
- **RLS**: `user_id = auth.uid()` for both read and write, so users can only see their own memory
- **Permissions**: `authenticated` role gets full CRUD

### Settings column: `memory_enabled`

```1:11:c:\Users\Prabhat\Desktop\PROMPT\supabase\migrations\20260323_add_memory_enabled_to_settings.sql
-- Adds a boolean toggle for the memory system.
-- When true (default), the app auto-learns facts from usage.
-- When false, no automatic fact extraction occurs.
ALTER TABLE user_settings
  ADD COLUMN IF NOT EXISTS memory_enabled BOOLEAN NOT NULL DEFAULT true;
```

---

## 3. Core Backend Service: `MemoryService`

This is the data-access layer. Every read/write to `user_memory` goes through it.

### Types

```7:29:c:\Users\Prabhat\Desktop\PROMPT\electron\main\database\services\memory-service.ts
export type MemoryType = 'user' | 'behavioral' | 'context';
export type MemorySource = 'inferred' | 'explicit';

export interface MemoryEntry {
  id: string;
  user_id: string;
  memory_type: MemoryType;
  key: string;
  value: string;
  confidence: number;
  source: MemorySource;
  observation_count: number;
  created_at: string;
  updated_at: string;
}

export interface UserMemoryMap {
  user: Record<string, MemoryEntry>;
  behavioral: Record<string, MemoryEntry>;
  context: Record<string, MemoryEntry>;
}
```

### Key methods

**`getMemory()`** - Fetches all entries for the authenticated user, grouped into three buckets. This powers both the UI and the context builder.

**`upsertMemoryEntry()`** - Single-entry upsert used by the IPC handler when users manually add/edit a fact. Uses the `UNIQUE(user_id, memory_type, key)` constraint for conflict resolution. Resets `observation_count` to 1.

**`hasExplicitEntry()`** - Checks if a user-set (explicit) entry exists for a given `(memory_type, key)`. This is the gatekeeper that prevents inferred facts from overwriting user-set facts.

**`upsertInferredEntry()`** - Single inferred upsert with explicit-priority check and observation count incrementing. Used by the old single-fact path.

**`upsertInferredEntries()`** - **Batch upsert** optimized to only 3 DB round-trips regardless of fact count:
1. Fetch all explicit entries (to build a skip-set)
2. Fetch existing entries (to increment observation counts)
3. Single batch upsert of all eligible rows

This is the primary write path for automated fact extraction.

**`deleteMemoryEntry()`** - Deletes by `(user_id, memory_type, key)`, RLS-enforced.

### Sanitization (prompt injection defense)

```230:245:c:\Users\Prabhat\Desktop\PROMPT\electron\main\database\services\memory-service.ts
  private static sanitizeField(raw: string, maxLen: number): string {
    return raw
      .slice(0, maxLen)
      .replace(/\[|\]|\{|\}/g, '')                          // remove bracket markers
      .replace(/SYSTEM\s*:/gi, '')                          // strip SYSTEM: prefixes
      .replace(/IGNORE\s+(ALL\s+)?PREVIOUS/gi, '')          // strip jailbreak phrases
      .replace(/OVERRIDE\s*:/gi, '')
      .replace(/INSTRUCTION\s*:/gi, '')
      .trim();
  }
```

Every key and value is sanitized before storage and before injection into prompts. Limits: `MAX_KEY_LEN = 60`, `MAX_VALUE_LEN = 300`.

### Context builder: `buildMemoryContext()`

This is the critical method that transforms stored facts into a string injected into the optimizer's system prompt.

```259:344:c:\Users\Prabhat\Desktop\PROMPT\electron\main\database\services\memory-service.ts
// ... buildMemoryContext() ...
```

How it works:
1. Loads all memory entries via `getMemory()`
2. Filters to entries with `confidence >= 0.5`
3. Splits `user`-type entries into **persona** keys (occupation, role, job_title, profession, industry, expertise, background, identity, title, position) vs **preference** keys
4. Builds labeled sections:
   - `[USER PERSONA]` - Background context only, with a strict directive: "NEVER use to change the type or purpose of the prompt"
   - `[USER PREFERENCES]` - "Apply where relevant to enrich the optimized prompt"
   - Usage Patterns from `behavioral` entries
   - Recurring Themes from `context` entries
5. **Hard cap of 800 characters** on the combined output to prevent overwhelming the system prompt

---

## 4. Orchestration: `MemoryAnalyzer`

This is the brain that decides **when** to extract facts and coordinates the extraction pipeline.

```17:143:c:\Users\Prabhat\Desktop\PROMPT\electron\main\database\services\memory-analyzer.ts
// ... MemoryAnalyzer class ...
```

### Throttling mechanism

```23:27:c:\Users\Prabhat\Desktop\PROMPT\electron\main\database\services\memory-analyzer.ts
  private optimizationsSinceLastRun = 0;
  private readonly runEveryN = 3;
```

Extraction only runs every **3rd** successful optimization. This prevents hammering the LLM endpoint during rapid-fire usage. The counter is in-memory, so it resets on app restart.

### `onNewOptimization()` - Entry point

Called fire-and-forget after every successful optimization in `chat-handlers.ts` and `overlay-ipc-handlers.ts`. Flow:
1. Increment counter. If < 3, return immediately
2. Reset counter to 0
3. Call `extractAndUpsert()` (wrapped in try/catch - never blocks the main flow)

### `extractAndUpsert()` - Core pipeline

1. **Check toggle** - Calls `isMemoryEnabled()` which reads `user_settings.memory_enabled`. Defaults to `true` (fail-open)
2. **Call Python** - `pythonClient.extractMemoryFacts(rawPrompt, optimizedText, skill)`
3. **Filter** - Only accepts facts with valid categories (`user`, `behavioral`, `context`)
4. **Batch upsert** - Via `MemoryService.upsertInferredEntries()`, respecting explicit priority
5. **Push to UI** - Sends `memory:updated` event to the app window with the full updated `UserMemoryMap`

---

## 5. Python Extraction Endpoint

### Models & Pydantic schemas

```1086:1100:c:\Users\Prabhat\Desktop\PROMPT\python-service\app\main.py
class MemoryFact(BaseModel):
    category: str    # 'user' | 'behavioral' | 'context'
    key: str         # short label, e.g. "role", "preferred_tone"
    value: str       # the fact, e.g. "Senior Backend Engineer"
    confidence: float  # 0.0 – 1.0

class ExtractMemoryRequest(BaseModel):
    raw_prompt: str = Field(..., max_length=50_000)
    optimized_text: str = Field(..., max_length=50_000)
    skill: Optional[str] = Field(None, max_length=200)

class ExtractMemoryResponse(BaseModel):
    facts: List[MemoryFact]
```

### System prompt

```1103:1124:c:\Users\Prabhat\Desktop\PROMPT\python-service\app\main.py
MEMORY_EXTRACTION_SYSTEM_PROMPT = """You are a silent profiling engine. Given a user's raw prompt and the optimized version, extract factual observations about the user.

Return a JSON array of facts. Each fact must have:
- "category": one of "user" (identity, role, domain), "behavioral" (writing style, preferences), or "context" (recurring topics/tools)
- "key": a short snake_case label (e.g. "role", "primary_language", "preferred_tone", "domain")
- "value": a concise factual statement
- "confidence": 0.0 to 1.0 (how certain you are)

Rules:
- Only extract facts you are reasonably confident about (>= 0.5)
- Do NOT invent facts — only observe what is clearly present in the prompt
- Do NOT extract generic observations (e.g. "uses AI" is useless)
- Prefer specific, actionable facts (e.g. "works with React and TypeScript" over "is a developer")
- Return an empty array [] if nothing meaningful can be inferred
- Return ONLY the JSON array, no markdown, no explanation
```

### Endpoint logic

```1127:1220:c:\Users\Prabhat\Desktop\PROMPT\python-service\app\main.py
@app.post("/extract-memory-facts", response_model=ExtractMemoryResponse)
async def extract_memory_facts(request: ExtractMemoryRequest, auth: dict = Depends(require_auth)):
    // ...
```

Key details:
- **Model**: `MEMORY_MODEL` (defaults to `SUMMARIZER_MODEL`, which defaults to `openrouter/openai/gpt-4o-mini`)
- **Temperature**: 0.1 (near-deterministic)
- **Max tokens**: 300 (compact output)
- **Input**: Raw prompt + optimized text concatenated, plus optional detected skill
- **Minimum prompt length**: 10 characters (short prompts are skipped)
- **Confidence cap**: All inferred confidences are capped at **0.95** so explicit entries (1.0) always win
- **JSON parsing**: Strips markdown code fences if present, validates each fact has valid category, non-empty key/value, and confidence in [0.0, 1.0]
- **Usage logging**: Logged as `memory_extraction` call type for cost tracking
- **Auth**: Protected by `require_auth` (Supabase JWT verification)
- **Error handling**: Returns empty facts array on any failure (JSON parse error, LLM failure, etc.)

### Model configuration

```11:14:c:\Users\Prabhat\Desktop\PROMPT\python-service\pipeline_v2\config.py
OPTIMIZER_MODEL  = os.environ.get("OPTIMIZER_MODEL",  "openrouter/openai/gpt-4o-mini")
SUMMARIZER_MODEL = os.environ.get("SUMMARIZER_MODEL", "openrouter/openai/gpt-4o-mini")
TITLE_MODEL      = os.environ.get("TITLE_MODEL",      SUMMARIZER_MODEL)
MEMORY_MODEL     = os.environ.get("MEMORY_MODEL",     SUMMARIZER_MODEL)
```

---

## 6. IPC Bridge (Electron Main <-> Renderer)

### Preload exposure

```865:881:c:\Users\Prabhat\Desktop\PROMPT\electron\preload.ts
  memory: {
    get: () => ipcRenderer.invoke('memory:get'),
    upsert: (data) => ipcRenderer.invoke('memory:upsert', data),
    delete: (data) => ipcRenderer.invoke('memory:delete', data),
    getEnabled: () => ipcRenderer.invoke('memory:get-enabled'),
    setEnabled: (enabled) => ipcRenderer.invoke('memory:set-enabled', enabled),
    onUpdated: (callback) => {
      const handler = (_event, data) => callback(data);
      ipcRenderer.on('memory:updated', handler);
      return () => ipcRenderer.removeListener('memory:updated', handler);
    },
  },
```

### IPC Handlers (`user-handlers.ts`)

Six IPC channels:

| Channel | Direction | Purpose |
|---|---|---|
| `memory:get` | invoke (req/res) | Fetch all memory grouped by type |
| `memory:upsert` | invoke (req/res) | Create/update a fact (UI defaults to `source: 'explicit'`, `confidence: 1.0`) |
| `memory:delete` | invoke (req/res) | Remove a specific fact by `(memoryType, key)` |
| `memory:get-enabled` | invoke (req/res) | Check if auto-learning toggle is on |
| `memory:set-enabled` | invoke (req/res) | Toggle auto-learning on/off |
| `memory:updated` | event (push) | Main -> renderer after background extraction completes |

All handlers are guarded by `authState.isAuthenticated` and wrapped in Sentry error capture.

---

## 7. Chat Integration (How Memory Gets Used)

Memory is injected into the optimization pipeline at **four** distinct code paths in `chat-handlers.ts`:

1. **`app:optimize-prompt`** (main chat optimization)
2. **`app:send-message`** (send with attachment/chat message)
3. **`app:regenerate-prompt`** (regeneration)
4. **Overlay optimize** (in `overlay-ipc-handlers.ts`)

All four paths follow the identical pattern:

```258:267:c:\Users\Prabhat\Desktop\PROMPT\electron\main\ipc-handlers\chat-handlers.ts
            // Append user memory context (guarded by toggle, non-blocking, silent failure)
            if (authState.isAuthenticated && memory && analyzer) {
                try {
                    const memEnabled = await analyzer.isMemoryEnabled();
                    if (memEnabled) {
                        const memCtx = await memory.buildMemoryContext();
                        if (memCtx) customInstructions = customInstructions ? `${customInstructions}\n\n${memCtx}` : memCtx;
                    }
                } catch { /* non-critical */ }
            }
```

Memory context is appended to `customInstructions` (which may already contain `user_settings.prompt_instructions`), then passed to `pythonClient.optimizeStream()`.

After each successful optimization, the analyzer fires:

```329:337:c:\Users\Prabhat\Desktop\PROMPT\electron\main\ipc-handlers\chat-handlers.ts
                    // Trigger background memory analysis (fire-and-forget, non-critical)
                    if (analyzer && authState.currentUserId) {
                        analyzer.onNewOptimization(
                            authState.currentUserId,
                            promptText,
                            optimizedText,
                            null,
                        ).catch((e: unknown) =>
```

### On the Python side (optimizer.py)

When `custom_instructions` reaches the optimizer, it's appended to the system prompt with explicit anti-override language:

```485:490:c:\Users\Prabhat\Desktop\PROMPT\python-service\pipeline_v2\optimizer.py
        if custom_instructions and custom_instructions.strip():
            system_prompt += (
                f"\n\nUSER CUSTOM INSTRUCTIONS (Apply these as secondary enrichment to the optimized prompt. "
                f"Do not let user memory override the user's explicit intent):\n"
                f"{custom_instructions.strip()}"
            )
```

---

## 8. Frontend UI: `MemoryView`

The entire memory frontend is a single component under Settings > Memory tab.

### Component hierarchy

```
SettingsView
  └── TabsContent value="memory"
       └── MemoryView
            ├── Header (Brain icon, total fact count)
            ├── ToggleSwitch ("Learn from my usage")
            └── MemorySection × 3 (user, behavioral, context)
                 ├── Collapsible header (icon, label, fact count)
                 ├── MemoryRow × N (per entry)
                 │    ├── Key label
                 │    ├── ConfidenceBadge
                 │    ├── Value text (or inline edit input)
                 │    └── Edit/Delete buttons (hover reveal)
                 └── "Add fact" button → key/value inputs
```

### State management

All local React state, no global store:
- `memory: UserMemoryMap` - The three-bucket fact map
- `loading: boolean` - Initial load spinner
- `memoryEnabled: boolean` - Toggle state
- `togglingMemory: boolean` - Prevents double-toggle

### Live sync

```289:295:c:\Users\Prabhat\Desktop\PROMPT\app-ui\src\components\settings\MemoryView.tsx
  useEffect(() => {
    if (!isElectron || !window.electronAPI.memory.onUpdated) return;
    const cleanup = window.electronAPI.memory.onUpdated((updatedMemory: UserMemoryMap) => {
      setMemory(updatedMemory);
    });
    return cleanup;
  }, [isElectron]);
```

When the `MemoryAnalyzer` finishes background extraction and pushes `memory:updated`, the UI updates in real-time without manual refresh.

### User actions

- **Toggle** - Calls `memory.setEnabled()`, shows toast confirmation
- **Edit** - Inline input with Enter to save, Escape to cancel. Upserts with `source: 'explicit'` (which bumps confidence to 1.0 and prevents future inferred overwrites)
- **Delete** - Optimistic removal from local state after IPC success
- **Add** - Key + value inputs, saved as explicit with confidence 1.0

### ConfidenceBadge

Displays either "You set this" (for explicit entries) or a "X% confident" badge with color coding (green for >= 70%, amber otherwise). If `observation_count > 1`, shows an eye icon with the count.

---

## 9. Priority System (Explicit vs Inferred)

This is the most important design decision in the memory layer:

1. **Explicit entries always win** - If a user sets `role = "Data Scientist"`, no amount of inferred observations will overwrite it
2. **Confidence capping** - Python caps all inferred confidence at **0.95**, so explicit entries at **1.0** are always higher
3. **Skip-on-conflict** - `upsertInferredEntries()` builds a set of all explicit `(memory_type, key)` pairs and skips any inferred fact that collides
4. **Edit promotes to explicit** - When a user edits any fact (even an inferred one), it becomes `source: 'explicit'` with `confidence: 1.0`, permanently locking it from automated updates

---

## 10. The Complete Data Flow (End to End)

```
User types a prompt
        │
        ▼
[Electron Renderer] → IPC 'app:optimize-prompt'
        │
        ▼
[Electron Main - chat-handlers.ts]
  1. Fetch user_settings.prompt_instructions
  2. Check memory toggle via analyzer.isMemoryEnabled()
  3. If enabled: memory.buildMemoryContext()
     → Load all user_memory entries
     → Filter by confidence >= 0.5
     → Split persona vs preferences
     → Format with [USER PERSONA] / [USER PREFERENCES] headers
     → Cap at 800 chars
  4. Merge into customInstructions string
        │
        ▼
[PythonClient.optimizeStream()]
  → POST to Python service with customInstructions
        │
        ▼
[Python optimizer.py]
  → Appends customInstructions to system prompt as
    "USER CUSTOM INSTRUCTIONS (Do not let user memory override...)"
  → LLM generates optimized prompt
        │
        ▼
[Back in Electron Main]
  5. Save optimization record to DB
  6. Fire-and-forget: analyzer.onNewOptimization()
     → Increment counter (skip if < 3)
     → Every 3rd call:
        a. Check memory toggle again
        b. pythonClient.extractMemoryFacts(raw, optimized, skill)
           → POST /extract-memory-facts
           → LLM extracts JSON array of facts
           → Each fact: {category, key, value, confidence}
           → Confidence capped at 0.95
        c. memoryService.upsertInferredEntries(facts)
           → 3 DB queries: fetch explicit set, fetch existing counts, batch upsert
           → Skip facts blocked by explicit entries
           → Increment observation_count for re-seen facts
        d. appWindowManager.send('memory:updated', updatedMemory)
           → UI refreshes in real-time
        │
        ▼
[Renderer receives optimized text + optional memory:updated event]
```

---

## 11. Configuration Summary

| Setting | Where | Default | Purpose |
|---|---|---|---|
| `MEMORY_MODEL` | Env var → `config.py` | `SUMMARIZER_MODEL` (gpt-4o-mini) | LLM used for fact extraction |
| `user_settings.memory_enabled` | DB column | `true` | User toggle for auto-learning |
| `runEveryN` | Hardcoded in `MemoryAnalyzer` | `3` | Extract facts every Nth optimization |
| `MAX_KEY_LEN` | Hardcoded in `MemoryService` | `60` | Max characters for fact keys |
| `MAX_VALUE_LEN` | Hardcoded in `MemoryService` | `300` | Max characters for fact values |
| Context cap | Hardcoded in `buildMemoryContext` | `800` chars | Max injected context size |
| Min confidence | Hardcoded in `buildMemoryContext` | `0.5` | Threshold for inclusion in prompts |
| Inferred confidence cap | Hardcoded in Python endpoint | `0.95` | Prevents inferred from matching explicit |
| LLM temperature | Hardcoded in Python endpoint | `0.1` | Near-deterministic extraction |
| LLM max_tokens | Hardcoded in Python endpoint | `300` | Compact fact output |
| Raw/optimized input cap | Pydantic field limit | `50,000` chars each | Prevents oversized extraction requests |

---

## 12. What's NOT in the Memory Layer

- **No vector embeddings** on user memory (semantic search is code-intelligence only)
- **No TTL / decay** - Facts never expire automatically
- **No search/filter** in the UI
- **No global state management** (no Zustand/Redux store for memory)
- **No dedicated REST API** - Everything goes through Electron IPC + Supabase client
- **No background workers / queues** - Fire-and-forget promises only
- **No tests** - No test files found for memory functionality
- **No compaction / maintenance jobs** - Facts accumulate indefinitely

---

**User**

create a document out of it

---

**Cursor**

I'll create a comprehensive document from the deep dive analysis.

Created `docs/MEMORY_LAYER_DEEP_DIVE.md` — a comprehensive document covering every aspect of the memory layer from database schema to UI components. It includes:

- Full architecture diagrams (ASCII) showing all four layers
- Complete database schema with column reference, indexes, constraints, RLS, and triggers
- Every method in `MemoryService` and `MemoryAnalyzer` explained
- The Python extraction endpoint logic, system prompt, and model configuration
- All 6 IPC channels and their contracts
- How memory integrates with the chat/optimization pipeline (injection + extraction)
- The complete frontend component hierarchy and state management
- The explicit-vs-inferred priority system with a decision matrix
- Two end-to-end data flow diagrams (injection and extraction)
- Security and sanitization mechanisms
- A full configuration reference (env vars, DB settings, hardcoded constants)
- Complete file inventory (10 core files, 4 migrations, 2 bridge files, 3 frontend files)
- 11 known gaps and limitations

---

**User**

There's another [roject on this system called Engram I want you to do the same thing with it as well and understand its memory working properly or atleast how it was supposed to work and make a seperate doc on it in Engram repo itself. same deep dive repeat from end to end and A comparison doc as well

---

**Cursor**

Let me first find the Engram project on your system, then do a full deep dive into its memory layer.

Found it — `C:\Users\Prabhat\Desktop\Engram`. Let me now do the full deep dive.

Now let me read the core backend files in full to get every detail.

Now let me read the remaining key files to complete the picture.

Now I have all the data. Let me create both documents.

Now let me create the comparison document.

All three documents are created. Here's what was produced:

### 1. `C:\Users\Prabhat\Desktop\Engram\MEMORY_DEEP_DIVE.md`
Complete deep dive into Engram's memory layer covering all 20 sections: architecture overview, the 4-layer memory model (raw captures, searchable vectors, consolidated insights, semantic graph), full SQLite schema (5 tables), ChromaDB collections (3 collections with 384d/512d vectors), the collector pipeline (7 collectors), the cold-path worker (OCR → chunk → embed → tag → graph), dual embedding engine (MiniLM + CLIP), the retrieval pipeline (search, timeline, related, context), the intelligence pipeline (7-step privacy-preserving RAG), nightly consolidation, retention/deletion, scheduling, API surface (16+ endpoints), MCP server, frontend, full configuration reference, complete file inventory, and known gaps.

### 2. `C:\Users\Prabhat\Desktop\Engram\MEMORY_COMPARISON_PROMPT_VS_ENGRAM.md`
Side-by-side comparison across 17 dimensions including: core philosophy ("learn who the user is" vs "remember everything the user does"), data model (key-value facts vs event captures + vectors), creation (LLM extraction vs passive collectors), storage (Supabase cloud vs SQLite + ChromaDB local), retrieval (load-all vs dual-vector RAG), LLM integration (prompt injection vs privacy-preserving RAG), user control, priority systems, graph/associations, background processing, privacy/security, frontend, consolidation, configuration, scale characteristics, what each could learn from the other, and shared gaps.

---

**User**

So based on all the research that u have done today its main purpose is that i want to get better at building a memory system. It should be impeccable one of the best ever built depending on what the app requires but I feel i am doing something but still its not the best. I want you to give me ur best hypothesis about where i am at right now. where i can be. how i can get there. what specific things to improve. where to improve. what knowledge to gain first. what to advance further in, what topics to know inside out everything

---

**Cursor**

This is the question I was hoping you'd ask, because having read both codebases line-by-line, I have a very clear picture of where you are and where the gaps are. I'll be direct.

---

## Where You Are Right Now

You're in the **competent implementer** zone. You can architect a working memory system, wire together the right components, and ship something functional. That's not nothing — most people can't do that. But both PROMPT and Engram reveal the same pattern: you build the **mechanical parts** correctly but miss the **cognitive science** underneath.

Here's what the code tells me about your current strengths:

**You understand:**
- How to structure persistence layers (SQLite, Supabase, ChromaDB — all used correctly)
- The concept of dual embeddings and cross-modal retrieval (Engram's text + CLIP is solid)
- Pipeline architecture (collectors → queue → worker → index is a clean separation)
- Priority systems and user agency (PROMPT's explicit-overrides-inferred is well designed)
- Privacy as a first-class concern (Engram's entity masking + sensitivity pipeline, PROMPT's sanitization)
- Cost management (throttling extraction to every 3rd optimization)

**But the code also reveals what you don't yet know you don't know.**

---

## The Honest Gaps (What's Actually Holding You Back)

### Gap 1: You treat memory as storage, not as cognition

Both projects store things and retrieve things. Neither project *thinks* about what to remember, when to forget, what matters more over time, or how memories relate to each other beyond cosine similarity.

In PROMPT: you load all facts, filter by confidence ≥ 0.5, and dump them into an 800-char string. There's no concept of "which facts are relevant to *this specific prompt*?" A user's `role: Data Scientist` fact gets injected whether they're writing a data pipeline prompt or asking for a recipe.

In Engram: you retrieve by vector similarity, which is better. But you have no concept of **memory importance** beyond recency decay. A screenshot of you debugging a critical production outage has exactly the same weight as a screenshot of you scrolling Twitter — unless the text content happens to match the query.

**The core problem:** You're building databases with search, not memory systems. A real memory system has *attention*, *salience*, *consolidation*, and *forgetting* — not just storage and retrieval.

### Gap 2: Your retrieval is either too simple or too brute-force

PROMPT loads everything. Engram vector-searches everything. Neither does what human memory does: **context-dependent, associative retrieval with working memory constraints**.

In a truly good memory system:
- The *type* of query should change *how* you retrieve (temporal query → timeline walk, factual query → semantic search, "what was I doing when..." → episodic reconstruction)
- Retrieved memories should **inhibit** related-but-irrelevant memories (the way recalling "Paris" for "capital of France" suppresses "Paris Hilton")
- Context should narrow the retrieval space *before* the vector search, not after

### Gap 3: You have no forgetting mechanism

PROMPT facts accumulate forever. Engram captures accumulate until a retention policy deletes them by age. Neither has **graceful forgetting** — the cognitive process where less-relevant memories lose accessibility over time while important ones are strengthened.

Your `observation_count` in PROMPT is the *seed* of this idea but you never use it. A fact observed 15 times over 3 months is categorically different from one observed once 3 months ago, but they're treated the same in `buildMemoryContext()`.

### Gap 4: Your consolidation is surface-level

Engram's nightly consolidation groups captures by time gap and summarizes them. That's a start. But real memory consolidation does much more:
- **Extracts patterns** across sessions ("this person always debugs by reading logs first, then checking the database")
- **Builds abstractions** ("this person is working on a migration from MongoDB to PostgreSQL" — not just "they had 14 sessions touching database code")
- **Updates the knowledge graph** with higher-order relationships
- **Prunes redundant raw memories** once the abstraction exists

PROMPT has no consolidation at all. The facts are either captured or not — there's no process that looks at 30 inferred facts and says "these 30 observations can be condensed into 5 higher-level truths."

### Gap 5: You haven't solved the context window problem

This is the engineering gap that separates good from great. Both projects hit a wall when they try to inject memory into an LLM:
- PROMPT caps at 800 characters (arbitrary, wasteful if the prompt is short, insufficient if it's complex)
- Engram caps at 2000 tokens (better, but still static)

Neither adapts the memory context to:
- The complexity of the current task
- What the LLM already knows from the conversation
- What the user *just said* that makes certain memories more relevant

### Gap 6: No evaluation or feedback loops

Neither project has any way to know if the memory system is actually helping. When PROMPT injects "role: Senior Backend Engineer" into the optimizer, does the output actually improve? You don't know. When Engram retrieves 10 captures for an Ask query, are they the right 10? You don't know.

Without measurement, you can't improve. You're flying blind.

---

## Where You Can Be

A truly impeccable memory system would have these properties:

1. **Adaptive retrieval** — Different query types trigger different retrieval strategies
2. **Salience scoring** — Not all memories are equal; importance is computed, not assumed
3. **Temporal reasoning** — "What was I doing when X happened?" is a first-class query
4. **Hierarchical memory** — Raw events → sessions → projects → identity (4+ levels, not 2)
5. **Active forgetting** — Memories compete for relevance; losers fade
6. **Context-aware injection** — The memory context adapts to the task at hand
7. **Feedback-driven learning** — The system knows when its memory helped and adjusts
8. **Associative linking** — Beyond cosine similarity; causal, temporal, and conceptual links

---

## How To Get There: The Study Plan

### Phase 1: Foundations You Need to Understand Deeply

**These are non-negotiable. You need to know these inside-out:**

**1. Cognitive science of memory**
- Read: "Memory" by Alan Baddeley (the textbook, not a pop science book)
- Focus on: Working memory model, levels of processing theory, encoding specificity principle, spacing effect, retrieval-induced forgetting
- Why: You're building a computational analog of human memory. If you don't understand the real thing, you'll keep reinventing flat key-value stores

**2. Spaced repetition and forgetting curves**
- Study Ebbinghaus's forgetting curve and the mathematical models behind it
- Study the Leitner system and SM-2 algorithm (Anki uses this)
- Why: This gives you the math for memory decay — when to strengthen, when to let go. Your `observation_count` field is the beginning of this but you never apply decay functions

**3. Vector retrieval — beyond the basics**
- You know how to use embeddings and cosine similarity. Now go deeper:
  - **Hybrid search** (BM25 + dense vectors — you partially do this but not formally)
  - **ColBERT and late interaction models** (token-level similarity, much better than mean pooling)
  - **Hypothetical Document Embeddings (HyDE)** — generate a hypothetical answer, embed that, search with it
  - **Matryoshka embeddings** — variable-dimension vectors for efficiency
  - **Re-ranking cascades** — you use cross-encoder reranking; study how to chain multiple stages
- Why: Your retrieval is the bottleneck in both projects. Making it smarter is the highest-ROI improvement

**4. Knowledge graphs (properly)**
- Your `capture_edges` table is a baby graph. Study:
  - Property graph model (Neo4j style) vs RDF triples
  - Graph neural networks for link prediction
  - Knowledge graph completion and reasoning
  - GraphRAG (Microsoft's approach to graph-enhanced retrieval)
- Why: Cosine similarity gives you "these two things are about similar topics." A proper knowledge graph gives you "this meeting caused this decision which led to this code change which fixed this bug." That's real associative memory.

### Phase 2: Architectural Patterns to Study

**5. MemGPT / Letta architecture**
- Study the MemGPT paper: "MemGPT: Towards LLMs as Operating Systems"
- Key idea: LLM manages its own memory via function calls — decides what to store, retrieve, and forget
- Why: This is the current state of the art for LLM memory management. Your PROMPT system hardcodes when to extract (every 3rd optimization) and what to inject (all facts above 0.5 confidence). MemGPT lets the LLM itself decide.

**6. Retrieval-Augmented Generation (RAG) — advanced patterns**
- Study:
  - **RAPTOR** — recursive abstractive processing for tree-organized retrieval
  - **Self-RAG** — LLM decides whether to retrieve and self-evaluates relevance
  - **Adaptive RAG** — routes queries to different retrieval strategies based on complexity
  - **Corrective RAG (CRAG)** — evaluates retrieved docs and triggers web search if insufficient
- Why: Your Engram Ask pipeline is basic RAG. These patterns would make it dramatically better.

**7. Memory consolidation algorithms**
- Study how the hippocampus replays memories during sleep (computational models)
- Study **complementary learning systems theory** — fast learning (hippocampus/episodic) vs slow learning (neocortex/semantic)
- Why: This directly maps to your Engram architecture — captures are "hippocampal" and insights are "neocortical." But your consolidation is a simple summarizer, not a proper knowledge extractor.

### Phase 3: Specific Technical Skills to Level Up

**8. Evaluation and benchmarks**
- Learn how to evaluate retrieval quality: **MRR, NDCG, MAP, Recall@K**
- Learn how to evaluate memory system helpfulness: A/B testing, user preference studies
- Build a test harness: Given a query and a known-good set of relevant memories, does your system find them?
- Why: You currently have zero tests and zero metrics. This is why you can't tell if your system is good or not.

**9. Prompt engineering for memory extraction**
- Your `MEMORY_EXTRACTION_SYSTEM_PROMPT` is decent but basic
- Study structured extraction techniques: function calling, tool use for extraction, chain-of-thought extraction
- Study how to extract *relations* (not just facts): "user prefers X *because* Y", "user switched from X *to* Y *on date* Z"
- Why: Better extraction → better facts → better personalization

**10. Temporal indexing and reasoning**
- Study time-series databases and temporal query patterns
- Study how to answer "when" and "how often" and "what changed" questions
- Build temporal indexes: not just `ORDER BY timestamp`, but interval trees, event sequences, causal chains
- Why: Both your projects are weak on temporal reasoning. "What was I working on last week?" should be a first-class operation, not a hack.

---

## Concrete Next Steps (What to Actually Do)

If I were you, here's the order I'd attack this in:

**Week 1–2: Read and absorb**
- Read the MemGPT paper
- Read Microsoft's GraphRAG paper
- Read the RAPTOR paper
- Skim Baddeley's working memory model (the Wikipedia article is sufficient to start)

**Week 3–4: Fix PROMPT's memory retrieval**
- Replace "load all facts → dump into string" with semantic retrieval
- Embed user facts into a small vector index
- At optimization time, embed the user's prompt, retrieve the top 3-5 most relevant facts (not all of them)
- Add a dynamic context budget that scales with prompt complexity

**Week 5–6: Add a proper forgetting curve to PROMPT**
- Use `observation_count` and `updated_at` to compute a real relevance score
- Facts that haven't been observed in 30 days should decay
- Facts observed frequently and recently should be boosted
- The 0.5 confidence threshold should be dynamic, not static

**Week 7–8: Fix Engram's deletion consistency and add evaluation**
- Fix the orphaned vector problem (this is a real bug)
- Build a simple evaluation harness: 20 test queries with expected results
- Measure retrieval quality before and after any change

**Week 9–12: Build hierarchical consolidation in Engram**
- Level 1: Raw captures (already exists)
- Level 2: Session summaries (already exists)
- Level 3: Project-level knowledge ("this week's work on the migration project")
- Level 4: Identity-level insights ("this person is a backend engineer who prefers TypeScript and works in fintech")
- Each level feeds the next. Level 4 feeds back into PROMPT-style personalization.

---

## The One Thing That Would Change Everything

If I had to pick the single most impactful thing you could do: **make your memory systems evaluate themselves.**

Right now, you build a feature, eyeball the output, and move on. You have no way to know if your 800-char context cap is helping or hurting. You have no way to know if your recency decay constant (168 hours) is optimal or arbitrary. You have no way to know if your similarity threshold (0.60) for graph edges is too permissive or too restrictive.

Build a simple evaluation loop:
1. Log every memory retrieval (what was queried, what was returned, what was injected)
2. Track downstream quality (did the user accept the optimization? did they regenerate? did they edit heavily?)
3. Correlate: which memory injections led to better outcomes?

That feedback loop is what separates a memory system that's "pretty good" from one that gets **better every day on its own.**

You have all the building blocks. The engineering in both projects is solid. What you need now is the **theory** to know what to build next and the **measurement** to know if it worked.

---

**User**

I want you to do one more deep dive of code-intelligences Memory layer and do the same thing with it as u have done with last 2 extractions get me all detailed things about it

---

**Cursor**

Let me do a full exploration of the code-intelligence service's memory layer.

Good — I have the migrations and service overview. Now let me read the core service files in full to get every detail.

Now let me read the remaining key files — the index worker, query analyzer, query preprocessor, and graph service.

I now have complete knowledge of every file. Here's the full deep dive:

---

# Code-Intelligence Memory Layer — Complete Deep Dive

## What It Is

The code-intelligence service is a **standalone Python FastAPI microservice** (port 8002) that indexes user codebases into a semantic memory layer using pgvector in Supabase Postgres. It gives the prompt optimizer "awareness" of the user's project by parsing code into structural chunks (files, classes, functions, methods), summarizing them with an LLM, embedding them into 512-dimensional vectors, building a call graph, and serving hybrid retrieval queries. This is the "Spaces" feature.

---

## Architecture Stack

| Layer | Technology |
|-------|-----------|
| **API** | FastAPI, Uvicorn, port 8002 |
| **Auth** | Supabase JWT (ES256/RS256 via JWKS, or HS256 fallback) |
| **Database** | Supabase Postgres (service-role key bypasses RLS) |
| **Vector index** | pgvector extension, HNSW index (`m=16, ef_construction=64`) |
| **Full-text index** | PostgreSQL `tsvector` + GIN index with `split_identifiers()` for camelCase/snake_case |
| **Concept index** | GIN index on `text[]` concepts column |
| **Embeddings** | OpenAI `text-embedding-3-small` at 512 dimensions (direct or via OpenRouter) |
| **Summarizer** | `gpt-4.1-nano` via OpenRouter (batch mode, 5 chunks per LLM call) |
| **Query decomposition** | `gpt-4o-mini` via OpenRouter |
| **Async workers** | Optional arq + Redis; fallback to FastAPI BackgroundTasks |
| **Monitoring** | Sentry + SlowAPI rate limiting |
| **Deployment** | Docker (slim Python 3.12) + Render.com |

---

## Database Schema (7 migrations, final state)

### Table: `projects`

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID PK | Project identifier |
| `user_id` | UUID FK → auth.users CASCADE | Owner |
| `name` | text | Project name |
| `repo_url`, `local_path` | text | Source location |
| `current_branch`, `default_branch` | text | Branch tracking |
| `index_status` | text CHECK (indexing, ready, stale, error, partial) | Current state |
| `last_indexed_at` | timestamptz | Last successful index |
| `github_repo_owner`, `github_repo_name` | text | GitHub integration |
| `project_summary` | text | Auto-generated project summary (v6) |
| `tech_stack` | jsonb | Detected tech stack (v6) |

### Table: `code_chunks`

The core memory table — each row is a code element.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID PK | Chunk identifier |
| `project_id` | UUID FK → projects CASCADE | Parent project |
| `user_id` | UUID FK → auth.users CASCADE | Owner |
| `file_path` | text | Source file path |
| `start_line`, `end_line` | integer | Line range in file |
| `chunk_type` | text CHECK (file, class, function, method) | Structural type |
| `name` | text | Symbol name |
| `signature` | text | Function/method signature |
| `language` | text | Programming language |
| `semantic_summary` | text | LLM-generated summary of what the code does |
| `code_body` | text | Actual code (up to 3000 chars) |
| `embedding` | vector(512) | Dense vector from `text-embedding-3-small` |
| `content_hash` | text | For change detection / skip-on-unchanged |
| `concepts` | text[] | Domain tags (e.g., `['auth', 'database', 'API']`) |
| `observation_count` | integer DEFAULT 0 | How often this chunk was retrieved (popularity signal) |
| `is_exported` | boolean DEFAULT false | Whether the symbol is exported/public |
| `branch` | text | Git branch |
| `branch_diverged` | boolean DEFAULT false | Flagged when branch has changed files |
| `search_vector` | tsvector GENERATED | FTS vector with identifier splitting |

**UNIQUE constraint**: `(project_id, file_path, name, chunk_type)`

**Indexes**:
- **HNSW** on `embedding` with cosine distance (`m=16, ef_construction=64`)
- **GIN** on `search_vector` (full-text search)
- **GIN** on `concepts` (concept overlap search)
- **B-tree** on `(project_id, observation_count DESC)` (reranking hot path)
- **B-tree** on `(project_id, branch, chunk_type)` and `(project_id, file_path)`

### Table: `code_relationships`

Typed edges between code chunks forming a call graph.

| Column | Type | Purpose |
|--------|------|---------|
| `from_chunk_id` | UUID FK → code_chunks CASCADE | Caller/importer |
| `to_chunk_id` | UUID FK → code_chunks CASCADE | Callee/imported |
| `relationship_type` | text CHECK (calls, imports, inherits, implements) | Edge type |

**UNIQUE**: `(from_chunk_id, to_chunk_id, relationship_type)`

### Table: `project_branches`

GitHub branch tracking for divergence detection.

| Column | Type | Purpose |
|--------|------|---------|
| `project_id` | UUID FK → projects CASCADE | Parent project |
| `branch_name` | text | Branch name |
| `last_commit_sha`, `last_commit_message` | text | Latest commit info |
| `ahead_of_main_by`, `behind_main_by` | integer | Divergence metrics |
| `diverged_files` | text[] | Files changed vs main |

### RPC Functions (Postgres)

| Function | Purpose |
|----------|---------|
| `match_code_chunks(vector(512), ...)` | Cosine similarity vector search |
| `search_code_chunks_text(text, ...)` | Full-text keyword search with `split_identifiers()` |
| `search_by_concepts(text[], ...)` | Concept tag overlap search (GIN) |
| `search_chunks_by_file_paths(uuid, uuid, text[], ...)` | ILIKE file path matching |
| `increment_observation_counts(uuid[])` | Atomic popularity bump |
| `cleanup_stale_chunks(uuid, jsonb)` | Server-side stale chunk deletion |
| `split_identifiers(text)` | Splits `camelCase` and `snake_case` into words |

---

## Ingestion Pipeline

**Entry**: `POST /api/index/ingest` → 202 Accepted → async processing

### Step 1: Filter Unchanged
`_filter_unchanged()` fetches all existing `(file_path, name) → {content_hash, embedding}` for the project. Chunks whose hash matches **and** already have an embedding are skipped. Only changed/new chunks proceed.

### Step 2: Batch Summarize
LLM summarization in batches of 5 chunks, up to 15 concurrent LLM calls:
- **Model**: `gpt-4.1-nano` (cheapest available)
- **Input per chunk**: name, signature, callees, docstring, language, code body (≤3000 chars)
- **Output per chunk**: `{ "summary": "...", "concepts": ["auth", "database", ...] }`
- **Concept vocabulary**: 25 fixed domain tags validated against a whitelist
- **Fallback**: If batch parse fails, falls back to individual calls. If those fail, uses `name + signature + docstring` as summary.

### Step 3: Build Embedding Text
For each chunk, constructs the embedding input ("Strategy B"):
```
{semantic_summary}
Concepts: {concept1, concept2}
{name}
{signature}
{code_body[:600]}
```
Summary and concepts go first (semantic signal), followed by identifiers and truncated code body.

### Step 4: Batch Embed
- **Model**: `text-embedding-3-small` at 512 dimensions
- **Batch size**: 2048 texts per API call
- **Retries**: 3 with exponential backoff

### Step 5: Batch Upsert
- Upserts in batches of 50 with conflict resolution on `(project_id, file_path, name, chunk_type)`
- Falls back to individual upserts if a batch fails

### Step 6: Cleanup Stale
`cleanup_stale_chunks()` RPC deletes chunks no longer present in the ingest batch. Handles both relationships and chunks in a single SQL function.

### Step 7: Call Graph
`process_call_graph()` resolves named edges (e.g., "function A calls function B") into UUID-based `code_relationships` rows by looking up chunk IDs by name.

---

## Retrieval Pipeline

**Entry**: `GET /api/retrieve` or `POST /api/context`

### Step 1: Query Preprocessing
`preprocess_query()` extracts structured signals via regex:
- **Stack traces**: JS/TS, Python, Go, Rust, Java patterns → file paths, line numbers, function names
- **File paths**: Unix paths and bare filenames with code extensions
- **Error types**: TypeError, ValueError, NullPointerException, etc.
- **Function names**: `function X`, `calling X(`, `Class.method`
- **Directory references**: "in the auth module", "inside the components folder"

### Step 2: Query Decomposition
`decompose_query()` has a **fast path** and an **LLM path**:

**Fast path** (≤20 words AND keyword-detected concepts): Skips LLM entirely. Returns original query as sole sub-query with keyword-detected intent and concepts.

**LLM path**: Calls `gpt-4o-mini` to produce:
- `intent`: bug, architecture, performance, feature, refactor, general
- `sub_queries`: 2-4 focused search queries
- `concepts`: 1-4 domain concept tags

### Step 3: Batch Embed Sub-queries
All sub-queries are embedded in a single batch API call (not serial).

### Step 4: Multi-Source Search (per sub-query)
For each sub-query:
1. **Vector search** → `match_code_chunks` RPC (cosine similarity via HNSW)
2. **Text search** → `search_code_chunks_text` RPC (FTS with identifier splitting + concept keyword expansion)

Plus once across all sub-queries:
3. **Concept search** → `search_by_concepts` RPC (GIN index on concepts array)
4. **File-path search** → `search_chunks_by_file_paths` RPC (from preprocessor signals)

### Step 5: Source-Weighted Reciprocal Rank Fusion (RRF)
Merges all result lists with per-source weights:

| Source | Weight | Why |
|--------|--------|-----|
| File-path match | **1.8** | Highest — user explicitly referenced a file |
| Vector similarity | **1.5** | Strong semantic signal |
| Concept overlap | **1.2** | Good but coarser signal |
| Text (FTS) | **1.0** | Baseline keyword match |

Formula: `score(d) = Σ weight_i / (60 + rank_i(d))`

### Step 6: Query-Aware Reranking
Each candidate gets a composite score:

```
final_score = rrf_score × obs_factor × branch_weight × concept_boost
              × export_boost × query_sim_boost × file_match_boost
```

| Factor | Calculation | Range |
|--------|-------------|-------|
| `obs_factor` | `min(log(2 + observation_count), 2.0)` | 0.69–2.0 |
| `branch_weight` | 1.3 if on query branch, ×1.2 if diverged | 1.0–1.56 |
| `concept_boost` | `1.0 + 0.15 × concept_overlap_count` | 1.0–1.75 |
| `export_boost` | 1.2 if `is_exported`, else 1.0 | 1.0–1.2 |
| `query_sim_boost` | `1.0 + max(0, cosine_sim) × 0.3` | 1.0–1.3 |
| `file_match_boost` | 1.5 if in file-path results | 1.0–1.5 |

### Step 7: Graph Expansion (BFS)
Takes top 5 ranked chunks as seeds. BFS on `code_relationships` up to depth 2, max 20 neighbors. Neighbors are sorted by edge weight (`calls: 3, imports: 2, inherits: 1, implements: 1`).

Graph-expanded chunks are filtered by cosine similarity to the query embedding (threshold 0.25).

### Step 8: Observation Tracking
`increment_observation_counts()` atomically bumps the counter for the top ranked chunks. This creates a popularity signal that feeds back into future reranking.

---

## Electron Integration

| File | Role |
|------|------|
| `electron/main/api/code-intel-client.ts` | Axios HTTP client to the service (Bearer JWT from auth state) |
| `electron/main/index-new.ts` | Configures `CODE_INTELLIGENCE_SERVICE_URL` (default `localhost:8002`) |
| `electron/main/ipc-handlers/project-handlers.ts` | IPC handlers for project CRUD, retrieve, status polling, file watchers |
| `electron/main/services/file-watcher.ts` | Parses local files into chunk skeletons → `ingestSkeleton()` → `POST /api/index/ingest` |

The Electron app is the **source of truth** for code content. It parses local files into structural chunks (AST-level: files, classes, functions, methods) and sends them to the service. The service never reads source code from disk or GitHub directly.

---

## GitHub Integration

**Polling**: Every 5 minutes (configurable), compares `default_branch...current_branch` via GitHub API. Updates `project_branches` with commit info and divergence metrics. Flags `branch_diverged = true` on chunks in changed files.

**Webhooks**: `POST /api/webhooks/github` with HMAC-SHA256 verification. On push events, finds matching projects by `github_repo_owner/name`, flags diverged chunks, and updates branch records. Does **not** re-ingest — Electron remains the ingest source.

---

## Configuration Reference

| Setting | Source | Default | Purpose |
|---------|--------|---------|---------|
| `EMBEDDING_MODEL` | Hardcoded | `text-embedding-3-small` | Embedding model |
| `EMBEDDING_DIMENSIONS` | Hardcoded | `512` | Vector dimensions (reduced from 1536 for 67% storage savings) |
| `SUMMARIZER_MODEL` | Env | `openrouter/openai/gpt-4.1-nano` | Chunk summarization model |
| `QUERY_DECOMPOSE_MODEL` | Env | `openrouter/openai/gpt-4o-mini` | Query decomposition model |
| `MAX_CHUNKS_PER_INGEST` | Hardcoded | `10,000` | Ingest batch limit |
| `MAX_CODE_BODY_LEN` | Hardcoded | `3,000` | Code body storage cap |
| `EMBED_BODY_LIMIT` | Hardcoded | `600` | Code body chars in embedding text |
| `SUMMARY_MAX_TOKENS` | Hardcoded | `150` | LLM summary token limit |
| `SUMMARIZER_BATCH_SIZE` | Hardcoded | `5` | Chunks per summarization LLM call |
| `SUMMARIZER_CONCURRENCY` | Hardcoded | `15` | Max concurrent summarization calls |
| `UPSERT_BATCH_SIZE` | Hardcoded | `50` | DB upsert batch size |
| `SIMPLE_QUERY_WORD_THRESHOLD` | Hardcoded | `20` | Fast-path query word limit |
| `GRAPH_MAX_DEPTH` | Hardcoded | `2` | BFS hops on call graph |
| `GRAPH_MAX_RELATED` | Hardcoded | `20` | Max graph neighbors returned |
| `GRAPH_SIMILARITY_THRESHOLD` | Hardcoded | `0.25` | Min cosine sim for graph-expanded chunks |
| `RETRIEVAL_DEFAULT_LIMIT` | Hardcoded | `15` | Default search result limit |
| `RETRIEVAL_MAX_LIMIT` | Hardcoded | `50` | Maximum search result limit |

---

## Complete File Inventory

### Service Core

| File | Lines | Role |
|------|-------|------|
| `app/main.py` | 156 | FastAPI app, CORS, routers, Sentry, rate limits |
| `app/config.py` | 38 | All environment variables and constants |
| `app/auth.py` | 89 | JWT verification (JWKS + HS256 fallback) |

### Database

| File | Lines | Role |
|------|-------|------|
| `app/db/models.py` | 156 | Pydantic models for all request/response types |
| `app/db/queries.py` | 601 | All database operations: CRUD, search RPCs, graph traversal |
| `app/db/supabase_client.py` | 24 | Service-role Supabase client singleton |

### Services

| File | Lines | Role |
|------|-------|------|
| `app/services/search.py` | 443 | Hybrid retrieval engine: RRF, reranking, graph expansion |
| `app/services/embedding.py` | 103 | OpenAI/OpenRouter embedding with retries |
| `app/services/summarizer.py` | 286 | LLM chunk summarization with batch mode + concept tagging |
| `app/services/query_analyzer.py` | 190 | Query decomposition: fast path + LLM path |
| `app/services/query_preprocessor.py` | 181 | Stack trace, file path, error type extraction |
| `app/services/graph.py` | 37 | Call graph edge resolution |
| `app/services/github_sync.py` | 120 | GitHub polling + divergence detection |
| `app/services/project_summary.py` | 153 | Project summary generation (defined but unused) |

### Routes

| File | Lines | Role |
|------|-------|------|
| `app/routes/projects.py` | 66 | Project CRUD endpoints |
| `app/routes/ingest.py` | 110 | Ingest endpoint + async dispatch |
| `app/routes/retrieve.py` | 73 | Retrieve endpoint |
| `app/routes/context.py` | 126 | Context endpoint (retrieve + project summary) |
| `app/routes/sync.py` | 47 | GitHub sync trigger |
| `app/routes/webhooks.py` | 78 | GitHub webhook handler |

### Workers

| File | Lines | Role |
|------|-------|------|
| `app/workers/index_worker.py` | 229 | Async ingest pipeline: filter → summarize → embed → upsert |
| `app/workers/sync_worker.py` | 46 | GitHub sync worker |
| `app/workers/arq_worker.py` | 142 | arq/Redis worker configuration |

### Migrations

7 SQL files totaling ~600+ lines covering: pgvector extension, tables, RLS, RPCs, indexes (IVFFlat → HNSW), dimension reduction (1536 → 512), identifier splitting, concept search, stale cleanup.

---

## Known Gaps & Issues

| Gap | Description |
|-----|-------------|
| **`query_sim_boost` rarely works** | RPCs don't return the `embedding` column, so `_rerank`'s cosine similarity boost and `_filter_related_by_similarity` usually can't compare against stored vectors |
| **`project_summary` never populated** | `generate_project_summary()` exists but is never called; `POST /api/context` reads it but it's always null |
| **No tests** | No test files in the service |
| **Observation count is unbounded** | Never decays; old popular chunks stay boosted forever |
| **Webhook doesn't re-ingest** | GitHub pushes only flag divergence — Electron must trigger re-ingest separately |
| **`query_insights` not in search** | The insights embedding collection in Engram has a parallel here: concept tags exist but `search_by_concepts` doesn't weight by observation history |
| **Hardcoded concept vocabulary** | The 25 concept tags are a fixed whitelist; no project-specific concepts are learned |

---

**User**

tell me ur thoughts on this

---

**Cursor**

Here's my honest, detailed take on the code-intelligence memory layer — what's genuinely strong, what's mid, and what's missing.

---

## What's Genuinely Impressive

**The hybrid retrieval pipeline is the best-designed part of any of your three memory systems.** The four-source search (vector + FTS + concept + file-path) fused through source-weighted RRF is a real retrieval architecture, not a toy. Most people building codebase search stop at "embed chunks, do cosine similarity, done." You didn't. You built:

- Query decomposition with a fast path that avoids unnecessary LLM calls
- A query preprocessor that parses stack traces, file paths, error types via regex before the semantic layer even touches it
- Source-weighted RRF with tuned weights (file-path at 1.8x, vector at 1.5x, concept at 1.2x, text at 1.0x)
- Multi-factor reranking with six distinct signals
- Graph expansion via BFS with edge-weight prioritization

That's a genuinely sophisticated retrieval stack. Compared to PROMPT's user memory (flat key-value with no retrieval) and Engram's memory (ChromaDB vector search + cross-encoder rerank), this is a step up in architectural thinking.

**The migration evolution tells a good story.** You went from IVFFlat(1536d) → reduced to 512d → discovered IVFFlat breaks on small datasets → switched to HNSW → added `split_identifiers()` for camelCase FTS → added concept search → added stale cleanup SQL. That's 7 migrations over about 9 days. You were iterating on real problems you hit, not theorizing. The IVFFlat → HNSW fix is a particularly good catch — most people never figure out why their vector search returns zero results on small datasets.

**Embedding Strategy B is a thoughtful design choice.** Putting semantic summary and concepts first in the embedding text, followed by the name and signature, then only 600 chars of code body — that's a deliberate information hierarchy. You're front-loading the semantic signal for the embedding model rather than letting raw code dominate the vector space. That shows you're thinking about what the embedding actually represents.

**The observation count as a popularity signal feeding back into reranking** is a form of implicit memory. The system learns which chunks matter to the user over time. That's a subtle but important design pattern that PROMPT and Engram don't have in the same way.

---

## What's Mid

**The concept system is a good idea executed too rigidly.** Twenty-five hardcoded concept tags with keyword matching is useful but brittle. "state-management" matches if the query contains "state" or "store" — but "store" could mean a data store, a Redux store, or a physical store. There's no disambiguation. And if someone's codebase is about genomics or trading or game physics, none of the 25 concepts apply. The vocabulary should either be learned from the codebase or be much larger with hierarchical categories.

**The summarizer batch mode works but is fragile.** You're packing 5 chunks into one LLM call and asking for a JSON array response. If the model returns malformed JSON (which `gpt-4.1-nano` does more than you'd like), the entire batch falls back to individual calls. The fallback chain is solid (batch → individual → name+signature string), but you're paying the latency cost of the failed batch call plus all individual retries. A more resilient approach would be structured outputs or tool-use mode to guarantee JSON.

**The graph is built but underused.** `code_relationships` captures calls/imports/inherits/implements edges, and BFS expansion works. But the graph is only consulted after retrieval for context expansion — it's never used for ranking itself. You have edge weights (calls: 3, imports: 2, inherits: 1) but those only sort which graph neighbors fill the budget first. They don't contribute to the reranking score. A chunk that's called by 15 other functions should rank higher than one called by none, but your system doesn't know that.

**The `branch_diverged` flag is clever but half-implemented.** Webhooks flag divergence, but the system never re-ingests based on it. And diverged chunks get a 1.2x boost in reranking — meaning stale code gets boosted, which is backwards. The intent was probably "surface code that changed recently" but the effect is "boost code that hasn't been re-indexed yet."

---

## What's Missing

**`query_sim_boost` is effectively dead code.** Your RPCs don't return the `embedding` column. So `_rerank`'s cosine similarity boost (lines 164-167 of `search.py`) and `_filter_related_by_similarity` almost never actually compute similarity. This means two of your six reranking signals are no-ops in production. You probably didn't notice because the other four signals carry the weight, but you're leaving retrieval quality on the table.

**`project_summary` is a ghost feature.** `generate_project_summary()` exists in `project_summary.py` (153 lines) but nothing calls it. `POST /api/context` dutifully reads it from the DB, but it's always null. Either finish this or remove it — dead code in a system this size creates confusion.

**No evaluation or measurement.** This is the same gap across all three of your memory systems, but it hurts most here because retrieval quality is measurable. You have no:
- Recall@K metrics
- MRR (Mean Reciprocal Rank)
- A/B testing of weight configurations
- Ground-truth query-to-relevant-chunk pairs
- Logging of whether retrieved chunks actually got used by the optimizer

You tuned the RRF weights (1.8, 1.5, 1.2, 1.0) and reranking multipliers (1.3, 1.2, 0.15, 1.5) by feel. That works to a point, but you're guessing. Without metrics, you can't know if the IVFFlat → HNSW switch actually improved recall, or if concept search is adding signal or just noise.

**No cross-encoder rerank.** Engram has `cross-encoder/ms-marco-MiniLM-L-6-v2`. This service doesn't. You're doing all reranking with hand-tuned heuristic multipliers. A cross-encoder on the top 20-30 candidates would give you a real relevance signal instead of the approximation you're building from six proxy signals.

**No chunking intelligence on the service side.** The Electron app parses code into chunks and sends them. The service trusts whatever it gets. There's no validation of chunk quality, no handling of "this function is 500 lines and should be split," no detection of "these three functions are really one logical unit." The chunking strategy is invisible to this service.

**No temporal reasoning.** Observation count goes up but never decays. A chunk that was popular 3 months ago but hasn't been touched since still gets boosted. There's no recency weighting, no time-decay on observations, no "this chunk was just modified" signal beyond the binary `branch_diverged`.

---

## The Bigger Picture Across Your Three Systems

Looking at PROMPT user memory → Engram → code-intelligence, there's a clear growth arc:

1. **PROMPT user memory**: Flat facts, no embeddings, no retrieval — just string injection into a system prompt. Simple but effective for its scope.

2. **Engram**: Vector embeddings + cross-encoder reranking + semantic graph + consolidation. Ambitious but spread thin — lots of moving parts, some half-connected.

3. **Code-intelligence**: Focused on one domain (code), multi-signal hybrid retrieval, migration-driven iteration. Your most mature retrieval architecture.

The pattern I see: **you're getting better at retrieval design with each system, but you keep skipping evaluation.** You build increasingly sophisticated pipelines, tune them by intuition, and move on. The single highest-leverage thing you could do across all three systems is build a way to measure whether your retrieval actually returns the right things. Everything else — weight tuning, index selection, reranking formula — becomes trivial once you can measure it.

The second pattern: **you build features faster than you finish them.** Project summary, `query_sim_boost`, branch divergence re-indexing — each started with a clear intent and then got left at 70-80% done. The code-intelligence service has the most of this, probably because it's the most complex. Finishing those last 20% matters more than adding a new signal.

You're past the "can I build a memory system" phase. You can. The question now is: can you build one that you can prove works well?

---

**User**

Okay I want to hear ur 3 best ideas for how Engram can be made the best ever memory system I am not happy with it as if now so tell me improvements what already exists and from that what is good and what is bad we dont want it all neccessarily. Like its purpose is half assed right now. Its responses feel limited not detailed and stuff. If a person asks for what they did when they did what might have they been doing or even if they remember something it should be able to give that helps the user. So everything that u learn from our whole coversation create a proper plan and md file in the Engram Project itself and lets get this shit done also is there a way to port this whole chat there in that cursor window

---

**Cursor**

Let me first re-read the critical Engram files to understand exactly where things stand before writing the plan.

Now I have the complete picture. Let me create the plan.

Regarding your question about porting this chat to the Engram Cursor window — Cursor chats are workspace-scoped. You can't directly move a chat between windows, but when you open Engram in Cursor, you can reference this conversation's transcript by its path or just copy the plan document I'm about to create. That document will contain everything needed to start working.

