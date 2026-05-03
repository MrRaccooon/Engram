"""
Intelligence / Ask routes.

POST /api/ask/preview  — run the privacy pipeline and return the exact
                         masked prompt that *would* be sent to the API.
                         No external call is made. Used by the frontend
                         confirmation modal.

POST /api/ask          — run the full pipeline and call the frontier API.
                         Returns the synthesized answer with real entity
                         names restored.
"""

from __future__ import annotations

import math
import time
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from loguru import logger

from pipeline import embedder, reranker
from pipeline import intelligence
from pipeline.context_parser import parse_window
from pipeline.query_engine import parse_query, ParsedQuery
from storage import vector_db, metadata_db, graph_db

router = APIRouter(tags=["ask"])


# ── Pydantic models ───────────────────────────────────────────────────────────

class AskFilters(BaseModel):
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    source_types: list[str] = Field(default_factory=list)


class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    filters: AskFilters = Field(default_factory=AskFilters)
    top_k: int = Field(default=10, ge=1, le=30)
    deep: bool = Field(default=False, description="Use the more capable (slower) model")


class PreviewResponse(BaseModel):
    masked_prompt: str
    entity_map: dict[str, str]
    blocked_count: int
    passing_count: int
    estimated_tokens: int
    system_prompt: str


class AskResponse(BaseModel):
    answer: str
    blocked_count: int
    passing_count: int
    model_used: str
    provider: str
    query_time_ms: int
    query_id: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _apply_recency(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Boost recent captures and penalise browser URL history.
    Screenshots and file captures are much stronger signals for 'what am I
    doing right now' than a browser history entry from 3 weeks ago.
    """
    now = datetime.now(timezone.utc)
    for c in candidates:
        try:
            ts_str = c.get("timestamp", "")
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            hours_ago = max((now - ts).total_seconds() / 3600, 0)
            decay = math.exp(-hours_ago / 168)  # half-weight at ~1 week
            base = c.get("rerank_score", c.get("score", 0))
            score = base * (0.7 + 0.3 * decay)

            # URL/browser-history entries get a 40% penalty — they're
            # historical context, not live session signal
            if c.get("source_type") == "url":
                score *= 0.6

            c["rerank_score"] = score
        except Exception:
            pass
    candidates.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
    return candidates


def _remove_self_refs(candidates: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Remove clipboard entries whose content IS the query (self-reference loop)."""
    q_norm = query.lower().strip()[:100]
    return [
        c for c in candidates
        if not (
            c.get("source_type") == "clipboard"
            and (c.get("content_preview") or "").lower().strip()[:100] == q_norm
        )
    ]


def _build_chroma_where(filters: AskFilters, pq: ParsedQuery | None = None) -> Optional[dict]:
    conditions = []
    if filters.source_types:
        conditions.append({"source_type": {"$in": filters.source_types}})

    if len(conditions) == 1:
        return conditions[0]
    elif len(conditions) > 1:
        return {"$and": conditions}
    return None


def _date_bounds(filters: AskFilters, pq: ParsedQuery | None = None) -> tuple[str | None, str | None]:
    date_from = filters.date_from or (pq.date_from if pq else None)
    date_to = filters.date_to or (pq.date_to if pq else None)
    return date_from, date_to


def _filter_candidates_by_date(
    candidates: list[dict[str, Any]],
    date_from: str | None,
    date_to: str | None,
) -> list[dict[str, Any]]:
    """Apply temporal filters outside Chroma because Chroma rejects string range operands."""
    if not date_from and not date_to:
        return candidates

    start = f"{date_from}T00:00:00" if date_from and "T" not in date_from else date_from
    end = f"{date_to}T23:59:59" if date_to and "T" not in date_to else date_to

    filtered: list[dict[str, Any]] = []
    for item in candidates:
        ts = item.get("timestamp") or ""
        if not ts:
            continue
        if start and ts < start:
            continue
        if end and ts > end:
            continue
        filtered.append(item)
    return filtered


def _enrich_with_full_content(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace 300-char content_preview with full content from SQLite."""
    for c in candidates:
        capture_id = c.get("capture_id", "")
        if not capture_id:
            continue
        row = metadata_db.fetch_capture_by_id(capture_id)
        if row and row["content"]:
            c["content"] = row["content"]
            if not c.get("window_title") and row["window_title"]:
                c["window_title"] = row["window_title"]
            if not c.get("url") and row["url"]:
                c["url"] = row["url"]
    return candidates


def _retrieve_insights(query_vec: list[float], top_k: int = 5) -> list[dict[str, Any]]:
    try:
        return vector_db.query_insights(query_vec, top_k=top_k)
    except Exception as exc:
        logger.debug(f"Insights retrieval failed: {exc}")
        return []


# ── Source-weighted Reciprocal Rank Fusion ─────────────────────────────────────

_RRF_K = 60  # standard RRF constant

_SOURCE_WEIGHTS = {
    "text":     1.0,
    "visual":   0.8,
    "insights": 1.5,
    "temporal": 2.0,
    "tags":     1.3,
    "graph":    0.7,
    "concepts": 1.2,
    "events":   1.1,
}


def _rrf_fuse(
    ranked_lists: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """
    Fuse multiple ranked lists into one using source-weighted RRF.
    All lists are keyed by capture_id for dedup.
    """
    scores: dict[str, float] = {}
    items: dict[str, dict[str, Any]] = {}

    for source, results in ranked_lists.items():
        weight = _SOURCE_WEIGHTS.get(source, 1.0)
        for rank, item in enumerate(results):
            cid = item.get("capture_id") or item.get("id", "")
            if not cid:
                continue
            scores[cid] = scores.get(cid, 0) + weight / (_RRF_K + rank)
            if cid not in items:
                items[cid] = item

    sorted_ids = sorted(scores, key=lambda k: scores[k], reverse=True)
    fused = []
    for cid in sorted_ids:
        entry = items[cid]
        entry["rrf_score"] = scores[cid]
        fused.append(entry)
    return fused


def _dedupe_chunks_to_captures(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse multiple chunks from the same capture to the best-scoring one."""
    seen: dict[str, dict[str, Any]] = {}
    for r in results:
        cid = r.get("capture_id", r.get("id", ""))
        if cid not in seen:
            seen[cid] = r
    return list(seen.values())


def _retrieve_candidates(
    query: str,
    top_k: int,
    filters: AskFilters,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """
    Multi-source retrieval with RRF fusion.
    Returns (candidates, insights, sources_used).
    """
    known_tags = []
    try:
        known_tags = metadata_db.fetch_distinct_tags(limit=300)
    except Exception:
        pass

    pq = parse_query(query, known_tags=known_tags)
    retrieval_top_k = min(top_k * 5, 50)
    where = _build_chroma_where(filters, pq)
    date_from, date_to = _date_bounds(filters, pq)

    ranked_lists: dict[str, list[dict[str, Any]]] = {}

    # 1) Text vector search (always)
    text_vec = embedder.embed_text(query)
    text_results = vector_db.query_text(text_vec, top_k=retrieval_top_k, where=where)
    text_deduped = _dedupe_chunks_to_captures(text_results)
    ranked_lists["text"] = text_deduped

    # 2) Visual vector search (always)
    visual_vec = embedder.embed_query_text_clip(query)
    if visual_vec:
        visual_results = vector_db.query_visual(
            visual_vec, top_k=retrieval_top_k // 2, where=where,
        )
        if visual_results:
            ranked_lists["visual"] = visual_results

    # 3) Insights vector search (always)
    insights = _retrieve_insights(text_vec, top_k=5)

    # 4) Temporal DB query (when temporal signals detected)
    if pq.has_temporal and pq.date_from:
        try:
            date_to = pq.date_to or pq.date_from
            temporal_rows = metadata_db.fetch_captures_in_range(
                pq.date_from, date_to, limit=50,
            )
            temporal_results = []
            for row in temporal_rows:
                d = dict(row)
                d["capture_id"] = d["id"]
                temporal_results.append(d)
            if temporal_results:
                ranked_lists["temporal"] = temporal_results
        except Exception as exc:
            logger.debug(f"Temporal retrieval failed: {exc}")

    # 5) Tag search (when entity names detected)
    if pq.entity_filters:
        try:
            tag_results: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            for tag in pq.entity_filters[:3]:
                rows = metadata_db.fetch_captures_by_tag(tag, limit=15)
                for row in rows:
                    d = dict(row)
                    d["capture_id"] = d["id"]
                    if d["id"] not in seen_ids:
                        seen_ids.add(d["id"])
                        tag_results.append(d)
            if tag_results:
                ranked_lists["tags"] = tag_results
        except Exception as exc:
            logger.debug(f"Tag retrieval failed: {exc}")

    # 6) Concept vocabulary search
    try:
        from pipeline.concept_vocabulary import match_query_to_concepts
        query_concepts = match_query_to_concepts(query, top_k=5, threshold=0.15)
        if query_concepts:
            concept_ids = [cid for cid, _, _ in query_concepts]
            concept_rows = metadata_db.fetch_captures_by_concepts(concept_ids, limit=20)
            concept_results: list[dict[str, Any]] = []
            for row in concept_rows:
                d = dict(row)
                d["capture_id"] = d["id"]
                concept_results.append(d)
            if concept_results:
                ranked_lists["concepts"] = concept_results
    except Exception as exc:
        logger.debug(f"Concept retrieval failed: {exc}")

    # 7) Event-based retrieval (action events from differential analysis)
    if pq.intent in ("activity", "recall", "locate", "temporal"):
        try:
            event_rows = metadata_db.search_events(
                query_text=query,
                time_start=pq.date_from,
                time_end=pq.date_to,
                app_name=pq.app_filters[0] if pq.app_filters else None,
                limit=20,
            )
            if event_rows:
                event_results: list[dict[str, Any]] = []
                for row in event_rows:
                    d = dict(row)
                    d["capture_id"] = d.get("capture_id", d.get("id", ""))
                    d["content_preview"] = (
                        f"[{d.get('change_type', '')}] "
                        f"{d.get('app_name', '')} — {d.get('window_title', '')}: "
                        f"{(d.get('changed_text', '') or '')[:200]}"
                    )
                    d["source_type"] = "event"
                    event_results.append(d)
                ranked_lists["events"] = event_results
        except Exception as exc:
            logger.debug(f"Event retrieval failed: {exc}")

    sources_used = list(ranked_lists.keys())
    if insights:
        sources_used.append("insights")

    # Fuse all sources
    fused = _rrf_fuse(ranked_lists)
    fused = _filter_candidates_by_date(fused, date_from, date_to)

    # Enrich with full content BEFORE reranking so the cross-encoder
    # scores on real text, not 300-char previews
    rerank_pool = fused[:top_k * 3]
    rerank_pool = _enrich_with_full_content(rerank_pool)

    # Ensure every candidate has a "content" field for the reranker;
    # fall back to content_preview for items that SQLite enrichment missed
    for item in rerank_pool:
        if not item.get("content"):
            item["content"] = item.get("content_preview") or ""

    reranked = reranker.rerank(
        query=query,
        candidates=rerank_pool,
        top_n=top_k,
        text_field="content",
    )

    reranked = _remove_self_refs(reranked, query)
    reranked = _apply_recency(reranked)

    # 8) Graph walk on top 3 results for context expansion
    if len(reranked) >= 2:
        try:
            graph_additions: list[dict[str, Any]] = []
            existing_ids = {c.get("capture_id", c.get("id", "")) for c in reranked}
            for seed in reranked[:3]:
                cid = seed.get("capture_id", seed.get("id", ""))
                if not cid:
                    continue
                related = graph_db.get_related(cid, limit=3)
                for r in related:
                    rid = r.get("id", "")
                    if rid and rid not in existing_ids:
                        r["capture_id"] = rid
                        r["_source"] = "graph"
                        graph_additions.append(r)
                        existing_ids.add(rid)
            if graph_additions:
                graph_additions = _enrich_with_full_content(graph_additions)
                reranked.extend(graph_additions[:3])
                sources_used.append("graph")
        except Exception as exc:
            logger.debug(f"Graph expansion failed: {exc}")

    return reranked, insights, sources_used


# ── Session context builder ───────────────────────────────────────────────────

def _build_session_context() -> str:
    """
    Assemble a short natural-language description of what the user has been
    doing recently. Injected into the Ask system prompt so the AI knows the
    user's current focus without needing to retrieve it from the vector store.
    """
    try:
        lines: list[str] = []

        # Last 60 minutes of activity
        recent = metadata_db.fetch_recent_captures(minutes=60, limit=30)
        if recent:
            # Parse each window title to extract project / file signals
            projects: dict[str, int] = {}
            files: dict[str, int] = {}
            activities: list[str] = []

            for row in recent:
                wt = row["window_title"] or ""
                app = row["app_name"] or ""
                ctx = parse_window(wt, app)
                if ctx.get("project"):
                    projects[ctx["project"]] = projects.get(ctx["project"], 0) + 1
                if ctx.get("file"):
                    files[ctx["file"]] = files.get(ctx["file"], 0) + 1
                if ctx.get("rich_text"):
                    activities.append(ctx["rich_text"])

            # Top project and file
            if projects:
                top_proj = max(projects, key=lambda k: projects[k])
                lines.append(f"Current project: {top_proj}")
            if files:
                top_files = sorted(files, key=lambda k: files[k], reverse=True)[:3]
                lines.append(f"Recently edited: {', '.join(top_files)}")

            # Deduplicated activity sample (last 5 unique)
            seen: set[str] = set()
            unique_acts: list[str] = []
            for act in activities:
                if act not in seen:
                    seen.add(act)
                    unique_acts.append(act)
            if unique_acts:
                lines.append("Recent activity: " + " → ".join(unique_acts[:5]))

        # Top apps over last 6 hours
        top_apps = metadata_db.fetch_top_apps(hours=6, limit=3)
        if top_apps:
            app_names = [r["app_name"].replace(".exe", "") for r in top_apps if r["app_name"]]
            if app_names:
                lines.append(f"Primary tools today: {', '.join(app_names)}")

        # Recent consolidated insights (last 3 days)
        insights = metadata_db.fetch_recent_insights(days=3)
        if insights:
            summaries = [r["summary"] for r in insights[:3] if r["summary"]]
            if summaries:
                lines.append("Recent session summaries:")
                for s in summaries:
                    lines.append(f"  • {s[:200]}")

        if not lines:
            return ""

        return "\n".join(lines)

    except Exception as exc:
        logger.debug(f"Session context build failed: {exc}")
        return ""


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/ask/preview", response_model=PreviewResponse)
async def ask_preview(req: AskRequest) -> PreviewResponse:
    """
    Build the masked prompt that would be sent to the API.
    No external API call is made. Used by the frontend confirmation modal.
    """
    candidates, insights, _sources = _retrieve_candidates(req.query, req.top_k, req.filters)
    logger.info(f"Ask preview q={req.query!r} → {len(candidates)} candidates, {len(insights)} insights")

    session_ctx = _build_session_context()

    try:
        preview = intelligence.build_preview(
            req.query, candidates, session_context=session_ctx, insights=insights,
        )
    except Exception as exc:
        logger.error(f"Ask preview failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    logger.info(f"Ask preview done: passing={preview['passing_count']} blocked={preview['blocked_count']} tokens≈{preview['estimated_tokens']}")
    return PreviewResponse(**preview)


@router.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest) -> AskResponse:
    """
    Run the full privacy pipeline and call the configured frontier API.
    Returns the synthesized answer with real entity names restored.
    """
    t0 = time.perf_counter()

    candidates, insights, sources_used = _retrieve_candidates(req.query, req.top_k, req.filters)
    session_ctx = _build_session_context()
    logger.info(
        f"Ask q={req.query!r} deep={req.deep} → {len(candidates)} candidates, "
        f"{len(insights)} insights | sources={sources_used} | "
        f"session_ctx={'yes' if session_ctx else 'none'}"
    )

    try:
        result = intelligence.ask(
            req.query, candidates, deep=req.deep,
            session_context=session_ctx, insights=insights,
        )
    except Exception as exc:
        logger.error(f"Ask failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(f"Ask done: model={result['model_used']} provider={result['provider']} in {elapsed_ms}ms")

    query_id = str(_uuid.uuid4())
    try:
        parsed = parse_query(req.query)
        metadata_db.insert_eval_log(
            query_id=query_id,
            query=req.query,
            intent=parsed.intent,
            candidate_count=len(candidates),
            sources_used=",".join(sources_used),
            model_used=result.get("model_used", ""),
            latency_ms=elapsed_ms,
        )
    except Exception as exc:
        logger.debug(f"Eval log insert failed (non-fatal): {exc}")

    return AskResponse(
        answer=result["answer"],
        blocked_count=result["blocked_count"],
        passing_count=result["passing_count"],
        model_used=result["model_used"],
        provider=result["provider"],
        query_time_ms=elapsed_ms,
        query_id=query_id,
    )
