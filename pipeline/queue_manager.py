"""
Hot-path queue manager.

This is the only write path for all collectors. It inserts a capture row
into SQLite and appends it to the job_queue in a single transaction.
The cold-path worker drains the queue asynchronously.

Design goal: complete in < 50 ms regardless of what the embedding worker
is doing. Collectors call enqueue() and return immediately.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from loguru import logger

from storage import metadata_db


def enqueue(
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
    diff_data: Optional[str] = None,
) -> str:
    """
    Write a capture to SQLite and add it to the job_queue.

    Returns the new capture UUID.
    Logs and re-raises on DB errors so the scheduler can catch them.
    """
    capture_id = metadata_db.insert_capture(
        source_type=source_type,
        timestamp=timestamp,
        raw_path=raw_path,
        thumb_path=thumb_path,
        content=content,
        phash=phash,
        window_title=window_title or "",
        app_name=app_name or "",
        url=url or "",
        diff_data=diff_data,
    )
    logger.debug(
        f"Enqueued [{source_type}] {capture_id[:8]} "
        f"app={app_name or '-'} title={str(window_title or '')[:40]}"
    )
    return capture_id
