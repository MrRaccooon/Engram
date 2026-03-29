"""
Cold-path embedding worker.

Called by the APScheduler every N minutes. Drains the pending job_queue:
  1. OCR  → extract text from screenshot thumbnails
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
