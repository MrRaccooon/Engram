"""
SQLite metadata store.

Two tables:
  captures   — one row per captured event (screenshot, clipboard, url, file)
  job_queue  — pending embedding jobs consumed by the cold-path worker
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from loguru import logger

_DB_PATH: Optional[Path] = None


def init(db_path: Path) -> None:
    """Create tables if they don't exist. Call once at startup."""
    global _DB_PATH
    _DB_PATH = db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(_SCHEMA_SQL)
    logger.info(f"SQLite metadata DB ready at {db_path}")


@contextmanager
def _connect() -> Generator[sqlite3.Connection, None, None]:
    assert _DB_PATH is not None, "Call metadata_db.init() before using the DB"
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS captures (
    id           TEXT PRIMARY KEY,
    timestamp    TEXT NOT NULL,
    source_type  TEXT NOT NULL CHECK(source_type IN ('screenshot','clipboard','url','file','audio')),
    raw_path     TEXT,
    thumb_path   TEXT,
    content      TEXT,
    phash        TEXT,
    window_title TEXT,
    app_name     TEXT,
    url          TEXT,
    status       TEXT NOT NULL DEFAULT 'pending'
                      CHECK(status IN ('pending','indexed','skipped','error'))
);

CREATE INDEX IF NOT EXISTS idx_captures_timestamp   ON captures(timestamp);
CREATE INDEX IF NOT EXISTS idx_captures_source_type ON captures(source_type);
CREATE INDEX IF NOT EXISTS idx_captures_status      ON captures(status);

CREATE TABLE IF NOT EXISTS job_queue (
    capture_id TEXT NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    attempts   INTEGER NOT NULL DEFAULT 0,
    error      TEXT
);

CREATE INDEX IF NOT EXISTS idx_job_queue_capture_id ON job_queue(capture_id);

-- Phase 2: Nightly consolidation insights
CREATE TABLE IF NOT EXISTS insights (
    id               TEXT PRIMARY KEY,
    date             TEXT NOT NULL,
    session_start    TEXT NOT NULL,
    session_end      TEXT NOT NULL,
    summary          TEXT NOT NULL,
    topics           TEXT,
    consolidated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_insights_date ON insights(date);

-- Phase 3: Semantic knowledge graph
CREATE TABLE IF NOT EXISTS capture_edges (
    source_id   TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    similarity  REAL NOT NULL,
    edge_type   TEXT NOT NULL,
    PRIMARY KEY (source_id, target_id)
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON capture_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON capture_edges(target_id);

CREATE TABLE IF NOT EXISTS capture_tags (
    capture_id  TEXT NOT NULL,
    tag         TEXT NOT NULL,
    tag_type    TEXT NOT NULL,
    PRIMARY KEY (capture_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_tags_capture ON capture_tags(capture_id);
CREATE INDEX IF NOT EXISTS idx_tags_tag     ON capture_tags(tag);
"""


# ── Write helpers ─────────────────────────────────────────────────────────────

def insert_capture(
    *,
    source_type: str,
    timestamp: Optional[datetime] = None,
    raw_path: Optional[str] = None,
    thumb_path: Optional[str] = None,
    content: Optional[str] = None,
    phash: Optional[str] = None,
    window_title: Optional[str] = None,
    app_name: Optional[str] = None,
    url: Optional[str] = None,
) -> str:
    """Insert a new capture row and enqueue it for embedding. Returns the UUID."""
    capture_id = str(uuid.uuid4())
    ts = (timestamp or datetime.utcnow()).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO captures
                (id, timestamp, source_type, raw_path, thumb_path, content,
                 phash, window_title, app_name, url, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (capture_id, ts, source_type, raw_path, thumb_path, content,
             phash, window_title, app_name, url),
        )
        conn.execute(
            "INSERT INTO job_queue (capture_id) VALUES (?)",
            (capture_id,),
        )
    return capture_id


def update_capture_status(capture_id: str, status: str, error: Optional[str] = None) -> None:
    """Update the status of a capture and optionally log an error on the queue row."""
    with _connect() as conn:
        conn.execute(
            "UPDATE captures SET status = ? WHERE id = ?",
            (status, capture_id),
        )
        if error:
            conn.execute(
                "UPDATE job_queue SET error = ? WHERE capture_id = ?",
                (error, capture_id),
            )


def increment_attempts(capture_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE job_queue SET attempts = attempts + 1 WHERE capture_id = ?",
            (capture_id,),
        )


# ── Read helpers ──────────────────────────────────────────────────────────────

def fetch_pending_jobs(limit: int = 32) -> list[sqlite3.Row]:
    """Return up to `limit` pending job rows joined with their capture metadata."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT c.*, jq.attempts, jq.created_at AS queued_at
            FROM job_queue jq
            JOIN captures c ON c.id = jq.capture_id
            WHERE c.status = 'pending' AND jq.attempts < 3
            ORDER BY jq.created_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return rows


def fetch_captures_in_window(
    center_ts: str, window_minutes: int = 5
) -> list[sqlite3.Row]:
    """Return all captures within ±window_minutes of center_ts (ISO format)."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM captures
            WHERE timestamp BETWEEN
                datetime(?, ?, '-' || ? || ' minutes')
                AND
                datetime(?, ?, '+' || ? || ' minutes')
            ORDER BY timestamp ASC
            """,
            (center_ts, center_ts, window_minutes,
             center_ts, center_ts, window_minutes),
        ).fetchall()
    return rows


def fetch_captures_for_day(date_str: str) -> list[sqlite3.Row]:
    """Return all captures for a given date (YYYY-MM-DD) ordered chronologically."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM captures
            WHERE date(timestamp) = ?
            ORDER BY timestamp ASC
            """,
            (date_str,),
        ).fetchall()
    return rows


def fetch_capture_by_id(capture_id: str) -> Optional[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM captures WHERE id = ?", (capture_id,)
        ).fetchone()


def count_captures() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM captures").fetchone()[0]


def count_pending_jobs() -> int:
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM job_queue jq JOIN captures c ON c.id = jq.capture_id WHERE c.status = 'pending'"
        ).fetchone()[0]


def delete_captures_before(cutoff_iso: str) -> int:
    """Delete all captures (and their queue rows) before cutoff. Returns deleted count."""
    with _connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM captures WHERE timestamp < ?", (cutoff_iso,)
        ).fetchone()[0]
        conn.execute("DELETE FROM captures WHERE timestamp < ?", (cutoff_iso,))
    return count


# ── Insights (Phase 2) ────────────────────────────────────────────────────────

def insert_insight(
    *,
    insight_id: str,
    date: str,
    session_start: str,
    session_end: str,
    summary: str,
    topics: Optional[str] = None,
) -> None:
    """Insert a consolidated insight summary."""
    consolidated_at = datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO insights
                (id, date, session_start, session_end, summary, topics, consolidated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (insight_id, date, session_start, session_end, summary, topics, consolidated_at),
        )


def fetch_insights_for_day(date_str: str) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM insights WHERE date = ? ORDER BY session_start ASC",
            (date_str,),
        ).fetchall()


def fetch_recent_insights(days: int = 7) -> list[sqlite3.Row]:
    from datetime import timedelta
    cutoff = (datetime.utcnow() - timedelta(days=days)).date().isoformat()
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM insights WHERE date >= ? ORDER BY date DESC, session_start ASC",
            (cutoff,),
        ).fetchall()


def fetch_latest_insight() -> Optional[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM insights ORDER BY consolidated_at DESC LIMIT 1"
        ).fetchone()


def has_insight_for_day(date_str: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM insights WHERE date = ? LIMIT 1", (date_str,)
        ).fetchone()
    return row is not None


# ── Graph / Tags (Phase 3) ────────────────────────────────────────────────────

def upsert_edge(source_id: str, target_id: str, similarity: float, edge_type: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO capture_edges (source_id, target_id, similarity, edge_type)
            VALUES (?, ?, ?, ?)
            """,
            (source_id, target_id, round(similarity, 4), edge_type),
        )


def fetch_related_captures(capture_id: str, limit: int = 5) -> list[sqlite3.Row]:
    """Return captures related to the given capture_id via the graph."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT c.*, e.similarity, e.edge_type
            FROM capture_edges e
            JOIN captures c ON c.id = e.target_id
            WHERE e.source_id = ?
            ORDER BY e.similarity DESC
            LIMIT ?
            """,
            (capture_id, limit),
        ).fetchall()
    return rows


def upsert_tags(capture_id: str, tags: list[tuple[str, str]]) -> None:
    """Insert (tag, tag_type) pairs for a capture, ignoring duplicates."""
    if not tags:
        return
    with _connect() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO capture_tags (capture_id, tag, tag_type) VALUES (?, ?, ?)",
            [(capture_id, tag, tag_type) for tag, tag_type in tags],
        )


def fetch_captures_by_tag(tag: str, limit: int = 20) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            """
            SELECT c.* FROM captures c
            JOIN capture_tags t ON t.capture_id = c.id
            WHERE t.tag = ?
            ORDER BY c.timestamp DESC
            LIMIT ?
            """,
            (tag, limit),
        ).fetchall()
