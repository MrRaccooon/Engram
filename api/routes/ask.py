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

import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from pipeline import embedder, reranker
from pipeline import intelligence
from storage import vector_db

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _retrieve_candidates(
    query: str,
    top_k: int,
    filters: AskFilters,
) -> list[dict[str, Any]]:
    """Dual vector retrieval — same logic as the search route."""
    retrieval_top_k = min(top_k * 5, 50)

    where: Optional[dict] = None
    conditions = []
    if filters.source_types:
        conditions.append({"source_type": {"$in": filters.source_types}})
    if filters.date_from:
        conditions.append({"timestamp": {"$gte": filters.date_from}})
    if filters.date_to:
        conditions.append({"timestamp": {"$lte": filters.date_to + "T23:59:59"}})
    if len(conditions) == 1:
        where = conditions[0]
    elif len(conditions) > 1:
        where = {"$and": conditions}

    text_vec = embedder.embed_text(query)
    text_results = vector_db.query_text(text_vec, top_k=retrieval_top_k, where=where)

    visual_vec = embedder.embed_query_text_clip(query)
    visual_results: list[dict[str, Any]] = []
    if visual_vec:
        visual_results = vector_db.query_visual(
            visual_vec, top_k=retrieval_top_k // 2, where=where
        )

    merged: dict[str, dict] = {}
    for r in text_results:
        merged[r["id"]] = r
    for r in visual_results:
        if r["id"] not in merged:
            r.setdefault("content_preview", "")
            merged[r["id"]] = r

    candidates = list(merged.values())

    reranked = reranker.rerank(
        query=query,
        candidates=candidates,
        top_n=top_k,
        text_field="content_preview",
    )

    return reranked


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/ask/preview", response_model=PreviewResponse)
async def ask_preview(req: AskRequest) -> PreviewResponse:
    """
    Build the masked prompt that would be sent to the API.
    No external API call is made. Used by the frontend confirmation modal.
    """
    candidates = _retrieve_candidates(req.query, req.top_k, req.filters)

    try:
        preview = intelligence.build_preview(req.query, candidates)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return PreviewResponse(**preview)


@router.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest) -> AskResponse:
    """
    Run the full privacy pipeline and call the configured frontier API.
    Returns the synthesized answer with real entity names restored.
    """
    t0 = time.perf_counter()

    candidates = _retrieve_candidates(req.query, req.top_k, req.filters)

    try:
        result = intelligence.ask(req.query, candidates, deep=req.deep)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    return AskResponse(
        answer=result["answer"],
        blocked_count=result["blocked_count"],
        passing_count=result["passing_count"],
        model_used=result["model_used"],
        provider=result["provider"],
        query_time_ms=elapsed_ms,
    )
