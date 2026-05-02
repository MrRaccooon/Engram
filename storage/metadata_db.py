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


_MIGRATIONS = [
    ("narrative",          "ALTER TABLE insights ADD COLUMN narrative TEXT"),
    ("topics_structured",  "ALTER TABLE insights ADD COLUMN topics_structured TEXT"),
    ("projects",           "ALTER TABLE insights ADD COLUMN projects TEXT"),
    ("files_touched",      "ALTER TABLE insights ADD COLUMN files_touched TEXT"),
    ("decisions",          "ALTER TABLE insights ADD COLUMN decisions TEXT"),
    ("problems",           "ALTER TABLE insights ADD COLUMN problems TEXT"),
    ("outcomes",           "ALTER TABLE insights ADD COLUMN outcomes TEXT"),
    ("consolidation_type", "ALTER TABLE insights ADD COLUMN consolidation_type TEXT NOT NULL DEFAULT 'daily'"),
]


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Add new columns to existing tables if they're missing."""
    try:
        existing = {
            row[1] for row in conn.execute("PRAGMA table_info(insights)").fetchall()
        }
    except Exception:
        return

    for col_name, sql in _MIGRATIONS:
        if col_name not in existing:
            try:
                conn.execute(sql)
                logger.debug(f"Migration: added column insights.{col_name}")
            except Exception:
                pass


def init(db_path: Path) -> None:
    """Create tables if they don't exist. Call once at startup."""
    global _DB_PATH
    _DB_PATH = db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(_SCHEMA_SQL)
        _run_migrations(conn)
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
    id                  TEXT PRIMARY KEY,
    date                TEXT NOT NULL,
    session_start       TEXT NOT NULL,
    session_end         TEXT NOT NULL,
    summary             TEXT NOT NULL,
    topics              TEXT,
    consolidated_at     TEXT NOT NULL,
    narrative           TEXT,
    topics_structured   TEXT,
    projects            TEXT,
    files_touched       TEXT,
    decisions           TEXT,
    problems            TEXT,
    outcomes            TEXT,
    consolidation_type  TEXT NOT NULL DEFAULT 'daily'
);

CREATE INDEX IF NOT EXISTS idx_insights_date ON insights(date);
CREATE INDEX IF NOT EXISTS idx_insights_type ON insights(consolidation_type);

-- Topic threads: accumulated knowledge across sessions
CREATE TABLE IF NOT EXISTS topic_threads (
    id              TEXT PRIMARY KEY,
    topic           TEXT NOT NULL UNIQUE,
    summary         TEXT NOT NULL DEFAULT '',
    total_sessions  INTEGER NOT NULL DEFAULT 0,
    total_minutes   REAL NOT NULL DEFAULT 0,
    projects        TEXT,
    files_touched   TEXT,
    decisions       TEXT,
    last_updated    TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_topic_threads_topic ON topic_threads(topic);

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

def fetch_pending_jobs(limit: int = 64) -> list[sqlite3.Row]:
    """
    Return up to `limit` pending job rows joined with their capture metadata.

    Priority order — screenshots and files first (time-sensitive context),
    then clipboard, then URL/browser history (historical, can wait):
      1. screenshot
      2. file
      3. clipboard
      4. url / audio / other
    Within each priority tier, ordered by creation time (oldest first).
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT c.*, jq.attempts, jq.created_at AS queued_at,
                   CASE c.source_type
                       WHEN 'screenshot' THEN 1
                       WHEN 'file'       THEN 2
                       WHEN 'clipboard'  THEN 3
                       ELSE 4
                   END AS priority
            FROM job_queue jq
            JOIN captures c ON c.id = jq.capture_id
            WHERE c.status = 'pending' AND jq.attempts < 3
            ORDER BY priority ASC, jq.created_at ASC
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
    narrative: Optional[str] = None,
    topics_structured: Optional[str] = None,
    projects: Optional[str] = None,
    files_touched: Optional[str] = None,
    decisions: Optional[str] = None,
    problems: Optional[str] = None,
    outcomes: Optional[str] = None,
    consolidation_type: str = "daily",
) -> None:
    """Insert a consolidated insight summary."""
    consolidated_at = datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO insights
                (id, date, session_start, session_end, summary, topics,
                 consolidated_at, narrative, topics_structured, projects,
                 files_touched, decisions, problems, outcomes, consolidation_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (insight_id, date, session_start, session_end, summary, topics,
             consolidated_at, narrative, topics_structured, projects,
             files_touched, decisions, problems, outcomes, consolidation_type),
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


def has_insight_for_day(date_str: str, consolidation_type: str = "daily") -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM insights WHERE date = ? AND consolidation_type = ? LIMIT 1",
            (date_str, consolidation_type),
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
    """Return captures related to the given capture_id via the graph (both directions)."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT c.*, e.similarity, e.edge_type
            FROM capture_edges e
            JOIN captures c ON c.id = CASE
                WHEN e.source_id = ? THEN e.target_id
                ELSE e.source_id
            END
            WHERE (e.source_id = ? OR e.target_id = ?)
              AND c.id != ?
            ORDER BY e.similarity DESC
            LIMIT ?
            """,
            (capture_id, capture_id, capture_id, capture_id, limit),
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


def fetch_captures_in_range(
    start_iso: str, end_iso: str, limit: int = 100
) -> list[sqlite3.Row]:
    """Return indexed captures between start and end timestamps."""
    with _connect() as conn:
        return conn.execute(
            """
            SELECT * FROM captures
            WHERE timestamp >= ? AND timestamp <= ?
              AND status = 'indexed'
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (start_iso, end_iso + "T23:59:59" if len(end_iso) == 10 else end_iso, limit),
        ).fetchall()


def fetch_distinct_tags(limit: int = 500) -> list[str]:
    """Return the most common tags across all captures."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT tag, COUNT(*) AS cnt FROM capture_tags
            GROUP BY tag ORDER BY cnt DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [r["tag"] for r in rows]


# ── Topic threads ──────────────────────────────────────────────────────────────

def upsert_topic_thread(
    *,
    topic: str,
    summary: str,
    session_count_delta: int = 1,
    minutes_delta: float = 0,
    projects: Optional[str] = None,
    files_touched: Optional[str] = None,
    decisions: Optional[str] = None,
) -> str:
    """Create or update a topic thread. Returns the thread ID."""
    now = datetime.utcnow().isoformat()
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id, total_sessions, total_minutes FROM topic_threads WHERE topic = ?",
            (topic,),
        ).fetchone()

        if existing:
            thread_id = existing["id"]
            conn.execute(
                """
                UPDATE topic_threads
                SET summary = ?, total_sessions = total_sessions + ?,
                    total_minutes = total_minutes + ?, projects = COALESCE(?, projects),
                    files_touched = COALESCE(?, files_touched),
                    decisions = COALESCE(?, decisions), last_updated = ?
                WHERE id = ?
                """,
                (summary, session_count_delta, minutes_delta,
                 projects, files_touched, decisions, now, thread_id),
            )
        else:
            thread_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO topic_threads
                    (id, topic, summary, total_sessions, total_minutes,
                     projects, files_touched, decisions, last_updated, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (thread_id, topic, summary, session_count_delta, minutes_delta,
                 projects, files_touched, decisions, now, now),
            )
    return thread_id


def fetch_topic_thread(topic: str) -> Optional[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM topic_threads WHERE topic = ?", (topic,),
        ).fetchone()


def fetch_all_topic_threads() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM topic_threads ORDER BY last_updated DESC",
        ).fetchall()


def count_topic_occurrences(topic: str) -> int:
    """Count how many insights mention this topic."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM insights WHERE topics_structured LIKE ?",
            (f"%{topic}%",),
        ).fetchone()
    return row[0] if row else 0


# ── Session context helpers ────────────────────────────────────────────────────

def fetch_recent_captures(minutes: int = 60, limit: int = 40) -> list[sqlite3.Row]:
    """
    Return the last N captures from the past `minutes` minutes.
    Includes both indexed and pending captures so the session context
    is available immediately, not only after the worker drains the queue.
    Excludes URL captures (browser history) since those are historical,
    not indicative of the current session.
    """
    from datetime import timedelta
    cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
    with _connect() as conn:
        return conn.execute(
            """
            SELECT * FROM captures
            WHERE timestamp >= ?
              AND status IN ('indexed', 'pending')
              AND source_type != 'url'
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()


def fetch_top_apps(hours: int = 24, limit: int = 5) -> list[sqlite3.Row]:
    """Return the most-used apps over the last N hours, by capture count."""
    from datetime import timedelta
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    with _connect() as conn:
        return conn.execute(
            """
            SELECT app_name, COUNT(*) AS cnt
            FROM captures
            WHERE timestamp >= ? AND app_name IS NOT NULL AND app_name != ''
            GROUP BY app_name
            ORDER BY cnt DESC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()


def fetch_top_window_titles(hours: int = 4, limit: int = 8) -> list[sqlite3.Row]:
    """Return distinct window titles seen in the last N hours (for context)."""
    from datetime import timedelta
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    with _connect() as conn:
        return conn.execute(
            """
            SELECT window_title, app_name, MAX(timestamp) AS last_seen, COUNT(*) AS cnt
            FROM captures
            WHERE timestamp >= ? AND window_title IS NOT NULL AND window_title != ''
            GROUP BY window_title
            ORDER BY cnt DESC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
