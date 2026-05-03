"""
Screenshot collector.

Captures the full screen using mss (cross-platform), generates a perceptual
hash to deduplicate near-identical frames, saves a thumbnail, and enqueues
the capture for embedding via the hot-path queue_manager.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import imagehash
import mss
import numpy as np
from loguru import logger
from PIL import Image

from pipeline import queue_manager
from collectors.window_context import get_active_window

_last_phash: Optional[imagehash.ImageHash] = None
_prev_frame: Optional[np.ndarray] = None
_prev_capture_id: Optional[str] = None
_SIMILARITY_THRESHOLD = 5  # hamming distance; lower = stricter dedupe


def capture(storage_root: Path, thumbnail_size: int = 1024) -> Optional[str]:
    """
    Take a screenshot, deduplicate, save thumbnail, enqueue for embedding.

    Returns the capture UUID if a new capture was stored, else None.
    """
    global _last_phash

    try:
        with mss.mss() as sct:
            # monitors[0] is the virtual screen spanning all monitors
            shot = sct.grab(sct.monitors[0])
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    except Exception as exc:
        logger.warning(f"Screenshot failed: {exc}")
        return None

    # Perceptual hash for deduplication
    current_phash = imagehash.phash(img)
    if _last_phash is not None:
        diff = current_phash - _last_phash
        if diff <= _SIMILARITY_THRESHOLD:
            logger.debug(f"Screenshot skipped — phash diff={diff} (duplicate)")
            return None
    _last_phash = current_phash

    # Derive paths
    ts = datetime.utcnow()
    ts_str = ts.strftime("%Y%m%d_%H%M%S")
    thumb_dir = storage_root / "thumbnails" / "screenshots"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = thumb_dir / f"ss_{ts_str}.jpg"

    # Save thumbnail (JPEG for storage efficiency)
    thumb = img.copy()
    thumb.thumbnail((thumbnail_size, thumbnail_size), Image.LANCZOS)
    thumb.save(thumb_path, format="JPEG", quality=75, optimize=True)

    # Optionally keep raw (caller decides via config; we always save thumb)
    raw_path: Optional[str] = None

    # Active window context
    window_title, app_name = get_active_window()

    # Differential analysis against previous frame
    diff_data_str: Optional[str] = None
    curr_arr = np.array(img)

    if _prev_frame is not None:
        try:
            from pipeline.diff_analyzer import compute_diff, to_dict
            diff_result = compute_diff(
                _prev_frame, curr_arr,
                full_res_frame=curr_arr,
                prev_capture_id=_prev_capture_id,
            )
            if diff_result.change_type != "idle":
                import json as _json
                diff_data_str = _json.dumps(to_dict(diff_result))
        except Exception as exc:
            logger.debug(f"Diff analysis skipped: {exc}")

    capture_id = queue_manager.enqueue(
        source_type="screenshot",
        timestamp=ts,
        thumb_path=str(thumb_path),
        raw_path=raw_path,
        phash=str(current_phash),
        window_title=window_title,
        app_name=app_name,
        diff_data=diff_data_str,
    )

    _update_prev_frame(curr_arr, capture_id)

    logger.debug(f"Screenshot captured → {thumb_path.name} ({capture_id[:8]})")
    return capture_id


def _update_prev_frame(frame: np.ndarray, capture_id: str) -> None:
    global _prev_frame, _prev_capture_id
    _prev_frame = frame
    _prev_capture_id = capture_id
