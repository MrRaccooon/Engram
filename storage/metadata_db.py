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
