"""
Fast OCR via RapidOCR (PaddleOCR ONNX backend).

Processes a 1024px screenshot thumbnail in ~0.2-0.5s on CPU —
roughly 10-30x faster than EasyOCR for equivalent accuracy.

The engine is lazily loaded on first call and cached for the process lifetime.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger

_engine = None


def extract_text(image_path: str | Path) -> Optional[str]:
    """
    Run OCR on an image file and return all detected text as a single string.
    Returns None if no text is detected or the file cannot be read.
    """
    global _engine

    path = Path(image_path)
    if not path.is_file():
        return None

    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        logger.info("Loading RapidOCR engine…")
        _engine = RapidOCR()
        logger.info("RapidOCR ready")

    try:
        result, _ = _engine(str(path))
        if not result:
            return None
        lines = [line[1] for line in result if line[1]]
        text = " ".join(lines)
        return text if text.strip() else None
    except Exception as exc:
        logger.warning(f"RapidOCR failed for {path.name}: {exc}")
        return None
