"""
Browser history collector.

Reads history from all major browsers — Brave, Chrome, Edge, Firefox,
Opera, Vivaldi, Arc — and enqueues new page visits for indexing.

Chromium-based browsers (Brave, Chrome, Edge, Opera, Vivaldi, Arc) all
use the same SQLite schema, so a single function covers them all with
different profile paths.

Firefox uses a different schema but the same copy-before-read trick
(both lock the DB while running).
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
from pipeline.context_parser import parse_window

# Track URLs already enqueued in this session to avoid re-indexing on every poll
_seen_urls: set[str] = set()

# Chrome epoch: microseconds from 1601-01-01 to 1970-01-01
_CHROME_EPOCH_OFFSET = 11_644_473_600


# ── Chromium browser profile paths ────────────────────────────────────────────

def _chromium_profiles() -> list[tuple[str, Path]]:
    """
    Return (browser_name, history_db_path) for every installed Chromium browser.
    Checks all known Windows/macOS/Linux profile locations.
    """
    home = Path.home()
    profiles: list[tuple[str, Path]] = []

    candidates: list[tuple[str, Path]] = [
        # Windows
        ("brave",   home / "AppData/Local/BraveSoftware/Brave-Browser/User Data/Default/History"),
        ("chrome",  home / "AppData/Local/Google/Chrome/User Data/Default/History"),
        ("edge",    home / "AppData/Local/Microsoft/Edge/User Data/Default/History"),
        ("opera",   home / "AppData/Roaming/Opera Software/Opera Stable/History"),
        ("vivaldi", home / "AppData/Local/Vivaldi/User Data/Default/History"),
        ("arc",     home / "AppData/Local/Arc/User Data/Default/History"),
        # macOS
        ("brave",   home / "Library/Application Support/BraveSoftware/Brave-Browser/Default/History"),
        ("chrome",  home / "Library/Application Support/Google/Chrome/Default/History"),
        ("edge",    home / "Library/Application Support/Microsoft Edge/Default/History"),
        ("opera",   home / "Library/Application Support/com.operasoftware.Opera/History"),
        ("vivaldi", home / "Library/Application Support/Vivaldi/Default/History"),
        ("arc",     home / "Library/Application Support/Arc/User Data/Default/History"),
        # Linux
        ("brave",   home / ".config/BraveSoftware/Brave-Browser/Default/History"),
        ("chrome",  home / ".config/google-chrome/Default/History"),
        ("chromium",home / ".config/chromium/Default/History"),
        ("edge",    home / ".config/microsoft-edge/Default/History"),
        ("opera",   home / ".config/opera/History"),
        ("vivaldi", home / ".config/vivaldi/Default/History"),
    ]

    seen_paths: set[str] = set()
    for name, path in candidates:
        key = str(path)
        if key not in seen_paths and path.exists():
            profiles.append((name, path))
            seen_paths.add(key)

    return profiles


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _chrome_ts(ts: int) -> datetime:
    unix = ts / 1_000_000 - _CHROME_EPOCH_OFFSET
    return datetime.fromtimestamp(unix, tz=timezone.utc).replace(tzinfo=None)


def _firefox_ts(ts: int) -> datetime:
    return datetime.utcfromtimestamp(ts / 1_000_000)


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


def _open_copy(db_path: Path) -> Optional[sqlite3.Connection]:
    """Copy the DB to a temp file and open it (avoids lock on running browsers)."""
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        shutil.copy2(db_path, tmp.name)
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as exc:
        logger.debug(f"Could not copy {db_path.name}: {exc}")
        return None


def _enrich_url_content(title: str, url: str, browser: str) -> str:
    """
    Build a rich, searchable description of a page visit by parsing its
    title through context_parser — same logic used for window titles.
    Returns a string suitable for vector embedding.
    """
    ctx = parse_window(title, browser)
    rich = ctx.get("rich_text") or ""

    # Always include the raw title + URL so keyword search still works
    parts = []
    if rich and rich != title:
        parts.append(rich)
    if title:
        parts.append(title)

    # Add domain as a keyword
    try:
        domain = urlparse(url).hostname or ""
        if domain:
            parts.append(f"site:{domain}")
    except Exception:
        pass

    return "\n".join(parts) if parts else title


# ── Chromium collector ─────────────────────────────────────────────────────────

def collect_chromium_browser(
    browser_name: str,
    db_path: Path,
    excluded_domains: list[str],
    limit: int = 200,
) -> int:
    """Collect history from any Chromium-based browser."""
    conn = _open_copy(db_path)
    if conn is None:
        return 0

    count = 0
    try:
        rows = conn.execute(
            "SELECT url, title, last_visit_time FROM urls ORDER BY last_visit_time DESC LIMIT ?",
            (limit,),
        ).fetchall()

        for row in rows:
            url = row["url"] or ""
            if not url or url in _seen_urls:
                continue
            if _is_excluded(url, excluded_domains):
                _seen_urls.add(url)
                continue

            title = row["title"] or ""
            ts = _chrome_ts(row["last_visit_time"])
            content = _enrich_url_content(title, url, browser_name)

            queue_manager.enqueue(
                source_type="url",
                timestamp=ts,
                content=content,
                url=url,
                window_title=title,
                app_name=browser_name,
            )
            _seen_urls.add(url)
            count += 1

    except Exception as exc:
        logger.warning(f"{browser_name} history read error: {exc}")
    finally:
        conn.close()

    if count:
        logger.info(f"{browser_name}: enqueued {count} new URLs")
    return count


# ── Firefox collector ──────────────────────────────────────────────────────────

def _firefox_db_paths() -> list[Path]:
    """Return all Firefox places.sqlite paths (handles multiple profiles)."""
    paths: list[Path] = []
    home = Path.home()
    roots: list[Path] = [
        home / "AppData/Roaming/Mozilla/Firefox/Profiles",  # Windows
        home / "Library/Application Support/Firefox/Profiles",  # macOS
        home / ".mozilla/firefox",  # Linux
    ]
    for root in roots:
        if root.is_dir():
            for profile_dir in root.iterdir():
                candidate = profile_dir / "places.sqlite"
                if candidate.exists():
                    paths.append(candidate)
    return paths


def collect_firefox(excluded_domains: list[str], limit: int = 200) -> int:
    """Collect recent Firefox history entries not yet seen."""
    db_paths = _firefox_db_paths()
    if not db_paths:
        return 0

    total = 0
    for db_path in db_paths:
        conn = _open_copy(db_path)
        if conn is None:
            continue

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
                url = row["url"] or ""
                if not url or url in _seen_urls:
                    continue
                if _is_excluded(url, excluded_domains):
                    _seen_urls.add(url)
                    continue

                title = row["title"] or ""
                ts = _firefox_ts(row["visit_date"])
                content = _enrich_url_content(title, url, "firefox")

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
            logger.warning(f"Firefox history read error ({db_path.parent.name}): {exc}")
        finally:
            conn.close()

        if count:
            logger.info(f"Firefox ({db_path.parent.name}): enqueued {count} new URLs")
        total += count

    return total


# ── Entry point ────────────────────────────────────────────────────────────────

def collect_all(excluded_domains: list[str]) -> int:
    """Collect from all installed browsers. Returns total new captures."""
    total = 0

    # All Chromium-based browsers
    for browser_name, db_path in _chromium_profiles():
        total += collect_chromium_browser(browser_name, db_path, excluded_domains)

    # Firefox
    total += collect_firefox(excluded_domains)

    return total
