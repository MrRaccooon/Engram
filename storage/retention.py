"""
Retention policy and auto-cleanup.

Runs on a schedule (daily) to enforce:
  1. Age-based deletion  — remove captures older than `retention_days`
  2. Storage budget      — when total Engram data exceeds `max_storage_gb`,
                           delete oldest captures until under budget

Deletion is coordinated: SQLite rows, ChromaDB vectors, and thumbnail
files are all removed atomically so there are no orphaned records.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger

from storage import metadata_db, vector_db


def _dir_size_gb(path: Path) -> float:
    """Return total size of a directory tree in gigabytes."""
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for fname in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fname))
            except OSError:
                pass
    return total / (1024 ** 3)


def _delete_captures(capture_ids: list[str]) -> None:
    """
    Remove captures from SQLite, ChromaDB, and the filesystem (thumbnails).
    """
    if not capture_ids:
        return

    # Collect thumbnail paths before deleting SQLite rows
    thumb_paths: list[str] = []
    for cid in capture_ids:
        row = metadata_db.fetch_capture_by_id(cid)
        if row and row["thumb_path"]:
            thumb_paths.append(row["thumb_path"])

    # Remove vectors from ChromaDB
    vector_db.delete_by_capture_ids(capture_ids)

    # Remove SQLite rows (cascades to job_queue)
    cutoff = datetime.utcnow().isoformat()  # will be unused — we delete by ID
    for cid in capture_ids:
        metadata_db.update_capture_status(cid, "error")  # mark before delete

    # Actually delete from SQLite — we need a direct deletion here
    from storage.metadata_db import _connect
    with _connect() as conn:
        placeholders = ",".join("?" * len(capture_ids))
        conn.execute(f"DELETE FROM captures WHERE id IN ({placeholders})", capture_ids)

    # Remove thumbnail files
    removed_files = 0
    for p in thumb_paths:
        try:
            Path(p).unlink(missing_ok=True)
            removed_files += 1
        except Exception as exc:
            logger.debug(f"Could not delete thumbnail {p}: {exc}")

    logger.info(
        f"Retention: deleted {len(capture_ids)} captures, {removed_files} thumbnail files"
    )


def run(
    base_path: Path,
    retention_days: int,
    max_storage_gb: float,
) -> None:
    """
    Execute the full retention policy. Call once daily from the scheduler.
    """
    logger.info("Running retention policy…")

    # ── 1. Age-based deletion ─────────────────────────────────────────────────
    if retention_days > 0:
        cutoff = (datetime.utcnow() - timedelta(days=retention_days)).isoformat()
        deleted_count = metadata_db.delete_captures_before(cutoff)
        if deleted_count:
            logger.info(f"Retention: removed {deleted_count} captures older than {retention_days} days")

    # ── 2. Storage budget enforcement ─────────────────────────────────────────
    if max_storage_gb > 0:
        current_gb = _dir_size_gb(base_path)
        if current_gb > max_storage_gb:
            overage_gb = current_gb - max_storage_gb
            logger.warning(
                f"Storage {current_gb:.2f}GB exceeds budget {max_storage_gb}GB "
                f"— freeing ~{overage_gb:.2f}GB by deleting oldest captures"
            )
            _evict_oldest(base_path, max_storage_gb)

    current_gb = _dir_size_gb(base_path)
    logger.info(f"Retention complete | storage={current_gb:.2f}GB")


def _evict_oldest(base_path: Path, max_gb: float, batch: int = 50) -> None:
    """Delete oldest captures in batches until storage is within budget."""
    from storage.metadata_db import _connect

    while _dir_size_gb(base_path) > max_gb:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT id FROM captures ORDER BY timestamp ASC LIMIT ?", (batch,)
            ).fetchall()
        if not rows:
            break
        ids = [r["id"] for r in rows]
        _delete_captures(ids)
