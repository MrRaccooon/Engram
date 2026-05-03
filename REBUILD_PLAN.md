# Engram Rebuild Plan — From Half-Assed to Best-in-Class

> **Context**: This plan was produced after deep-diving PROMPT's user memory,
> PROMPT's code-intelligence service, and Engram's entire codebase.
> It distills every lesson from all three systems into three surgical
> improvements that fix Engram's core problem: **answers feel limited,
> not detailed, and don't actually help the user recall their life.**

---

## The Diagnosis — Why Engram Feels Broken

### What currently works well
- **Capture pipeline**: Screenshot OCR, clipboard, URL, file watchers — the ingestion is solid.
- **Dual embeddings**: MiniLM text + CLIP visual is a good architecture choice.
- **Cross-encoder reranking**: `ms-marco-MiniLM-L-6-v2` is the right tool for semantic reranking.
- **Privacy pipeline**: Sensitivity filtering + entity masking is genuinely thoughtful.
- **Session context builder**: `_build_session_context()` in `ask.py` already extracts current project, recent files, top apps, and recent insights. This is a strong foundation.
- **Semantic graph**: `capture_edges` + `capture_tags` tables exist and edges are built during ingestion.
- **Consolidation worker**: Nightly session summaries with Ollama/API fallback chain is a good design.

### What's actually broken (in order of severity)

#### 1. The Ask pipeline is a single-pass dumb pipe
- One text vector query + one visual query → rerank → mask → send to LLM → done.
- **No query understanding**. If someone asks "what was I working on last Tuesday", the system does semantic search for the string "working on last Tuesday" instead of parsing the date and querying captures for that day.
- **No insights retrieval**. The `insights_embeddings` ChromaDB collection exists and contains consolidated session summaries. The Ask pipeline **never queries it**. Those summaries are the single best source for answering "what did I do" questions.
- **No graph traversal**. The semantic graph is built during ingestion but only used by the `/related/{capture_id}` endpoint. Ask never walks the graph.
- **No tag search**. NER tags (person names, org names, project names) are extracted and stored in `capture_tags`. Ask never queries them.

#### 2. Context assembly produces a thin, noisy prompt
- `content_preview` is capped at **300 characters** in ChromaDB metadata. For OCR text from a screenshot, 300 chars is maybe 3 lines of code or one paragraph. The full content sits in SQLite's `captures.content` column but is never fetched during Ask.
- The prompt format is flat: numbered chunks with metadata. No temporal structure, no grouping by session, no distinction between "this is what you were doing" vs "this is related context".
- `max_tokens` for the API response is **1024**. Detailed answers are impossible in 1024 tokens.

#### 3. Consolidation produces trivially thin summaries
- Summaries are 2-3 sentences, max 150 tokens. "Session of 45 minutes primarily in Code.exe. Windows: main.py, terminal."
- Topics are just app names, not semantic topics.
- No structured extraction of: what was decided, what was researched, what was written, what problems were encountered, what was completed.
- Consolidation only runs daily. There's no real-time or hourly consolidation, so recent activity has no summary layer.
- No cross-day consolidation. Weekly patterns, project arcs, recurring themes — all invisible.

### What to cut
- **`suppress_incognito`**: Not implemented in the capture daemon anyway. Remove from config.
- **`encrypt_at_rest`**: Flagged in config but never implemented. Either build it or remove the option to stop false promises.
- **MCP server**: Config exists (`mcp.enabled: false`) but the implementation is minimal. Don't invest here until the core works.

---

## The Three Improvements

### Improvement 1: Multi-Layer Retrieval Engine

**The problem**: Ask does one vector search and hopes for the best. When someone asks "what was I doing last Wednesday afternoon", the system embeds that string and returns whatever 10 chunks have the highest cosine similarity. It might return a clipboard entry about "Wednesday meetings" from 3 months ago instead of the actual captures from last Wednesday.

**The fix**: Build a query understanding layer that decomposes the question, then runs multiple retrieval strategies and fuses results.

#### 1A. Query Understanding (new file: `pipeline/query_engine.py`)

Parse the user's question to extract:

| Signal | Example Query | Extracted |
|--------|--------------|-----------|
| **Temporal** | "what was I doing last Tuesday" | `date_from=2026-04-28, date_to=2026-04-28` |
| **Temporal relative** | "earlier today", "yesterday", "this morning" | Time window converted to ISO range |
| **App/tool** | "what was I coding in VS Code" | `app_filter=["Code.exe"]` |
| **Entity** | "anything about the Engram project" | `tag_search=["Engram"]` |
| **Intent** | "what did I do" vs "where is the file" vs "who sent me" | Controls response style |
| **Time-of-day** | "this morning", "last night" | Narrows time window |

Use a combination of:
1. **Regex/rule-based** for temporal expressions (like code-intelligence's `query_preprocessor.py` does for stack traces)
2. **Keyword detection** for app names (match against known `app_name` values from the DB)
3. **LLM decomposition** for complex queries (like code-intelligence's `query_analyzer.py`, but only for queries >15 words or those without clear temporal/entity signals)

#### 1B. Multi-Source Retrieval

For every Ask query, run these in parallel:

| Source | When | How | Weight |
|--------|------|-----|--------|
| **Text vectors** | Always | Embed query → `query_text()` | 1.0 |
| **Visual vectors** | Always | CLIP-embed query → `query_visual()` | 0.8 |
| **Insights vectors** | Always | Embed query → `query_insights()` | **1.5** (summaries are the richest signal for "what did I do" questions) |
| **Temporal DB** | When temporal signals detected | `fetch_captures_for_day()` or time-range query | **2.0** (if user asked about a specific time, those captures are ground truth) |
| **Tag search** | When entity names detected | `fetch_captures_by_tag()` | 1.3 |
| **Graph walk** | After initial results | For top 3 results, fetch `get_related()` | 0.7 (context, not primary) |

Fuse using **source-weighted RRF** (exactly like code-intelligence's `_reciprocal_rank_fusion`):

```python
score(capture) = Σ weight_source / (k + rank_in_source)
```

#### 1C. Cross-Encoder Rerank on Fused Results

After RRF fusion, rerank the top 30 candidates with the cross-encoder (already have this). Then apply recency boost (already have this).

**Key change**: The cross-encoder currently ranks on `content_preview` (300 chars). After fetching full content from SQLite (see Improvement 2), rerank on the **full content** instead.

#### Files to create/modify:
- **CREATE**: `pipeline/query_engine.py` — Query understanding + temporal parsing + multi-source retrieval + RRF fusion
- **MODIFY**: `api/routes/ask.py` — Replace `_retrieve_candidates()` with `query_engine.retrieve()`
- **MODIFY**: `storage/metadata_db.py` — Add `fetch_captures_in_range(start_iso, end_iso)` for arbitrary time windows
- **MODIFY**: `storage/vector_db.py` — Ensure `query_insights()` is exposed and called

---

### Improvement 2: Rich Context Assembly & Detailed Responses

**The problem**: Even when the right captures are retrieved, the prompt sent to the LLM is thin. 300-char previews with flat numbering produce answers like "You were working in VS Code on a Python file." instead of "At 2:15 PM you were debugging the embedding pipeline in Engram's worker.py — the OCR was failing on screenshots with dark backgrounds. By 3 PM you'd switched to the consolidation worker and were testing the Ollama summarization fallback."

**The fix**: Build full content, structure it temporally, and give the LLM enough room to answer properly.

#### 2A. Full Content Retrieval

After retrieval + reranking, fetch the **full `content` column** from SQLite for the top candidates (not just the 300-char `content_preview` from ChromaDB).

```python
for candidate in top_candidates:
    full_row = metadata_db.fetch_capture_by_id(candidate["capture_id"])
    candidate["full_content"] = full_row["content"]  # full OCR text, clipboard content, etc.
```

This is a trivial change but dramatically improves context quality.

#### 2B. Temporal Context Window

When the query is about a specific time, also fetch the **surrounding captures** (±15 minutes) to provide narrative context. This is what makes "what was I doing" questions answerable — not just the single matching capture, but the flow of activity around it.

```python
# For the top 3 ranked captures, fetch their temporal neighborhood
for top in ranked[:3]:
    neighbors = metadata_db.fetch_captures_in_window(top["timestamp"], window_minutes=15)
    # Add to context as "surrounding activity"
```

#### 2C. Structured Prompt Assembly

Replace the flat numbered format with a temporally structured, multi-section prompt:

```
## Session Summaries (from consolidated insights)
These are AI-generated summaries of your recent work sessions:
- [Apr 28, 2:00–4:30 PM] Worked on Engram's pipeline module, debugging OCR...
- [Apr 28, 10:00 AM–12:15 PM] Research session on vector indexing strategies...

## Relevant Captures (chronological)
### 2:15 PM — VS Code (worker.py)
[Full OCR content of the screenshot — maybe 800 chars of actual code visible]

### 2:18 PM — Terminal
[Terminal output showing test results]

### 2:22 PM — Clipboard
[Code snippet the user copied]

### 2:25 PM — Chrome (ChromaDB docs)
[URL and page title]

## Current Session Context
Current project: Engram
Recently edited: worker.py, embedder.py, config.yaml
Primary tools today: Code, Terminal, Chrome

## Question
{query}
```

This gives the LLM **temporal narrative structure** instead of a flat bag of chunks.

#### 2D. Raise Response Token Limit

Change `max_tokens` from 1024 to **2048** in both `_call_anthropic` and `_call_openai` / `_call_openrouter`. Detailed answers need room.

#### 2E. Include Insights in Retrieval Context

When insights are retrieved (from Improvement 1), include them as the **first section** of the prompt. Insights are pre-summarized session descriptions — exactly what the LLM needs to answer "what did I do" questions without having to reconstruct it from raw OCR noise.

#### Files to modify:
- **MODIFY**: `pipeline/intelligence.py` — New `_assemble_structured_prompt()` replacing `_assemble_prompt()`, raise max_tokens
- **MODIFY**: `api/routes/ask.py` — Pass insights and temporal neighbors to prompt assembly
- **MODIFY**: `storage/metadata_db.py` — Add `fetch_captures_in_range()` time-range query

---

### Improvement 3: Rich Structured Consolidation

**The problem**: Consolidation produces "Session of 45 minutes primarily in Code.exe." This is useless for recall. The whole point of consolidation is to create a **semantic memory layer** that lets the system answer detailed questions about past activity without re-reading every raw capture.

**The fix**: Make consolidation produce structured, detailed summaries that actually contain actionable information.

#### 3A. Structured Summary Schema

Instead of a single text blob, consolidate into a structured format:

```json
{
  "narrative": "Spent 2 hours debugging the Engram embedding pipeline. Started with OCR failures on dark-background screenshots, traced to RapidOCR's binarization threshold. Fixed by adjusting contrast preprocessing. Then moved to testing consolidation worker — Ollama summarization was timing out on sessions with >20 captures. Reduced batch size from 25 to 15.",
  "topics": ["engram", "embedding-pipeline", "ocr", "consolidation"],
  "projects": ["Engram"],
  "files_touched": ["pipeline/worker.py", "pipeline/embedder.py", "config/config.yaml"],
  "tools_used": ["VS Code", "Terminal", "Chrome"],
  "decisions": ["Adjust OCR contrast threshold", "Reduce consolidation batch size to 15"],
  "research": ["RapidOCR binarization docs", "ChromaDB collection recovery"],
  "problems": ["OCR fails on dark backgrounds", "Ollama timeout on large sessions"],
  "outcomes": ["OCR fix deployed", "Consolidation batch size reduced"],
  "time_distribution": {"VS Code": 65, "Chrome": 20, "Terminal": 15}
}
```

#### 3B. Better Consolidation Prompt

Replace the current 2-sentence prompt with one that extracts structured information:

```
You are summarizing a person's digital work session from their screen captures.

Produce a JSON object with these fields:
- "narrative": A detailed 4-6 sentence summary of what happened, in chronological order. Include specific file names, URLs, topics, and decisions visible in the captures.
- "topics": 2-5 semantic topic tags (not app names)
- "projects": Project names visible in window titles or file paths
- "files_touched": Files that appear in editor windows or terminal commands
- "decisions": Any decisions or conclusions visible in the activity
- "problems": Issues or errors the user appeared to encounter
- "outcomes": What was accomplished or completed

Be specific and factual. Use details from the captures, not generic descriptions.
```

Raise `max_tokens` for consolidation from 150 to **500**.

#### 3C. Real-Time Micro-Consolidation

Don't wait until 2 AM. Run a **lightweight consolidation every 2 hours** that:
1. Groups the last 2 hours of captures into sessions
2. Produces a quick summary using the structured format
3. Embeds and stores as an insight
4. At the nightly run, these micro-insights get merged into day-level summaries

This means when a user asks "what was I doing this morning", there's already a consolidated summary available — not just raw captures.

#### 3D. Multi-Day Rollup

Add a weekly consolidation that:
1. Reads all daily insights for the past 7 days
2. Produces a **weekly narrative**: themes, projects progressed, recurring problems, time allocation patterns
3. Stored as a higher-level insight in a new `weekly_insights` table or tagged in the existing insights table

This enables questions like "what have I been working on this week" or "how much time did I spend on Engram vs PROMPT".

#### 3E. Insight Schema Upgrade

Modify the `insights` table to store structured data:

```sql
ALTER TABLE insights ADD COLUMN topics_structured TEXT;  -- JSON array of semantic topics
ALTER TABLE insights ADD COLUMN projects TEXT;            -- JSON array of project names  
ALTER TABLE insights ADD COLUMN files_touched TEXT;       -- JSON array of file paths
ALTER TABLE insights ADD COLUMN decisions TEXT;           -- JSON array of decisions
ALTER TABLE insights ADD COLUMN problems TEXT;            -- JSON array of problems
ALTER TABLE insights ADD COLUMN outcomes TEXT;            -- JSON array of outcomes
ALTER TABLE insights ADD COLUMN consolidation_type TEXT DEFAULT 'daily';  -- daily | micro | weekly
```

#### Files to create/modify:
- **MODIFY**: `pipeline/consolidation_worker.py` — Structured prompts, micro-consolidation, weekly rollup
- **MODIFY**: `storage/metadata_db.py` — New insight columns, fetch helpers for micro/weekly insights
- **MODIFY**: `config/config.yaml` — Micro-consolidation interval setting

---

## Implementation Order

### Phase 1: Make Ask Actually Work (Improvements 2A, 2B, 2D, 1B partial)
**Goal**: Without changing retrieval strategy, make the existing pipeline produce better answers by using full content and raising token limits.

1. Fetch full `content` from SQLite after retrieval (2A) — trivial change
2. Raise API response `max_tokens` to 2048 (2D) — one-line change
3. Add insights retrieval to Ask pipeline (1B partial) — query `insights_embeddings` and include in context
4. Build structured prompt format (2C) — replace `_assemble_prompt()`

**Estimated effort**: 2-3 hours. **Impact**: Immediately noticeable improvement in answer quality.

### Phase 2: Multi-Layer Retrieval (Improvements 1A, 1B, 1C)
**Goal**: Ask understands *when* you're asking about and retrieves from all sources.

1. Build query understanding with temporal parsing (1A)
2. Add multi-source retrieval: insights, tags, temporal DB queries (1B)
3. Implement source-weighted RRF fusion (1B)
4. Rerank on full content instead of 300-char preview (1C)

**Estimated effort**: 4-6 hours. **Impact**: "What did I do last Tuesday" actually returns Tuesday's activity.

### Phase 3: Rich Consolidation (Improvements 3A-3E)
**Goal**: Build the semantic memory layer that makes long-term recall work.

1. Upgrade insight schema (3E)
2. Structured consolidation prompts (3A, 3B)
3. Micro-consolidation every 2 hours (3C)
4. Weekly rollup (3D)

**Estimated effort**: 4-5 hours. **Impact**: "What have I been working on this week" returns a detailed narrative.

---

## What NOT to Build

- **Don't add more embedding models**. MiniLM + CLIP is fine. The bottleneck is retrieval strategy and context quality, not embedding quality.
- **Don't build a chat memory / conversation history**. The system should answer each question independently from captured context, not try to maintain a conversation thread. (If multi-turn is needed later, it's a separate concern.)
- **Don't build MCP integration yet**. Get the core recall working first.
- **Don't add more capture sources**. Screenshots, clipboard, URLs, and files cover the input surface. The problem is what you DO with captures, not how many you have.
- **Don't build at-rest encryption**. It's a nice-to-have that doesn't affect recall quality.

---

## Success Criteria

After all three improvements, Engram should be able to answer these questions with detailed, accurate, temporally-grounded responses:

| Question | Expected Answer Quality |
|----------|----------------------|
| "What was I doing last Tuesday afternoon?" | Names specific apps, files, projects, and activities from that time window |
| "What did I research about vector databases?" | Surfaces URLs visited, pages read, notes taken — across multiple days if needed |
| "How much time did I spend on Engram this week?" | Uses weekly rollup + daily insights to give a breakdown |
| "What was that error I was debugging yesterday?" | Finds the terminal output, the code being edited, and the resolution |
| "Who did I email about the deployment?" | Uses NER tags to find captures mentioning specific people + email activity |
| "What files was I editing when I was working on the auth system?" | Combines tag search ("auth") + file path extraction from window titles |
| "Remind me what I decided about the consolidation batch size" | Finds the insight where the decision was recorded, not just the raw capture |

---

## Reference: Lessons from PROMPT's Code-Intelligence Service

The code-intelligence service solved similar problems for code retrieval. These patterns transfer directly:

| Code-Intel Pattern | Engram Equivalent |
|-------------------|-------------------|
| Query decomposition with fast path + LLM path | Query understanding in `query_engine.py` |
| Source-weighted RRF (vector 1.5, concept 1.2, text 1.0, file-path 1.8) | Multi-source RRF (insights 1.5, temporal 2.0, text 1.0, visual 0.8, tags 1.3, graph 0.7) |
| `observation_count` for popularity signal | Track which captures get surfaced repeatedly — boost them |
| `split_identifiers()` for camelCase FTS | Not needed (Engram text is natural language, not code) |
| `_build_embedding_text()` with semantic signal first | Already good — MiniLM embeds raw content which is fine for natural text |
| BFS graph expansion with edge weights | Graph walk on top results for contextual expansion |
| Batch summarization with concept tagging | Structured consolidation with topic/project/decision extraction |

---

*Generated from deep dives of PROMPT user memory, PROMPT code-intelligence, and Engram.*
*Conversation reference: [Memory Deep Dives](1a32e2cd-e0ae-4186-9cbc-38858fc7aa2a)*
