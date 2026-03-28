"""
Browser history collector.

Reads Chrome and Firefox SQLite history databases, converts their internal
timestamps to UTC datetimes, filters excluded domains, deduplicates against
already-seen URLs, and enqueues new entries for embedding.

Chrome and Firefox both lock their history DB while running. We copy the
file to a temp location before reading to avoid SQLite locking errors.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from loguru import logger

from pipeline import queue_manager

# Track URLs already enqueued in this session to avoid re-indexing on every poll
_seen_urls: set[str] = set()

# Chrome epoch: number of microseconds from 1601-01-01 to 1970-01-01
_CHROME_EPOCH_OFFSET = 11_644_473_600


def _chrome_ts_to_datetime(chrome_ts: int) -> datetime:
    """Convert Chrome's microsecond timestamp to UTC datetime."""
    unix_ts = chrome_ts / 1_000_000 - _CHROME_EPOCH_OFFSET
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).replace(tzinfo=None)


def _firefox_ts_to_datetime(firefox_ts: int) -> datetime:
    """Convert Firefox's microsecond-since-epoch timestamp to UTC datetime."""
    return datetime.utcfromtimestamp(firefox_ts / 1_000_000)


def _is_excluded(url: str, excluded_domains: list[str]) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    for pattern in excluded_domains:
        if pattern.startswith("*."):
            if host.endswith(pattern[2:]):
                return True
        elif host == pattern or host.endswith("." + pattern):
            return True
    return False


def _read_with_temp_copy(db_path: Path) -> Optional[sqlite3.Connection]:
    """Copy the DB to a temp file and open it (avoids lock on running browsers)."""
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        shutil.copy2(db_path, tmp.name)
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as exc:
        logger.warning(f"Could not copy {db_path.name}: {exc}")
        return None


def collect_chrome(excluded_domains: list[str], limit: int = 200) -> int:
    """
    Collect recent Chrome history entries not yet seen.
    Returns number of new captures enqueued.
    """
    history_path = Path.home() / "AppData/Local/Google/Chrome/User Data/Default/History"
    if not history_path.exists():
        return 0

    conn = _read_with_temp_copy(history_path)
    if conn is None:
        return 0

    count = 0
    try:
        rows = conn.execute(
            """
            SELECT url, title, last_visit_time
            FROM urls
            ORDER BY last_visit_time DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in rows:
            url = row["url"]
            if url in _seen_urls:
                continue
            if _is_excluded(url, excluded_domains):
                _seen_urls.add(url)
                continue
            ts = _chrome_ts_to_datetime(row["last_visit_time"])
            title = row["title"] or ""
            content = f"{title}\n{url}".strip()
            queue_manager.enqueue(
                source_type="url",
                timestamp=ts,
                content=content,
                url=url,
                window_title=title,
                app_name="chrome",
            )
            _seen_urls.add(url)
            count += 1
    except Exception as exc:
        logger.warning(f"Chrome history read error: {exc}")
    finally:
        conn.close()

    if count:
        logger.info(f"Chrome: enqueued {count} new URLs")
    return count


def collect_firefox(excluded_domains: list[str], limit: int = 200) -> int:
    """
    Collect recent Firefox history entries not yet seen.
    Returns number of new captures enqueued.
    """
    profiles_root = Path.home() / "AppData/Roaming/Mozilla/Firefox/Profiles"
    if not profiles_root.exists():
        return 0

    # Pick the first default-release profile found
    db_path: Optional[Path] = None
    for profile_dir in profiles_root.iterdir():
        candidate = profile_dir / "places.sqlite"
        if candidate.exists():
            db_path = candidate
            break

    if db_path is None:
        return 0

    conn = _read_with_temp_copy(db_path)
    if conn is None:
        return 0

    count = 0
    try:
        rows = conn.execute(
            """
            SELECT p.url, p.title, v.visit_date
            FROM moz_historyvisits v
            JOIN moz_places p ON p.id = v.place_id
            ORDER BY v.visit_date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in rows:
            url = row["url"]
            if url in _seen_urls:
                continue
            if _is_excluded(url, excluded_domains):
                _seen_urls.add(url)
                continue
            ts = _firefox_ts_to_datetime(row["visit_date"])
            title = row["title"] or ""
            content = f"{title}\n{url}".strip()
            queue_manager.enqueue(
                source_type="url",
                timestamp=ts,
                content=content,
                url=url,
                window_title=title,
                app_name="firefox",
            )
            _seen_urls.add(url)
            count += 1
    except Exception as exc:
        logger.warning(f"Firefox history read error: {exc}")
    finally:
        conn.close()

    if count:
        logger.info(f"Firefox: enqueued {count} new URLs")
    return count


def collect_all(excluded_domains: list[str]) -> int:
    """Collect from all supported browsers. Returns total new captures."""
    return collect_chrome(excluded_domains) + collect_firefox(excluded_domains)
