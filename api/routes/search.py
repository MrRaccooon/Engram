"""
Search routes.

POST /api/search          — semantic search with dual vector + reranker
GET  /api/search/timeline — all captures for a given calendar day
"""

from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from loguru import logger

from pipeline import embedder, reranker
from storage import metadata_db, vector_db

router = APIRouter(tags=["search"])


# ── Pydantic models ───────────────────────────────────────────────────────────

class SearchFilters(BaseModel):
    date_from: Optional[str] = Field(None, description="ISO date string YYYY-MM-DD")
    date_to: Optional[str] = Field(None, description="ISO date string YYYY-MM-DD")
    source_types: list[str] = Field(
        default_factory=list,
        description="Filter to specific source types: screenshot, clipboard, url, file, audio",
    )
    apps: list[str] = Field(default_factory=list, description="Filter by app_name")


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    top_k: int = Field(default=10, ge=1, le=50)


class CaptureResult(BaseModel):
    capture_id: str
    source_type: str
    timestamp: str
    content_preview: str
    thumb_path: Optional[str]
    window_title: str
    app_name: str
    url: str
    relevance_score: float
    chunk_index: int = 0


class SearchResponse(BaseModel):
    results: list[CaptureResult]
    query_time_ms: int
    total_candidates: int


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_chroma_where(filters: SearchFilters) -> Optional[dict]:
    """Translate SearchFilters into a ChromaDB $and/$or where clause."""
    conditions = []

    if filters.source_types:
        conditions.append({"source_type": {"$in": filters.source_types}})

    if filters.apps:
        conditions.append({"app_name": {"$in": filters.apps}})

    if filters.date_from:
        conditions.append({"timestamp": {"$gte": filters.date_from}})

    if filters.date_to:
        conditions.append({"timestamp": {"$lte": filters.date_to + "T23:59:59"}})

    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def _dedupe_by_capture(results: list[dict]) -> list[dict]:
    """
    When multiple chunks of the same capture appear in results,
    keep only the highest-scored chunk per capture_id.
    """
    seen: dict[str, dict] = {}
    for r in results:
        cid = r.get("capture_id", r.get("id", ""))
        if cid not in seen or r.get("rerank_score", r.get("score", 0)) > seen[cid].get("rerank_score", seen[cid].get("score", 0)):
            seen[cid] = r
    return list(seen.values())


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    t0 = time.perf_counter()

    where = _build_chroma_where(req.filters)

    # ── 1. Dual vector retrieval ──────────────────────────────────────────────
    retrieval_top_k = min(req.top_k * 5, 50)  # fetch more than needed for reranker

    text_vec = embedder.embed_text(req.query)
    text_results = vector_db.query_text(text_vec, top_k=retrieval_top_k, where=where)

    visual_vec = embedder.embed_query_text_clip(req.query)
    visual_results: list[dict[str, Any]] = []
    if visual_vec:
        visual_results = vector_db.query_visual(visual_vec, top_k=retrieval_top_k // 2, where=where)

    # Merge text + visual candidates, dedupe by doc_id
    all_candidates_map: dict[str, dict] = {}
    for r in text_results:
        all_candidates_map[r["id"]] = r
    for r in visual_results:
        if r["id"] not in all_candidates_map:
            # Visual-only result: add a content_preview placeholder
            r.setdefault("content_preview", "")
            r.setdefault("chunk_index", 0)
            all_candidates_map[r["id"]] = r

    all_candidates = list(all_candidates_map.values())
    total_candidates = len(all_candidates)

    if not all_candidates:
        return SearchResponse(results=[], query_time_ms=0, total_candidates=0)

    # ── 2. Rerank ─────────────────────────────────────────────────────────────
    reranked = reranker.rerank(
        query=req.query,
        candidates=all_candidates,
        top_n=req.top_k * 2,          # rerank more, then dedupe by capture
        text_field="content_preview",
    )

    # ── 3. Deduplicate chunks → one result per capture ────────────────────────
    deduped = _dedupe_by_capture(reranked)
    deduped.sort(key=lambda r: r.get("rerank_score", r.get("score", 0)), reverse=True)
    top_results = deduped[: req.top_k]

    # ── 4. Build response ─────────────────────────────────────────────────────
    response_items: list[CaptureResult] = []
    for r in top_results:
        capture_id = r.get("capture_id", "")
        row = metadata_db.fetch_capture_by_id(capture_id) if capture_id else None
        response_items.append(
            CaptureResult(
                capture_id=capture_id,
                source_type=r.get("source_type", ""),
                timestamp=r.get("timestamp", ""),
                content_preview=r.get("content_preview", ""),
                thumb_path=row["thumb_path"] if row else r.get("thumb_path"),
                window_title=r.get("window_title", ""),
                app_name=r.get("app_name", ""),
                url=r.get("url", ""),
                relevance_score=round(r.get("rerank_score", r.get("score", 0)), 4),
                chunk_index=r.get("chunk_index", 0),
            )
        )

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(f"Search q={req.query!r} → {len(response_items)} results ({total_candidates} candidates) in {elapsed_ms}ms")
    return SearchResponse(
        results=response_items,
        query_time_ms=elapsed_ms,
        total_candidates=total_candidates,
    )


@router.get("/related/{capture_id}")
async def related(capture_id: str, limit: int = Query(default=5, ge=1, le=20)) -> dict:
    """Return captures semantically related to the given capture_id."""
    try:
        from storage.graph_db import get_related
        results = get_related(capture_id, limit=limit)
        logger.info(f"Related capture_id={capture_id[:8]} → {len(results)} related")
    except Exception as exc:
        logger.error(f"Related lookup failed for {capture_id[:8]}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    formatted = [
        {
            "capture_id": r.get("id", ""),
            "source_type": r.get("source_type", ""),
            "timestamp": r.get("timestamp", ""),
            "content_preview": (r.get("content") or "")[:200],
            "thumb_path": r.get("thumb_path"),
            "window_title": r.get("window_title", ""),
            "app_name": r.get("app_name", ""),
            "url": r.get("url", ""),
            "similarity": r.get("similarity", 0),
            "edge_type": r.get("edge_type", "semantic"),
        }
        for r in results
    ]
    return {"capture_id": capture_id, "related": formatted, "count": len(formatted)}


@router.get("/search/timeline")
async def timeline(date: str = Query(..., description="Date in YYYY-MM-DD format")) -> dict:
    """Return all captures for a given day, ordered chronologically."""
    try:
        rows = metadata_db.fetch_captures_for_day(date)
    except Exception as exc:
        logger.error(f"Timeline fetch failed for {date}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    logger.info(f"Timeline date={date} → {len(rows)} captures")
    captures = [
        {
            "capture_id": r["id"],
            "source_type": r["source_type"],
            "timestamp": r["timestamp"],
            "content_preview": (r["content"] or "")[:200],
            "thumb_path": r["thumb_path"],
            "window_title": r["window_title"],
            "app_name": r["app_name"],
            "url": r["url"],
            "status": r["status"],
        }
        for r in rows
    ]
    return {"date": date, "captures": captures, "count": len(captures)}
