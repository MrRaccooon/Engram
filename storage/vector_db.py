"""
ChromaDB vector store.

Two collections:
  text_embeddings   — float[384] from sentence-transformers (all-MiniLM-L6-v2)
  visual_embeddings — float[512] from CLIP (ViT-B/32)

Both use cosine distance. Metadata stored alongside each vector enables
filtering and result enrichment without a second DB round-trip.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import chromadb
from chromadb.config import Settings
from loguru import logger

_client: Optional[chromadb.ClientAPI] = None
_text_col: Optional[chromadb.Collection] = None
_visual_col: Optional[chromadb.Collection] = None

TEXT_COLLECTION = "text_embeddings"
VISUAL_COLLECTION = "visual_embeddings"


def init(db_path: Path) -> None:
    """Initialise persistent ChromaDB client and create collections. Call once at startup."""
    global _client, _text_col, _visual_col

    db_path.mkdir(parents=True, exist_ok=True)

    _client = chromadb.PersistentClient(
        path=str(db_path),
        settings=Settings(anonymized_telemetry=False),
    )

    _text_col = _client.get_or_create_collection(
        name=TEXT_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )

    _visual_col = _client.get_or_create_collection(
        name=VISUAL_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )

    logger.info(
        f"ChromaDB ready at {db_path} | "
        f"text={_text_col.count()} visual={_visual_col.count()}"
    )


def _ensure_init() -> None:
    if _client is None:
        raise RuntimeError("Call vector_db.init() before using the vector store")


# ── Upsert ────────────────────────────────────────────────────────────────────

def upsert_text(
    *,
    doc_id: str,
    embedding: list[float],
    content_preview: str,
    capture_id: str,
    timestamp: str,
    source_type: str,
    chunk_index: int = 0,
    total_chunks: int = 1,
    window_title: str = "",
    app_name: str = "",
    url: str = "",
) -> None:
    """Upsert a text chunk embedding into the text collection."""
    _ensure_init()
    assert _text_col is not None
    _text_col.upsert(
        ids=[doc_id],
        embeddings=[embedding],
        metadatas=[{
            "capture_id": capture_id,
            "timestamp": timestamp,
            "source_type": source_type,
            "content_preview": content_preview[:300],
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "window_title": window_title,
            "app_name": app_name,
            "url": url,
        }],
    )


def upsert_visual(
    *,
    doc_id: str,
    embedding: list[float],
    capture_id: str,
    timestamp: str,
    thumb_path: str = "",
    window_title: str = "",
    app_name: str = "",
) -> None:
    """Upsert a CLIP image embedding into the visual collection."""
    _ensure_init()
    assert _visual_col is not None
    _visual_col.upsert(
        ids=[doc_id],
        embeddings=[embedding],
        metadatas=[{
            "capture_id": capture_id,
            "timestamp": timestamp,
            "thumb_path": thumb_path,
            "window_title": window_title,
            "app_name": app_name,
        }],
    )


# ── Query ─────────────────────────────────────────────────────────────────────

def query_text(
    embedding: list[float],
    top_k: int = 50,
    where: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Return top-k text results as a list of dicts with id, distance, metadata."""
    _ensure_init()
    assert _text_col is not None
    kwargs: dict[str, Any] = {
        "query_embeddings": [embedding],
        "n_results": min(top_k, max(_text_col.count(), 1)),
        "include": ["metadatas", "distances"],
    }
    if where:
        kwargs["where"] = where
    results = _text_col.query(**kwargs)
    return _flatten(results)


def query_visual(
    embedding: list[float],
    top_k: int = 50,
    where: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Return top-k visual results as a list of dicts with id, distance, metadata."""
    _ensure_init()
    assert _visual_col is not None
    kwargs: dict[str, Any] = {
        "query_embeddings": [embedding],
        "n_results": min(top_k, max(_visual_col.count(), 1)),
        "include": ["metadatas", "distances"],
    }
    if where:
        kwargs["where"] = where
    results = _visual_col.query(**kwargs)
    return _flatten(results)


def _flatten(chroma_result: dict) -> list[dict[str, Any]]:
    """Convert ChromaDB's nested list response into a flat list of result dicts."""
    out = []
    ids = chroma_result.get("ids", [[]])[0]
    distances = chroma_result.get("distances", [[]])[0]
    metadatas = chroma_result.get("metadatas", [[]])[0]
    for doc_id, dist, meta in zip(ids, distances, metadatas):
        out.append({"id": doc_id, "distance": dist, "score": 1 - dist, **meta})
    return out


# ── Stats ─────────────────────────────────────────────────────────────────────

def count_text() -> int:
    _ensure_init()
    assert _text_col is not None
    return _text_col.count()


def count_visual() -> int:
    _ensure_init()
    assert _visual_col is not None
    return _visual_col.count()


def delete_by_capture_ids(capture_ids: list[str]) -> None:
    """Remove all vectors associated with a list of capture IDs."""
    _ensure_init()
    assert _text_col is not None and _visual_col is not None
    for col in (_text_col, _visual_col):
        col.delete(where={"capture_id": {"$in": capture_ids}})
