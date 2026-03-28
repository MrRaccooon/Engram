"""
Cross-encoder reranker.

After dual vector search returns up to 50 candidates from ChromaDB,
the reranker scores each (query, passage) pair using a cross-encoder
model that attends to both simultaneously — giving much higher precision
than cosine similarity alone.

Model: cross-encoder/ms-marco-MiniLM-L-6-v2
  - ~85MB, CPU-capable, < 500ms for 50 candidates on a modern CPU
  - Trained on MS MARCO passage ranking — generalises well to any text pairs

Outputs are raw logit scores (higher = more relevant). We sort descending
and return the top-N results with scores normalised to [0, 1] via sigmoid.
"""

from __future__ import annotations

import math
from typing import Any

from loguru import logger

_reranker = None
_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"


def _get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder
        logger.info(f"Loading reranker model: {_MODEL_NAME}…")
        _reranker = CrossEncoder(_MODEL_NAME, max_length=512, device="cpu")
        logger.info("Reranker ready")
    return _reranker


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def rerank(
    query: str,
    candidates: list[dict[str, Any]],
    top_n: int = 10,
    text_field: str = "content_preview",
) -> list[dict[str, Any]]:
    """
    Rerank `candidates` (dicts with at least a `text_field` key) by relevance
    to `query` using the cross-encoder model.

    Args:
        query:      The user's search query.
        candidates: List of result dicts from vector_db.query_text / query_visual.
        top_n:      Number of results to return after reranking.
        text_field: Key in each candidate dict containing the passage text.

    Returns:
        Top-N candidate dicts sorted by relevance, each augmented with a
        `rerank_score` float in [0, 1].
    """
    if not candidates:
        return []

    reranker = _get_reranker()

    pairs = [(query, c.get(text_field, "") or "") for c in candidates]

    try:
        raw_scores = reranker.predict(pairs, show_progress_bar=False)
    except Exception as exc:
        logger.warning(f"Reranker failed, falling back to vector scores: {exc}")
        return sorted(candidates, key=lambda c: c.get("score", 0), reverse=True)[:top_n]

    scored = [
        {**c, "rerank_score": _sigmoid(float(s))}
        for c, s in zip(candidates, raw_scores)
    ]
    scored.sort(key=lambda c: c["rerank_score"], reverse=True)
    return scored[:top_n]
