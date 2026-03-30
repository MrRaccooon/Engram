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

    insights = [
        {
            "id": row["id"],
            "date": row["date"],
            "session_start": row["session_start"],
            "session_end": row["session_end"],
            "summary": row["summary"],
            "topics": row["topics"],
            "consolidated_at": row["consolidated_at"],
        }
        for row in rows
    ]

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
        "insight": {
            "id": row["id"],
            "date": row["date"],
            "session_start": row["session_start"],
            "session_end": row["session_end"],
            "summary": row["summary"],
            "topics": row["topics"],
            "consolidated_at": row["consolidated_at"],
        }
    }
