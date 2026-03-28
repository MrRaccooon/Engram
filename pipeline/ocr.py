"""
OCR wrapper using EasyOCR.

EasyOCR significantly outperforms Tesseract on real-world UI screenshots —
dark mode editors, terminal output, mixed-layout pages, and low-contrast text.

The reader is lazily initialised on first use and cached for the process
lifetime to avoid the 2-3s model load cost on every call.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from loguru import logger

_reader = None  # easyocr.Reader — lazy init


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr
        logger.info("Loading EasyOCR model (first-time only)…")
        _reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        logger.info("EasyOCR ready")
    return _reader


def extract_from_image(image_path: str | Path) -> str:
    """
    Run OCR on an image file and return cleaned plain text.

    Returns an empty string if extraction fails or yields nothing useful.
    """
    path = Path(image_path)
    if not path.is_file():
        logger.warning(f"OCR: file not found: {path}")
        return ""

    try:
        reader = _get_reader()
        results = reader.readtext(str(path), detail=0, paragraph=True)
        raw = " ".join(results)
        return _clean(raw)
    except Exception as exc:
        logger.warning(f"OCR failed for {path.name}: {exc}")
        return ""


def extract_from_pil(image) -> str:
    """
    Run OCR on an in-memory PIL Image object.
    Avoids a disk round-trip when the image is already loaded.
    """
    try:
        import numpy as np
        reader = _get_reader()
        arr = np.array(image)
        results = reader.readtext(arr, detail=0, paragraph=True)
        raw = " ".join(results)
        return _clean(raw)
    except Exception as exc:
        logger.warning(f"OCR on PIL image failed: {exc}")
        return ""


def _clean(text: str) -> str:
    """
    Normalise OCR output:
    - Collapse runs of whitespace / newlines into single spaces
    - Strip leading/trailing whitespace
    - Remove lone punctuation artefacts (single chars on a line)
    """
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"(?m)^\s*[^\w\s]{1,2}\s*$", "", text)  # lone punctuation lines
    return text.strip()
