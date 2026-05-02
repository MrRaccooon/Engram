"""
Activity analytics routes.

All queries are pure SQLite aggregations — no ML required.

GET /api/activity/apps?from=YYYY-MM-DD&to=YYYY-MM-DD
    Time per app in seconds, grouped by app_name, for the given date range.

GET /api/activity/focus?date=YYYY-MM-DD
    Focus sessions for the given day: contiguous blocks of >= 20 minutes
    in the same application.

GET /api/activity/heatmap?weeks=4
    Hourly capture counts grouped by (weekday, hour) for the last N weeks.
    Used to render the GitHub-style activity heatmap.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query
from loguru import logger

from storage.metadata_db import _connect

router = APIRouter(tags=["activity"])

_SCREENSHOT_INTERVAL_S = 30  # assumed capture cadence for time estimation


# ── Helpers ───────────────────────────────────────────────────────────────────

def _date_range(from_date: str, to_date: str) -> tuple[str, str]:
    return from_date + "T00:00:00", to_date + "T23:59:59"


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/activity/apps")
async def app_time(
    from_date: str = Query(..., alias="from", description="YYYY-MM-DD"),
    to_date: str = Query(..., alias="to", description="YYYY-MM-DD"),
) -> dict[str, Any]:
    """
    Return estimated time per app (in seconds) for the given date range.
    Time is estimated as capture_count × screenshot_interval.
    """
    ts_from, ts_to = _date_range(from_date, to_date)

    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                app_name,
                COUNT(*) AS capture_count,
                COUNT(*) * ? AS estimated_seconds,
                date(timestamp) AS day
            FROM captures
            WHERE timestamp BETWEEN ? AND ?
              AND source_type = 'screenshot'
              AND app_name IS NOT NULL
              AND app_name != ''
              AND status = 'indexed'
            GROUP BY app_name, day
            ORDER BY day ASC, estimated_seconds DESC
            """,
            (_SCREENSHOT_INTERVAL_S, ts_from, ts_to),
        ).fetchall()

    # Aggregate by app across all days
    by_app: dict[str, int] = {}
    daily: dict[str, dict[str, int]] = {}  # day → {app: seconds}

    for row in rows:
        app = row["app_name"]
        secs = row["estimated_seconds"]
        day = row["day"]
        by_app[app] = by_app.get(app, 0) + secs
        if day not in daily:
            daily[day] = {}
        daily[day][app] = daily[day].get(app, 0) + secs

    sorted_apps = sorted(by_app.items(), key=lambda x: x[1], reverse=True)
    logger.info(f"Activity apps: {from_date}..{to_date} → {len(sorted_apps)} apps")

    return {
        "from": from_date,
        "to": to_date,
        "totals": [{"app": a, "seconds": s} for a, s in sorted_apps],
        "daily": [
            {"date": d, "apps": [{"app": a, "seconds": s} for a, s in sorted(apps.items(), key=lambda x: x[1], reverse=True)]}
            for d, apps in sorted(daily.items())
        ],
    }


@router.get("/activity/focus")
async def focus_sessions(
    date_str: str = Query(..., alias="date", description="YYYY-MM-DD"),
) -> dict[str, Any]:
    """
    Identify focus sessions: contiguous runs of >= 20 minutes in one app.
    Returns a list of sessions with start, end, duration, and app.
    """
    ts_from = date_str + "T00:00:00"
    ts_to = date_str + "T23:59:59"

    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT timestamp, app_name, window_title
            FROM captures
            WHERE timestamp BETWEEN ? AND ?
              AND source_type = 'screenshot'
              AND app_name IS NOT NULL AND app_name != ''
              AND status = 'indexed'
            ORDER BY timestamp ASC
            """,
            (ts_from, ts_to),
        ).fetchall()

    if not rows:
        return {"date": date_str, "sessions": []}

    sessions = []
    GAP_THRESHOLD_S = 90   # more than 90s gap = new session
    MIN_DURATION_S = 20 * 60  # sessions shorter than 20 min are excluded

    session_start = rows[0]["timestamp"]
    session_app = rows[0]["app_name"]
    session_title = rows[0]["window_title"] or ""
    prev_ts = rows[0]["timestamp"]

    def _parse(ts: str) -> datetime:
        return datetime.fromisoformat(ts)

    for row in rows[1:]:
        ts = row["timestamp"]
        app = row["app_name"]
        gap = (_parse(ts) - _parse(prev_ts)).total_seconds()

        if app != session_app or gap > GAP_THRESHOLD_S:
            duration = (_parse(prev_ts) - _parse(session_start)).total_seconds()
            if duration >= MIN_DURATION_S:
                sessions.append({
                    "app": session_app,
                    "window_title": session_title,
                    "start": session_start,
                    "end": prev_ts,
                    "duration_minutes": round(duration / 60, 1),
                })
            session_start = ts
            session_app = app
            session_title = row["window_title"] or ""

        prev_ts = ts

    # Close the last session
    duration = (_parse(prev_ts) - _parse(session_start)).total_seconds()
    if duration >= MIN_DURATION_S:
        sessions.append({
            "app": session_app,
            "window_title": session_title,
            "start": session_start,
            "end": prev_ts,
            "duration_minutes": round(duration / 60, 1),
        })

    logger.info(f"Focus sessions: date={date_str} → {len(sessions)} sessions")
    return {"date": date_str, "sessions": sessions}


@router.get("/activity/heatmap")
async def heatmap(
    weeks: int = Query(default=4, ge=1, le=52),
) -> dict[str, Any]:
    """
    Return hourly capture counts grouped by (weekday 0=Mon, hour 0-23)
    for the last N weeks. Used to draw the GitHub-style heatmap.
    """
    since = (datetime.utcnow() - timedelta(weeks=weeks)).isoformat()

    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                strftime('%w', timestamp) AS weekday,  -- 0=Sun, 1=Mon...
                CAST(strftime('%H', timestamp) AS INTEGER) AS hour,
                COUNT(*) AS count
            FROM captures
            WHERE timestamp >= ?
              AND source_type = 'screenshot'
              AND status = 'indexed'
            GROUP BY weekday, hour
            ORDER BY weekday, hour
            """,
            (since,),
        ).fetchall()

    # SQLite %w is 0=Sun, convert to 0=Mon
    cells = []
    for row in rows:
        wd = (int(row["weekday"]) + 6) % 7  # 0=Mon
        cells.append({"weekday": wd, "hour": row["hour"], "count": row["count"]})

    logger.info(f"Heatmap: weeks={weeks} → {len(cells)} cells")
    return {"weeks": weeks, "cells": cells}
