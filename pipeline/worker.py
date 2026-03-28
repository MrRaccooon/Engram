"""
Cold-path embedding worker.

Called by the APScheduler every N minutes. Drains the pending job_queue:
  1. OCR  → extract text from screenshot thumbnails
  2. Chunk → split long text into overlapping 512-token windows
  3. Embed → text vectors (sentence-transformers) + visual vectors (CLIP)
  4. Upsert → write vectors + metadata into ChromaDB
  5. Status → mark capture as 'indexed' or 'error' in SQLite

Designed to be safe to call concurrently (APScheduler max_instances=1
prevents overlap, but the function itself is also idempotent).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from loguru import logger

from pipeline import chunker, embedder, ocr
from storage import metadata_db, vector_db

_BATCH_SIZE = 16  # captures processed per worker invocation


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

    # ── 1. Extract text ───────────────────────────────────────────────────────
    text = content

    if source_type == "screenshot" and thumb_path:
        ocr_text = ocr.extract_from_image(thumb_path)
        if ocr_text:
            text = ocr_text
        elif not text:
            logger.debug(f"OCR yielded nothing for {capture_id[:8]}, skipping text embed")

    if not text.strip() and source_type != "screenshot":
        logger.debug(f"No text content for {capture_id[:8]} ({source_type}), skipping text embed")

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
