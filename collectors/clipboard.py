"""
Clipboard collector.

Polls the Windows clipboard on a configurable interval, detects genuine
changes via content hash, and enqueues new clipboard events for embedding.
Deduplicates consecutive identical copies so rapid Ctrl+C spam is ignored.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

import win32clipboard
from loguru import logger

from pipeline import queue_manager
from collectors.window_context import get_active_window

_last_content_hash: Optional[str] = None


def _read_clipboard() -> Optional[str]:
    """Return current clipboard text, or None if empty / non-text."""
    try:
        win32clipboard.OpenClipboard()
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
            data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            return data if isinstance(data, str) else None
        return None
    except Exception:
        return None
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def poll() -> Optional[str]:
    """
    Read clipboard and enqueue if content has changed since last poll.

    Returns the capture UUID on a new capture, else None.
    """
    global _last_content_hash

    text = _read_clipboard()
    if not text or not text.strip():
        return None

    h = _content_hash(text)
    if h == _last_content_hash:
        return None  # no change
    _last_content_hash = h

    window_title, app_name = get_active_window()
    ts = datetime.utcnow()

    capture_id = queue_manager.enqueue(
        source_type="clipboard",
        timestamp=ts,
        content=text,
        window_title=window_title,
        app_name=app_name,
    )

    preview = text[:60].replace("\n", " ")
    logger.debug(f"Clipboard captured: '{preview}...' ({capture_id[:8]})")
    return capture_id
