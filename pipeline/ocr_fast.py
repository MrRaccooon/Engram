"""
Screenshot OCR with preprocessing and spatial text assembly.

Preprocesses screenshots for OCR accuracy (contrast boost, dark theme
inversion, text region upscaling), then uses RapidOCR (PaddleOCR ONNX)
with bounding-box-aware text assembly that preserves line structure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
from loguru import logger
from PIL import Image, ImageEnhance, ImageFilter

_engine = None


def _load_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        logger.info("Loading RapidOCR engine...")
        _engine = RapidOCR()
        logger.info("RapidOCR ready")
    return _engine


def _is_dark_theme(img: Image.Image, sample_size: int = 1000) -> bool:
    """Detect if the screenshot has a dark theme by sampling pixel brightness."""
    arr = np.array(img)
    h, w = arr.shape[:2]
    ys = np.random.randint(0, h, sample_size)
    xs = np.random.randint(0, w, sample_size)
    samples = arr[ys, xs]
    if len(samples.shape) == 2:
        brightness = samples.mean()
    else:
        brightness = samples.mean(axis=1).mean()
    return brightness < 100


def _preprocess(img: Image.Image) -> Image.Image:
    """
    Preprocess a screenshot for better OCR accuracy.
    - Detect and invert dark themes
    - Boost contrast and sharpness
    - Convert to grayscale for cleaner text extraction
    """
    if img.mode != "RGB":
        img = img.convert("RGB")

    if _is_dark_theme(img):
        from PIL import ImageOps
        img = ImageOps.invert(img)

    img = ImageEnhance.Contrast(img).enhance(1.5)
    img = ImageEnhance.Sharpness(img).enhance(1.8)

    gray = img.convert("L")
    return gray.convert("RGB")


def _assemble_lines(result: list) -> str:
    """
    Assemble OCR results into properly structured text using bounding boxes.

    RapidOCR returns [(bbox, text, confidence), ...] where bbox is
    [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]. We use y-coordinates to group
    text into lines and x-coordinates for ordering within lines.
    """
    if not result:
        return ""

    entries = []
    for item in result:
        bbox = item[0]
        text = item[1]
        if not text or not text.strip():
            continue
        y_center = (bbox[0][1] + bbox[2][1]) / 2
        x_left = bbox[0][0]
        entries.append((y_center, x_left, text.strip()))

    if not entries:
        return ""

    entries.sort(key=lambda e: (e[0], e[1]))

    lines: list[list[tuple[float, str]]] = []
    current_line: list[tuple[float, str]] = []
    current_y = entries[0][0]
    line_height = 15

    for y, x, text in entries:
        if abs(y - current_y) > line_height:
            if current_line:
                lines.append(current_line)
            current_line = [(x, text)]
            current_y = y
        else:
            current_line.append((x, text))

    if current_line:
        lines.append(current_line)

    text_lines = []
    for line in lines:
        line.sort(key=lambda e: e[0])
        text_lines.append(" ".join(t for _, t in line))

    return "\n".join(text_lines)


def extract_text(image_path: str | Path) -> Optional[str]:
    """
    Run OCR on a screenshot with preprocessing and spatial text assembly.
    Returns structured text preserving line layout, or None if no text found.
    """
    path = Path(image_path)
    if not path.is_file():
        return None

    engine = _load_engine()

    try:
        img = Image.open(path)
        processed = _preprocess(img)

        import tempfile
        import os
        fd, tmp = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            processed.save(tmp)
            result, _ = engine(tmp)
        finally:
            os.unlink(tmp)

        if not result:
            return None

        text = _assemble_lines(result)
        return text if text.strip() else None

    except Exception as exc:
        logger.warning(f"OCR failed for {path.name}: {exc}")
        return None


def extract_text_from_region(
    image_path: str | Path,
    x1: int, y1: int, x2: int, y2: int,
) -> Optional[str]:
    """OCR a specific region of an image. Used by diff analyzer for targeted extraction."""
    path = Path(image_path)
    if not path.is_file():
        return None

    try:
        img = Image.open(path)
        crop = img.crop((x1, y1, x2, y2))

        if crop.width < 30 or crop.height < 15:
            return None

        crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
        processed = _preprocess(crop)

        engine = _load_engine()

        import tempfile
        import os
        fd, tmp = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            processed.save(tmp)
            result, _ = engine(tmp)
        finally:
            os.unlink(tmp)

        if not result:
            return None

        text = _assemble_lines(result)
        return text if text.strip() else None

    except Exception as exc:
        logger.debug(f"Region OCR failed: {exc}")
        return None


def detect_panels(image_path: str | Path) -> list[dict]:
    """
    Detect distinct content panels in a screenshot using OCR bounding boxes.
    Returns a list of panel regions with their content type hints.
    """
    path = Path(image_path)
    if not path.is_file():
        return []

    engine = _load_engine()
    try:
        result, _ = engine(str(path))
        if not result:
            return []

        bboxes = []
        for item in result:
            bbox = item[0]
            y_center = (bbox[0][1] + bbox[2][1]) / 2
            x_center = (bbox[0][0] + bbox[2][0]) / 2
            bboxes.append((x_center, y_center, item[1]))

        if not bboxes:
            return []

        img = Image.open(path)
        w, h = img.size

        ys = [b[1] for b in bboxes]

        panels = []
        if ys:
            gap_threshold = h * 0.05
            sorted_ys = sorted(set(int(y / gap_threshold) for y in ys))

            if len(sorted_ys) > 1:
                mid_y = h * 0.65
                top_texts = [t for _, y, t in bboxes if y < mid_y]
                bottom_texts = [t for _, y, t in bboxes if y >= mid_y]

                if top_texts and bottom_texts:
                    panels.append({
                        "region": "top",
                        "y_start": 0, "y_end": int(mid_y),
                        "text_sample": " ".join(top_texts[:5])[:200],
                    })
                    panels.append({
                        "region": "bottom",
                        "y_start": int(mid_y), "y_end": h,
                        "text_sample": " ".join(bottom_texts[:5])[:200],
                    })

        return panels

    except Exception as exc:
        logger.debug(f"Panel detection failed: {exc}")
        return []
