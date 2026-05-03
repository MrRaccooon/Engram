"""
ChromaDB vector store.

Two collections:
  text_embeddings   — float[384] from sentence-transformers (all-MiniLM-L6-v2)
  visual_embeddings — float[768] from CLIP (ViT-L/14)

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
_insights_col: Optional[chromadb.Collection] = None
_text_recovery_attempted = False
_visual_recovery_attempted = False
_insights_recovery_attempted = False

TEXT_COLLECTION = "text_embeddings"
VISUAL_COLLECTION = "visual_embeddings"
INSIGHTS_COLLECTION = "insights_embeddings"


def init(db_path: Path) -> None:
    """Initialise persistent ChromaDB client and create collections. Call once at startup."""
    global _client, _text_col, _visual_col, _insights_col

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

    _insights_col = _client.get_or_create_collection(
        name=INSIGHTS_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )

    logger.info(
        f"ChromaDB ready at {db_path} | "
        f"text={_text_col.count()} visual={_visual_col.count()} "
        f"insights={_insights_col.count()}"
    )


def _ensure_init() -> None:
    if _client is None:
        raise RuntimeError("Call vector_db.init() before using the vector store")


def _is_recoverable_chroma_error(exc: Exception) -> bool:
    """Return True for disk/index errors where rebuilding the collection is safe."""
    msg = str(exc).lower()
    return (
        "nothing found on disk" in msg
        or "hnsw segment reader" in msg
        or "internal error" in msg
    )


def _recreate_collection(kind: str) -> None:
    """Drop and recreate a collection in-place after on-disk index corruption."""
    global _text_col, _visual_col, _insights_col
    assert _client is not None

    if kind == "text":
        name = TEXT_COLLECTION
        try:
            _client.delete_collection(name=name)
        except Exception:
            pass
        _text_col = _client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )
    elif kind == "visual":
        name = VISUAL_COLLECTION
        try:
            _client.delete_collection(name=name)
        except Exception:
            pass
        _visual_col = _client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )
    else:
        name = INSIGHTS_COLLECTION
        try:
            _client.delete_collection(name=name)
        except Exception:
            pass
        _insights_col = _client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )

    logger.warning(
        f"ChromaDB collection '{name}' was rebuilt after on-disk corruption. "
        f"New captures will re-populate this index."
    )


def _attempt_recovery(kind: str, exc: Exception) -> bool:
    """
    Try one in-process recovery for a corrupted collection.
    Returns True if recovery happened and caller should retry once.
    """
    global _text_recovery_attempted, _visual_recovery_attempted, _insights_recovery_attempted
    if not _is_recoverable_chroma_error(exc):
        return False

    if kind == "text":
        if _text_recovery_attempted:
            return False
        _text_recovery_attempted = True
    elif kind == "visual":
        if _visual_recovery_attempted:
            return False
        _visual_recovery_attempted = True
    else:
        if _insights_recovery_attempted:
            return False
        _insights_recovery_attempted = True

    try:
        _recreate_collection(kind)
        return True
    except Exception as recreate_exc:
        logger.error(f"Failed to rebuild ChromaDB '{kind}' collection: {recreate_exc}")
        return False


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
    payload = {
        "ids": [doc_id],
        "embeddings": [embedding],
        "metadatas": [{
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
    }
    try:
        _text_col.upsert(**payload)
    except Exception as exc:
        if _attempt_recovery("text", exc):
            assert _text_col is not None
            _text_col.upsert(**payload)
        else:
            raise


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
    payload = {
        "ids": [doc_id],
        "embeddings": [embedding],
        "metadatas": [{
            "capture_id": capture_id,
            "timestamp": timestamp,
            "thumb_path": thumb_path,
            "window_title": window_title,
            "app_name": app_name,
        }],
    }
    try:
        _visual_col.upsert(**payload)
    except Exception as exc:
        if _attempt_recovery("visual", exc):
            assert _visual_col is not None
            _visual_col.upsert(**payload)
        else:
            raise


# ── Query ─────────────────────────────────────────────────────────────────────

def query_text(
    embedding: list[float],
    top_k: int = 50,
    where: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Return top-k text results as a list of dicts with id, distance, metadata."""
    _ensure_init()
    assert _text_col is not None
    try:
        count = _text_col.count()
    except Exception as exc:
        if _attempt_recovery("text", exc):
            assert _text_col is not None
            count = _text_col.count()
        else:
            raise
    if count == 0:
        return []
    kwargs: dict[str, Any] = {
        "query_embeddings": [embedding],
        "n_results": min(top_k, count),
        "include": ["metadatas", "distances"],
    }
    if where:
        kwargs["where"] = where
    try:
        results = _text_col.query(**kwargs)
    except Exception as exc:
        if _attempt_recovery("text", exc):
            assert _text_col is not None
            if _text_col.count() == 0:
                return []
            results = _text_col.query(**kwargs)
        else:
            raise
    return _flatten(results)


def query_visual(
    embedding: list[float],
    top_k: int = 50,
    where: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Return top-k visual results as a list of dicts with id, distance, metadata."""
    _ensure_init()
    assert _visual_col is not None
    try:
        count = _visual_col.count()
    except Exception as exc:
        if _attempt_recovery("visual", exc):
            assert _visual_col is not None
            count = _visual_col.count()
        else:
            raise
    if count == 0:
        return []
    kwargs: dict[str, Any] = {
        "query_embeddings": [embedding],
        "n_results": min(top_k, count),
        "include": ["metadatas", "distances"],
    }
    if where:
        kwargs["where"] = where
    try:
        results = _visual_col.query(**kwargs)
    except Exception as exc:
        if _attempt_recovery("visual", exc):
            assert _visual_col is not None
            if _visual_col.count() == 0:
                return []
            results = _visual_col.query(**kwargs)
        else:
            raise
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
    try:
        return _text_col.count()
    except Exception as exc:
        if _attempt_recovery("text", exc):
            assert _text_col is not None
            return _text_col.count()
        raise


def count_visual() -> int:
    _ensure_init()
    assert _visual_col is not None
    try:
        return _visual_col.count()
    except Exception as exc:
        if _attempt_recovery("visual", exc):
            assert _visual_col is not None
            return _visual_col.count()
        raise


def count_insights() -> int:
    _ensure_init()
    assert _insights_col is not None
    try:
        return _insights_col.count()
    except Exception as exc:
        if _attempt_recovery("insights", exc):
            assert _insights_col is not None
            return _insights_col.count()
        raise


def delete_by_capture_ids(capture_ids: list[str]) -> None:
    """Remove all vectors associated with a list of capture IDs."""
    _ensure_init()
    assert _text_col is not None and _visual_col is not None
    for col in (_text_col, _visual_col):
        col.delete(where={"capture_id": {"$in": capture_ids}})


# ── Insights (Phase 2) ────────────────────────────────────────────────────────

def upsert_insight(
    *,
    doc_id: str,
    embedding: list[float],
    insight_id: str,
    date: str,
    summary_preview: str,
    topics: str = "",
) -> None:
    """Upsert a consolidated insight embedding."""
    _ensure_init()
    assert _insights_col is not None
    payload = {
        "ids": [doc_id],
        "embeddings": [embedding],
        "metadatas": [{
            "insight_id": insight_id,
            "date": date,
            "summary_preview": summary_preview[:300],
            "topics": topics,
            "source_type": "insight",
        }],
    }
    try:
        _insights_col.upsert(**payload)
    except Exception as exc:
        if _attempt_recovery("insights", exc):
            assert _insights_col is not None
            _insights_col.upsert(**payload)
        else:
            raise


def query_insights(
    embedding: list[float],
    top_k: int = 5,
    where: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Query the insights collection by embedding similarity."""
    _ensure_init()
    assert _insights_col is not None
    try:
        count = _insights_col.count()
    except Exception as exc:
        if _attempt_recovery("insights", exc):
            assert _insights_col is not None
            count = _insights_col.count()
        else:
            raise
    if count == 0:
        return []
    kwargs: dict[str, Any] = {
        "query_embeddings": [embedding],
        "n_results": min(top_k, count),
        "include": ["metadatas", "distances"],
    }
    if where:
        kwargs["where"] = where
    try:
        results = _insights_col.query(**kwargs)
    except Exception as exc:
        if _attempt_recovery("insights", exc):
            assert _insights_col is not None
            if _insights_col.count() == 0:
                return []
            results = _insights_col.query(**kwargs)
        else:
            raise
    return _flatten(results)


def get_nearest_text_neighbors(
    embedding: list[float],
    top_k: int = 6,
    exclude_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Return the top-k nearest text vectors.
    Used by the graph builder to find related captures.
    """
    _ensure_init()
    assert _text_col is not None
    try:
        count = _text_col.count()
    except Exception as exc:
        if _attempt_recovery("text", exc):
            assert _text_col is not None
            count = _text_col.count()
        else:
            raise
    if count < 2:
        return []
    n = min(top_k + 1, count)  # +1 because the source itself may appear
    try:
        results = _text_col.query(
            query_embeddings=[embedding],
            n_results=n,
            include=["metadatas", "distances"],
        )
    except Exception as exc:
        if _attempt_recovery("text", exc):
            assert _text_col is not None
            if _text_col.count() < 2:
                return []
            results = _text_col.query(
                query_embeddings=[embedding],
                n_results=n,
                include=["metadatas", "distances"],
            )
        else:
            raise
    flat = _flatten(results)
    if exclude_id:
        flat = [r for r in flat if r.get("capture_id") != exclude_id]
    return flat[:top_k]
