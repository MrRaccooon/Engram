"""
Semantic knowledge graph storage helpers.

Thin wrappers around the capture_edges and capture_tags tables
defined in metadata_db._SCHEMA_SQL.

The graph is built by the cold-path worker after each capture is indexed:
  - Edges connect semantically similar captures (cosine sim > threshold)
  - Tags are named entities extracted from OCR text by spaCy

These are convenience functions; all SQL lives in metadata_db to keep
a single database connection context.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from storage import metadata_db


SIMILARITY_THRESHOLD = 0.60  # lowered from 0.82 — short metadata strings rarely exceed 0.75


def build_edges_for_capture(
    capture_id: str,
    capture_embedding: list[float],
    top_k: int = 5,
) -> int:
    """
    Find the top-k nearest captures in the text collection and write
    edges to capture_edges for any with similarity >= SIMILARITY_THRESHOLD.

    Returns the number of edges created.
    """
    from storage import vector_db

    neighbors = vector_db.get_nearest_text_neighbors(
        capture_embedding,
        top_k=top_k + 1,
        exclude_id=capture_id,
    )

    edges_created = 0
    for neighbor in neighbors:
        sim = neighbor.get("score", 0)
        if sim < SIMILARITY_THRESHOLD:
            continue
        neighbor_capture_id = neighbor.get("capture_id", "")
        if not neighbor_capture_id or neighbor_capture_id == capture_id:
            continue

        metadata_db.upsert_edge(
            source_id=capture_id,
            target_id=neighbor_capture_id,
            similarity=sim,
            edge_type="semantic",
        )
        edges_created += 1

    return edges_created


def get_related(capture_id: str, limit: int = 5) -> list[dict[str, Any]]:
    """
    Return related captures for a given capture_id, ordered by similarity.
    Each result is a dict with capture fields + similarity + edge_type.
    """
    rows = metadata_db.fetch_related_captures(capture_id, limit=limit)
    return [dict(row) for row in rows]


def upsert_tags(capture_id: str, tags: list[tuple[str, str]]) -> None:
    """Write NER tags for a capture (delegated to metadata_db)."""
    metadata_db.upsert_tags(capture_id, tags)


def get_by_tag(tag: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return captures associated with a given tag string."""
    rows = metadata_db.fetch_captures_by_tag(tag, limit=limit)
    return [dict(row) for row in rows]
