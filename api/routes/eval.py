"""
Evaluation routes.

POST /api/eval/feedback  — Record user feedback (thumbs up/down + optional note)
                           on a previously answered query.

GET  /api/eval/metrics   — Compute aggregate retrieval quality metrics over
                           logged queries: satisfaction rate, avg latency,
                           source distribution, and per-intent breakdowns.

GET  /api/eval/log       — Return recent eval log entries for inspection.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from loguru import logger

from storage import metadata_db

router = APIRouter(tags=["eval"])


# ── Pydantic models ───────────────────────────────────────────────────────────

class FeedbackRequest(BaseModel):
    query_id: str = Field(..., description="ID of the eval log entry to annotate")
    rating: int = Field(..., ge=-1, le=1, description="-1 bad, 0 neutral, 1 good")
    note: Optional[str] = Field(None, max_length=500)


class FeedbackResponse(BaseModel):
    status: str
    query_id: str


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/eval/feedback", response_model=FeedbackResponse)
async def submit_feedback(req: FeedbackRequest) -> FeedbackResponse:
    """Record user feedback on a query answer."""
    try:
        metadata_db.update_eval_feedback(req.query_id, req.rating, req.note)
    except Exception as exc:
        logger.error(f"Eval feedback failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    logger.info(f"Eval feedback: query_id={req.query_id} rating={req.rating}")
    return FeedbackResponse(status="ok", query_id=req.query_id)


@router.get("/eval/metrics")
async def get_metrics(
    days: int = Query(default=7, ge=1, le=90, description="Look-back window in days"),
) -> dict[str, Any]:
    """Compute aggregate quality metrics from eval logs."""
    try:
        rows = metadata_db.fetch_eval_logs(days=days)
    except Exception as exc:
        logger.error(f"Eval metrics failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    if not rows:
        return {"days": days, "total_queries": 0, "metrics": {}}

    total = len(rows)
    rated = [r for r in rows if r["feedback_rating"] is not None]
    positive = sum(1 for r in rated if r["feedback_rating"] > 0)
    negative = sum(1 for r in rated if r["feedback_rating"] < 0)

    latencies = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
    avg_latency = round(sum(latencies) / len(latencies)) if latencies else 0
    p95_latency = _percentile(latencies, 95) if latencies else 0

    candidate_counts = [r["candidate_count"] for r in rows if r["candidate_count"] is not None]
    avg_candidates = round(sum(candidate_counts) / len(candidate_counts), 1) if candidate_counts else 0

    # Source usage distribution
    source_counts: dict[str, int] = {}
    for r in rows:
        sources = r.get("sources_used") or ""
        for src in sources.split(","):
            src = src.strip()
            if src:
                source_counts[src] = source_counts.get(src, 0) + 1

    # Per-intent breakdown
    intent_stats: dict[str, dict[str, int]] = {}
    for r in rows:
        intent = r.get("intent") or "unknown"
        if intent not in intent_stats:
            intent_stats[intent] = {"total": 0, "rated": 0, "positive": 0}
        intent_stats[intent]["total"] += 1
        if r["feedback_rating"] is not None:
            intent_stats[intent]["rated"] += 1
            if r["feedback_rating"] > 0:
                intent_stats[intent]["positive"] += 1

    metrics = {
        "total_queries": total,
        "rated_queries": len(rated),
        "satisfaction_rate": round(positive / len(rated), 3) if rated else None,
        "negative_rate": round(negative / len(rated), 3) if rated else None,
        "avg_latency_ms": avg_latency,
        "p95_latency_ms": p95_latency,
        "avg_candidates": avg_candidates,
        "source_distribution": source_counts,
        "intent_breakdown": intent_stats,
    }

    return {"days": days, "total_queries": total, "metrics": metrics}


@router.get("/eval/log")
async def get_eval_log(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Return recent eval log entries."""
    try:
        rows = metadata_db.fetch_eval_logs_paginated(limit=limit, offset=offset)
    except Exception as exc:
        logger.error(f"Eval log fetch failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    entries = []
    for r in rows:
        entries.append({
            "id": r["id"],
            "query": r["query"],
            "intent": r.get("intent"),
            "candidate_count": r.get("candidate_count"),
            "sources_used": r.get("sources_used"),
            "model_used": r.get("model_used"),
            "latency_ms": r.get("latency_ms"),
            "feedback_rating": r.get("feedback_rating"),
            "feedback_note": r.get("feedback_note"),
            "created_at": r.get("created_at"),
        })

    return {"entries": entries, "count": len(entries), "offset": offset}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _percentile(data: list[int], pct: int) -> int:
    if not data:
        return 0
    s = sorted(data)
    idx = int(len(s) * pct / 100)
    idx = min(idx, len(s) - 1)
    return s[idx]
