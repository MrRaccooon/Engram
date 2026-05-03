"""
Differential capture analysis.

Compares consecutive screenshot frames to detect WHAT CHANGED between them,
classifying the change into action types (typing, scrolling, app_switch,
new_element, idle). Optionally runs OCR only on the changed regions for
surgical text extraction.

This captures VERBS (what the user did) rather than NOUNS (what was on screen),
producing an event stream that powers activity-based retrieval and richer
consolidation narratives.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from loguru import logger

_DIFF_RESIZE = 512
_PIXEL_THRESHOLD = 30
_RECENT_MAGNITUDES: deque[float] = deque(maxlen=6)


@dataclass
class DiffResult:
    change_magnitude: float = 0.0
    change_type: str = "idle"
    changed_regions: list[tuple[int, int, int, int]] = field(default_factory=list)
    changed_text: str = ""
    is_high_activity: bool = False
    prev_capture_id: Optional[str] = None


def compute_diff(
    prev_frame: np.ndarray,
    curr_frame: np.ndarray,
    full_res_frame: Optional[np.ndarray] = None,
    prev_capture_id: Optional[str] = None,
) -> DiffResult:
    """
    Compare two frames and classify the change.

    prev_frame / curr_frame: numpy arrays (any size, will be resized).
    full_res_frame: optional full-resolution current frame for targeted OCR.
    """
    try:
        import cv2
    except ImportError:
        logger.debug("cv2 not available — diff analysis skipped")
        return DiffResult()

    prev_small = cv2.resize(prev_frame, (_DIFF_RESIZE, _DIFF_RESIZE))
    curr_small = cv2.resize(curr_frame, (_DIFF_RESIZE, _DIFF_RESIZE))

    prev_gray = cv2.cvtColor(prev_small, cv2.COLOR_BGR2GRAY) if len(prev_small.shape) == 3 else prev_small
    curr_gray = cv2.cvtColor(curr_small, cv2.COLOR_BGR2GRAY) if len(curr_small.shape) == 3 else curr_small

    diff = cv2.absdiff(prev_gray, curr_gray)
    _, binary = cv2.threshold(diff, _PIXEL_THRESHOLD, 255, cv2.THRESH_BINARY)

    magnitude = float(np.mean(binary) / 255.0)
    _RECENT_MAGNITUDES.append(magnitude)

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    bboxes = [cv2.boundingRect(c) for c in contours if cv2.contourArea(c) > 50]

    change_type = _classify_change(magnitude, bboxes, _DIFF_RESIZE)

    changed_text = ""
    if change_type in ("typing", "new_element") and full_res_frame is not None:
        changed_text = _ocr_changed_regions(full_res_frame, bboxes, prev_frame.shape, curr_frame.shape)

    result = DiffResult(
        change_magnitude=round(magnitude, 4),
        change_type=change_type,
        changed_regions=bboxes[:20],
        changed_text=changed_text[:2000],
        is_high_activity=change_type in ("typing", "app_switch", "new_element"),
        prev_capture_id=prev_capture_id,
    )

    return result


def _classify_change(
    magnitude: float,
    bboxes: list[tuple[int, int, int, int]],
    frame_size: int,
) -> str:
    if magnitude < 0.02:
        return "idle"
    if magnitude > 0.7:
        return "app_switch"

    if not bboxes:
        return "idle"

    total_bbox_area = sum(w * h for _, _, w, h in bboxes)
    frame_area = frame_size * frame_size

    heights = [h for _, _, _, h in bboxes]
    widths = [w for _, _, w, _ in bboxes]
    max_w = max(widths) if widths else 0
    max_h = max(heights) if heights else 0

    if max_w > frame_size * 0.6 and max_h < frame_size * 0.15:
        return "scrolling"

    if len(bboxes) <= 3 and total_bbox_area / frame_area < 0.15:
        ys = [y for _, y, _, _ in bboxes]
        avg_y = sum(ys) / len(ys)

        xs = [x for x, _, _, _ in bboxes]
        avg_x = sum(xs) / len(xs)
        if avg_y < frame_size * 0.15 or avg_y > frame_size * 0.85:
            if avg_x > frame_size * 0.6:
                return "new_element"
        return "typing"

    if 0.15 <= magnitude <= 0.7:
        if len(bboxes) > 5:
            return "scrolling"
        return "typing"

    return "typing"


def _ocr_changed_regions(
    full_frame: np.ndarray,
    bboxes: list[tuple[int, int, int, int]],
    prev_shape: tuple,
    curr_shape: tuple,
) -> str:
    """Run OCR only on the bounding boxes that changed."""
    if not bboxes:
        return ""

    h_full, w_full = full_frame.shape[:2]
    scale_x = w_full / _DIFF_RESIZE
    scale_y = h_full / _DIFF_RESIZE

    texts: list[str] = []
    try:
        from pipeline.ocr_fast import extract_text as _ocr
        import tempfile
        import os

        try:
            import cv2
        except ImportError:
            return ""

        for bx, by, bw, bh in bboxes[:5]:
            x1 = max(0, int(bx * scale_x) - 10)
            y1 = max(0, int(by * scale_y) - 10)
            x2 = min(w_full, int((bx + bw) * scale_x) + 10)
            y2 = min(h_full, int((by + bh) * scale_y) + 10)

            if (x2 - x1) < 20 or (y2 - y1) < 10:
                continue

            crop = full_frame[y1:y2, x1:x2]

            fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
            try:
                cv2.imwrite(tmp_path, crop)
                t = _ocr(tmp_path)
                if t and t.strip():
                    texts.append(t.strip())
            finally:
                os.close(fd)
                os.unlink(tmp_path)

    except Exception as exc:
        logger.debug(f"Diff OCR failed: {exc}")

    return "\n".join(texts)[:2000]


def get_activity_level() -> str:
    """
    Return the current activity level based on recent diff magnitudes.
    Used by the adaptive capture scheduler.
    """
    if len(_RECENT_MAGNITUDES) == 0:
        return "low"

    avg = sum(_RECENT_MAGNITUDES) / len(_RECENT_MAGNITUDES)
    recent = list(_RECENT_MAGNITUDES)[-3:] if len(_RECENT_MAGNITUDES) >= 3 else list(_RECENT_MAGNITUDES)
    recent_avg = sum(recent) / len(recent)

    if recent_avg > 0.15 or avg > 0.10:
        return "high"
    elif recent_avg > 0.04:
        return "medium"
    return "low"


def to_dict(result: DiffResult) -> dict:
    """Serialize a DiffResult to a JSON-safe dict."""
    return {
        "change_magnitude": result.change_magnitude,
        "change_type": result.change_type,
        "changed_regions": result.changed_regions[:10],
        "changed_text": result.changed_text,
        "is_high_activity": result.is_high_activity,
        "prev_capture_id": result.prev_capture_id,
    }
