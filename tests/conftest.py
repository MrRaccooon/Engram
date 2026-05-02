"""
Shared fixtures for Engram smoke tests.

Provides:
  - Temporary SQLite database (real DB, no mocks)
  - Mock patches for heavy external deps (embedder, vector_db, reranker, spaCy, LLMs)
  - Helper factories for fake captures, insights, chunks
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ── Temporary SQLite database ─────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path):
    """Initialise metadata_db with a fresh temp database. Yields the db path."""
    from storage import metadata_db
    db_path = tmp_path / "test_metadata.db"
    metadata_db.init(db_path)
    yield db_path
    metadata_db._DB_PATH = None


# ── Capture factory ───────────────────────────────────────────────────────────

def make_capture(
    *,
    source_type: str = "screenshot",
    content: str = "some test content",
    window_title: str = "Test Window",
    app_name: str = "Code.exe",
    url: str = "",
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    ts = timestamp or datetime.utcnow()
    return {
        "id": str(uuid.uuid4()),
        "timestamp": ts.isoformat(),
        "source_type": source_type,
        "raw_path": None,
        "thumb_path": None,
        "content": content,
        "content_preview": content[:300],
        "phash": None,
        "window_title": window_title,
        "app_name": app_name,
        "url": url,
        "status": "indexed",
    }


def make_chunk(
    *,
    capture_id: str | None = None,
    content: str = "chunk text content",
    content_preview: str | None = None,
    source_type: str = "screenshot",
    timestamp: str | None = None,
    app_name: str = "Code.exe",
    window_title: str = "editor - main.py",
    url: str = "",
    score: float = 0.8,
) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "capture_id": capture_id or str(uuid.uuid4()),
        "content": content,
        "content_preview": content_preview or content[:300],
        "source_type": source_type,
        "timestamp": timestamp or datetime.utcnow().isoformat(),
        "app_name": app_name,
        "window_title": window_title,
        "url": url,
        "score": score,
    }


def make_insight(
    *,
    date: str | None = None,
    summary: str = "Worked on Engram memory system",
    narrative: str = "Detailed narrative of work session",
    topics: list[str] | None = None,
) -> dict[str, Any]:
    d = date or datetime.utcnow().date().isoformat()
    return {
        "date": d,
        "summary": summary,
        "summary_preview": summary[:300],
        "narrative": narrative,
        "topics": topics or ["engram", "memory"],
    }


# ── Insert helpers for DB tests ───────────────────────────────────────────────

def insert_test_capture(mdb, **kwargs) -> str:
    """Insert a capture into the DB and return its ID."""
    defaults = {
        "source_type": "screenshot",
        "content": "test content for capture",
        "window_title": "Test",
        "app_name": "Code.exe",
    }
    defaults.update(kwargs)
    return mdb.insert_capture(**defaults)
