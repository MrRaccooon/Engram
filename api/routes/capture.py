"""
Capture routes.

GET  /api/status            — daemon health, queue depth, indexed count, storage
POST /api/capture/manual    — trigger an immediate capture from the frontend / hotkey
GET  /api/context/:id       — all captures within ±N minutes of a given capture
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from loguru import logger

from storage import metadata_db, vector_db

router = APIRouter(tags=["capture"])

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "config.yaml"


def _memory_signals(capture_id: str) -> tuple[list[dict], list[dict]]:
    """Return visual concepts and action events attached to a capture."""
    try:
        concept_rows = metadata_db.fetch_concepts_for_capture(capture_id, limit=6)
        concepts = [
            {
                "id": r["id"],
                "prompt": r["prompt"],
                "category": r["category"],
                "confidence": round(float(r["confidence"]), 4),
            }
            for r in concept_rows
        ]
    except Exception:
        concepts = []

    try:
        event_rows = metadata_db.fetch_events_for_capture(capture_id, limit=3)
        events = [
            {
                "id": r["id"],
                "change_type": r["change_type"],
                "change_magnitude": round(float(r["change_magnitude"]), 4),
                "changed_text": r["changed_text"] or "",
            }
            for r in event_rows
        ]
    except Exception:
        events = []

    return concepts, events


def _load_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _storage_mb(base_path: Path) -> float:
    total = 0
    for dirpath, _, filenames in os.walk(base_path):
        for fname in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fname))
            except OSError:
                pass
    return round(total / (1024 ** 2), 1)


# ── /status ───────────────────────────────────────────────────────────────────

@router.get("/status")
async def status() -> dict:
    cfg = _load_config()
    base = Path(cfg["storage"]["base_path"]).expanduser()

    indexed = metadata_db.count_captures()
    queue_depth = metadata_db.count_pending_jobs()
    text_vecs = vector_db.count_text()
    visual_vecs = vector_db.count_visual()
    storage_mb = _storage_mb(base)

    logger.debug(f"Status: indexed={indexed} queue={queue_depth} text_vecs={text_vecs} visual_vecs={visual_vecs} storage={storage_mb}MB")
    return {
        "daemon_running": True,
        "indexed_captures": indexed,
        "pending_queue": queue_depth,
        "text_vectors": text_vecs,
        "visual_vectors": visual_vecs,
        "storage_mb": storage_mb,
        "timestamp": datetime.utcnow().isoformat(),
    }


# ── /capture/manual ───────────────────────────────────────────────────────────

class ManualCaptureResponse(BaseModel):
    capture_id: str
    status: str
    message: str


@router.post("/capture/manual", response_model=ManualCaptureResponse)
async def manual_capture() -> ManualCaptureResponse:
    """
    Trigger an immediate screenshot + clipboard capture.
    Called by the global hotkey (Ctrl+Shift+M) or the frontend button.
    """
    cfg = _load_config()
    base = Path(cfg["storage"]["base_path"]).expanduser()
    thumb_size: int = cfg.get("storage", {}).get("thumbnail_size", 400)

    try:
        from collectors import screenshot, clipboard
        ss_id = screenshot.capture(storage_root=base, thumbnail_size=thumb_size)
        clip_id = clipboard.poll()
        primary_id = ss_id or clip_id or "none"
        logger.info(f"Manual capture: screenshot={ss_id or 'skip'} clipboard={clip_id or 'skip'}")
        return ManualCaptureResponse(
            capture_id=primary_id,
            status="queued",
            message="Screenshot and clipboard captured and queued for embedding",
        )
    except Exception as exc:
        logger.error(f"Manual capture failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Capture failed: {exc}")


# ── /context/:id ──────────────────────────────────────────────────────────────

@router.get("/context/{capture_id}")
async def context(
    capture_id: str,
    window_minutes: int = Query(default=5, ge=1, le=60),
) -> dict:
    """
    Return all captures within ±window_minutes of the given capture's timestamp.
    This is the temporal context reconstruction endpoint.
    """
    row = metadata_db.fetch_capture_by_id(capture_id)
    if not row:
        logger.warning(f"Context: capture {capture_id[:8]} not found")
        raise HTTPException(status_code=404, detail=f"Capture {capture_id} not found")

    center_ts: str = row["timestamp"]
    nearby = metadata_db.fetch_captures_in_window(center_ts, window_minutes)

    def _fmt(r) -> dict:
        concepts, events = _memory_signals(r["id"])
        return {
            "capture_id": r["id"],
            "source_type": r["source_type"],
            "timestamp": r["timestamp"],
            "content_preview": (r["content"] or "")[:500],
            "thumb_path": r["thumb_path"],
            "window_title": r["window_title"],
            "app_name": r["app_name"],
            "url": r["url"],
            "is_center": r["id"] == capture_id,
            "concepts": concepts,
            "events": events,
        }

    center_index = next(
        (i for i, r in enumerate(nearby) if r["id"] == capture_id), None
    )

    return {
        "capture_id": capture_id,
        "center_timestamp": center_ts,
        "window_minutes": window_minutes,
        "context": [_fmt(r) for r in nearby],
        "center_index": center_index,
    }
