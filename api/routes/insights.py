"""
Insights routes.

GET /api/insights?date=YYYY-MM-DD
    Return the consolidated insight summaries for a given day.
    If no date is provided, returns the last 7 days.

GET /api/insights/latest
    Return the most recent insight summary (for daily digest display).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query
from loguru import logger

from storage import metadata_db

router = APIRouter(tags=["insights"])


def _format_insight(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "date": row["date"],
        "session_start": row["session_start"],
        "session_end": row["session_end"],
        "summary": row["summary"],
        "topics": row["topics"],
        "consolidated_at": row["consolidated_at"],
        "narrative": row["narrative"],
        "topics_structured": row["topics_structured"],
        "projects": row["projects"],
        "files_touched": row["files_touched"],
        "decisions": row["decisions"],
        "problems": row["problems"],
        "outcomes": row["outcomes"],
        "consolidation_type": row["consolidation_type"],
    }


@router.get("/insights")
async def get_insights(
    date: Optional[str] = Query(None, description="YYYY-MM-DD (omit for last 7 days)"),
) -> dict[str, Any]:
    """Return insight summaries for a date or the last 7 days."""
    try:
        if date:
            rows = metadata_db.fetch_insights_for_day(date)
        else:
            rows = metadata_db.fetch_recent_insights(days=7)
    except Exception as exc:
        logger.error(f"Insights fetch failed: {exc}")
        return {"insights": [], "error": str(exc)}

    insights = [_format_insight(row) for row in rows]

    logger.info(f"Insights: date={date or 'last_7_days'} → {len(insights)} entries")
    return {"date": date, "insights": insights, "count": len(insights)}


@router.get("/insights/latest")
async def get_latest_insight() -> dict[str, Any]:
    """Return the most recent single insight summary."""
    try:
        row = metadata_db.fetch_latest_insight()
    except Exception as exc:
        logger.error(f"Latest insight fetch failed: {exc}")
        return {"insight": None, "error": str(exc)}

    if not row:
        return {"insight": None}

    return {
        "insight": _format_insight(row)
    }


@router.get("/learning/summary")
async def get_learning_summary() -> dict[str, Any]:
    """Return accumulated topic threads: what Engram has learned so far."""
    try:
        rows = metadata_db.fetch_all_topic_threads()
    except Exception as exc:
        logger.error(f"Learning summary fetch failed: {exc}")
        return {"topics": [], "count": 0, "error": str(exc)}

    topics = [
        {
            "id": row["id"],
            "topic": row["topic"],
            "summary": row["summary"],
            "total_sessions": row["total_sessions"],
            "total_minutes": row["total_minutes"],
            "projects": row["projects"],
            "files_touched": row["files_touched"],
            "decisions": row["decisions"],
            "last_updated": row["last_updated"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]
    return {"topics": topics, "count": len(topics)}
