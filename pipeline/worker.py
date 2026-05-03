"""
Cold-path embedding worker.

Called by the APScheduler every N minutes. Drains the pending job_queue:
  1. Build searchable text from metadata (window_title + app_name) + any content
  2. Chunk → split long text into overlapping 512-token windows
  3. Embed → text vectors (sentence-transformers) + visual vectors (CLIP)
  4. Upsert → write vectors + metadata into ChromaDB
  5. NER tags → extract entities and write to capture_tags (Phase 3)
  6. Graph edges → find nearest neighbors and write to capture_edges (Phase 3)
  7. Status → mark capture as 'indexed' or 'error' in SQLite

Designed to be safe to call concurrently (APScheduler max_instances=1
prevents overlap, but the function itself is also idempotent).
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from loguru import logger

from pipeline import chunker, embedder
from storage import metadata_db, vector_db

_BATCH_SIZE = 64  # captures processed per worker invocation

# ── OCR post-processing helpers ───────────────────────────────────────────────

# Patterns for Python/Node/terminal errors visible in screenshots
_ERROR_PATTERNS = [
    # Python exceptions: "TypeError: ...", "ValueError: ..."
    re.compile(r"\b([A-Z][a-zA-Z]+Error|[A-Z][a-zA-Z]+Exception|Traceback)\b[^\n]*", re.IGNORECASE),
    # "Error:" generic prefix
    re.compile(r"\bError:\s+[^\n]{5,120}"),
    # npm/node errors
    re.compile(r"\bERR![^\n]{5,100}"),
    # File path + line reference (common in tracebacks)
    re.compile(r'File "[^"]+", line \d+'),
    # Command failed / exit code
    re.compile(r"(?:exit code|exited with|failed with|returned non-zero)[^\n]{0,60}", re.IGNORECASE),
    # HTTP error codes
    re.compile(r"\b[45]\d{2}\s+(?:Error|Not Found|Internal Server Error|Bad Request)[^\n]{0,60}", re.IGNORECASE),
]


def _extract_errors_from_ocr(ocr_text: str) -> str:
    """
    Scan OCR text for error patterns and prepend them as structured
    high-priority content. The raw OCR text is still included so
    non-error content isn't lost.
    """
    error_hits: list[str] = []
    for pattern in _ERROR_PATTERNS:
        for match in pattern.findall(ocr_text):
            hit = match.strip() if isinstance(match, str) else " ".join(match).strip()
            if hit and len(hit) > 8:
                error_hits.append(hit)

    if error_hits:
        # Deduplicate preserving order
        seen: set[str] = set()
        unique = []
        for h in error_hits:
            if h not in seen:
                seen.add(h)
                unique.append(h)
        error_block = "ERRORS VISIBLE: " + " | ".join(unique[:6])
        return f"{error_block}\n{ocr_text}"

    return ocr_text


def process_batch(batch_size: int = _BATCH_SIZE) -> int:
    """
    Process up to `batch_size` pending captures from the job_queue.

    Returns the number of captures successfully indexed.
    """
    pending = metadata_db.fetch_pending_jobs(limit=batch_size)
    if not pending:
        return 0

    logger.info(f"Worker: processing {len(pending)} pending captures…")
    indexed = 0

    for row in pending:
        capture_id: str = row["id"]
        source_type: str = row["source_type"]
        metadata_db.increment_attempts(capture_id)

        try:
            _process_capture(row)
            metadata_db.update_capture_status(capture_id, "indexed")
            indexed += 1
        except Exception as exc:
            logger.error(f"Failed to index {capture_id[:8]} ({source_type}): {exc}")
            metadata_db.update_capture_status(capture_id, "error", error=str(exc))

    logger.info(f"Worker: indexed {indexed}/{len(pending)} captures")
    return indexed


def _process_capture(row) -> None:
    """Full embedding pipeline for a single capture row."""
    capture_id: str = row["id"]
    source_type: str = row["source_type"]
    timestamp: str = row["timestamp"]
    thumb_path: str = row["thumb_path"] or ""
    content: str = row["content"] or ""
    window_title: str = row["window_title"] or ""
    app_name: str = row["app_name"] or ""
    url: str = row["url"] or ""

    # ── 0b. Differential capture event ──────────────────────────────────────
    diff_data_raw: str = row["diff_data"] or "" if "diff_data" in row.keys() else ""
    _diff_parsed: dict | None = None
    if diff_data_raw:
        try:
            import json as _json
            _diff_parsed = _json.loads(diff_data_raw)
            metadata_db.insert_capture_event(
                capture_id=capture_id,
                prev_capture_id=_diff_parsed.get("prev_capture_id"),
                change_type=_diff_parsed.get("change_type", "unknown"),
                change_magnitude=float(_diff_parsed.get("change_magnitude", 0)),
                changed_text=_diff_parsed.get("changed_text", ""),
                window_title=window_title,
                app_name=app_name,
                timestamp=timestamp,
            )
        except Exception as exc:
            logger.debug(f"Diff event skipped for {capture_id[:8]}: {exc}")

    # ── 1. Extract text ───────────────────────────────────────────────────────
    text = content

    if _diff_parsed:
        changed = _diff_parsed.get("changed_text", "")
        if changed and changed.strip():
            text = f"{text}\nCHANGED: {changed.strip()}" if text.strip() else f"CHANGED: {changed.strip()}"
    _diff_parsed = None

    if source_type == "screenshot":
        # Parse window title into structured context for richer searchable text
        try:
            from pipeline.context_parser import parse_window
            ctx = parse_window(window_title, app_name)
            rich_text = ctx.get("rich_text", "")
        except Exception as exc:
            logger.debug(f"context_parser skipped for {capture_id[:8]}: {exc}")
            ctx = {}
            rich_text = ""

        # Build meta text: rich parsed description + fallback raw fields
        meta_parts = []
        if rich_text:
            meta_parts.append(rich_text)
        elif app_name:
            clean_app = app_name.replace(".exe", "")
            meta_parts.append(clean_app)
            if window_title:
                meta_parts.append(window_title)

        # Add project / file as standalone keywords for better recall
        if ctx.get("file"):
            meta_parts.append(f"file:{ctx['file']}")
        if ctx.get("project"):
            meta_parts.append(f"project:{ctx['project']}")
        if ctx.get("question"):
            meta_parts.append(ctx["question"])

        meta_text = " | ".join(meta_parts) if meta_parts else ""

        if not text.strip():
            text = meta_text
        elif meta_text:
            text = f"{meta_text}\n{text}"

        # RapidOCR + screenshot analyzer: read the screenshot as a document
        if thumb_path and Path(thumb_path).is_file():
            try:
                from pipeline.ocr_fast import extract_text as _ocr
                from pipeline.screenshot_analyzer import analyze, to_searchable_text

                ocr_text = _ocr(thumb_path) or ""
                if ocr_text.strip():
                    # Analyze what type of content is visible and extract structure
                    screen_ctx = analyze(ocr_text, window_title=window_title, app_name=app_name)
                    enriched = to_searchable_text(screen_ctx, raw_ocr=ocr_text)
                    text = f"{text}\n{enriched}" if text.strip() else enriched
                    if screen_ctx.summary:
                        logger.debug(f"Screen analysis [{capture_id[:8]}]: {screen_ctx.content_type} — {screen_ctx.summary[:80]}")
            except Exception as exc:
                logger.debug(f"Screenshot analysis skipped for {capture_id[:8]}: {exc}")

    if not text.strip() and source_type != "screenshot":
        logger.debug(f"No text content for {capture_id[:8]} ({source_type}), skipping text embed")

    # ── 1b. Write enriched text back to SQLite so timeline/detail view works ─
    if source_type == "screenshot" and text.strip():
        try:
            with metadata_db._connect() as conn:
                conn.execute(
                    "UPDATE captures SET content = ? WHERE id = ?",
                    (text[:4000], capture_id),
                )
        except Exception as exc:
            logger.debug(f"Content writeback skipped for {capture_id[:8]}: {exc}")

    # ── 2. Chunk + text embed ─────────────────────────────────────────────────
    if text.strip():
        chunks = chunker.chunk(text)
        chunk_vectors = embedder.embed_texts(chunks)

        for idx, (chunk_text, vec) in enumerate(zip(chunks, chunk_vectors)):
            doc_id = f"{capture_id}_t{idx}"
            vector_db.upsert_text(
                doc_id=doc_id,
                embedding=vec,
                content_preview=chunk_text[:300],
                capture_id=capture_id,
                timestamp=timestamp,
                source_type=source_type,
                chunk_index=idx,
                total_chunks=len(chunks),
                window_title=window_title,
                app_name=app_name,
                url=url,
            )

    # ── 3. Visual (CLIP) embed for screenshots ────────────────────────────────
    visual_vec = None
    if source_type == "screenshot" and thumb_path and Path(thumb_path).is_file():
        visual_vec = embedder.embed_image_path(thumb_path)
        if visual_vec:
            doc_id = f"{capture_id}_v0"
            vector_db.upsert_visual(
                doc_id=doc_id,
                embedding=visual_vec,
                capture_id=capture_id,
                timestamp=timestamp,
                thumb_path=thumb_path,
                window_title=window_title,
                app_name=app_name,
            )

    # ── 3b. Concept tagging via CLIP zero-shot vocabulary ──────────────────
    if source_type == "screenshot" and visual_vec:
        try:
            from pipeline.concept_vocabulary import tag_screenshot
            concept_tags = tag_screenshot(visual_vec)
            if concept_tags:
                metadata_db.insert_capture_concepts(
                    capture_id,
                    [(cid, conf) for cid, _, conf in concept_tags],
                )
                logger.debug(
                    f"Concepts [{capture_id[:8]}]: "
                    + ", ".join(p[:30] for _, p, _ in concept_tags[:4])
                )
        except Exception as exc:
            logger.debug(f"Concept tagging skipped for {capture_id[:8]}: {exc}")

    # ── 4. NER tagging (Phase 3) ──────────────────────────────────────────────
    if text.strip():
        try:
            from pipeline.entity_masker import extract_tags
            from storage.graph_db import upsert_tags
            tags = extract_tags(text)
            if tags:
                upsert_tags(capture_id, tags)
                logger.debug(f"Worker: {capture_id[:8]} → {len(tags)} tag(s)")
        except Exception as exc:
            logger.debug(f"NER tagging skipped for {capture_id[:8]}: {exc}")

    # ── 5. Semantic graph edges (Phase 3) ────────────────────────────────────
    # Use the first text chunk's vector to find related captures
    if text.strip():
        try:
            from storage.graph_db import build_edges_for_capture
            first_vec = embedder.embed_text(text[:512])
            if first_vec:
                n_edges = build_edges_for_capture(capture_id, first_vec, top_k=5)
                if n_edges:
                    logger.debug(f"Worker: {capture_id[:8]} → {n_edges} graph edge(s)")
        except Exception as exc:
            logger.debug(f"Graph edge building skipped for {capture_id[:8]}: {exc}")
