"""
Self-evolving CLIP concept vocabulary.

Maintains an in-memory numpy matrix of CLIP text embeddings that represent
visual concepts (e.g. "python code in an editor", "a whatsapp conversation").
Every screenshot's CLIP image vector is dot-producted against this matrix
for zero-shot concept tagging — no VLM, no GPU, sub-millisecond.

The vocabulary starts with ~300 seed concepts and grows automatically via
window-title mining, OCR noun extraction, and (future) cluster discovery.
Concepts that stop matching decay; concepts that prove useful survive.
"""

from __future__ import annotations

import math
import struct
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from loguru import logger

from storage import metadata_db

# ── In-memory concept cache ─────────────────────────────────────────────────

_concept_ids: list[str] = []
_concept_prompts: list[str] = []
_concept_idf: list[float] = []
_concept_word_counts: list[int] = []
_concept_matrix: Optional[np.ndarray] = None  # (N, 512) float32
_initialized = False

_MIN_MATCH_THRESHOLD = 0.15
_TOP_K = 8


# ── Serialisation helpers ───────────────────────────────────────────────────

def _vec_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_vec(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


# ── Initialisation ──────────────────────────────────────────────────────────

def init() -> None:
    """
    Bootstrap seed concepts on first run, then load the in-memory matrix.
    Call once at startup after metadata_db.init().
    """
    global _initialized
    if _initialized:
        return

    count = metadata_db.count_concepts()
    if count == 0:
        _seed_vocabulary()

    _rebuild_cache()
    _initialized = True
    logger.info(
        f"Concept vocabulary ready — {len(_concept_ids)} active concepts loaded"
    )


def _seed_vocabulary() -> None:
    from pipeline.concept_seeds import get_seed_concepts
    from pipeline import embedder

    seeds = get_seed_concepts()
    all_prompts: list[str] = []
    all_categories: list[str] = []

    for category, prompts in seeds.items():
        for p in prompts:
            s = p.strip()
            if s:
                all_prompts.append(s)
                all_categories.append(category)

    logger.info(f"Seeding concept vocabulary with {len(all_prompts)} prompts …")

    vecs = embedder.embed_clip_texts_batch(all_prompts)
    if len(vecs) != len(all_prompts):
        logger.error("CLIP batch embed returned wrong count — seeding aborted")
        return

    for prompt, category, vec in zip(all_prompts, all_categories, vecs):
        blob = _vec_to_blob(vec)
        metadata_db.insert_concept(
            prompt=prompt,
            category=category,
            source="seed",
            clip_embedding=blob,
            status="active",
        )

    logger.info(f"Seeded {len(all_prompts)} concepts")


def _rebuild_cache() -> None:
    """Load active concepts into the numpy matrix."""
    global _concept_ids, _concept_prompts, _concept_idf, _concept_word_counts
    global _concept_matrix

    rows = metadata_db.fetch_active_concepts()
    if not rows:
        _concept_ids = []
        _concept_prompts = []
        _concept_idf = []
        _concept_word_counts = []
        _concept_matrix = None
        return

    ids: list[str] = []
    prompts: list[str] = []
    idfs: list[float] = []
    wcs: list[int] = []
    vecs: list[list[float]] = []

    for r in rows:
        blob = r["clip_embedding"]
        if not blob:
            continue
        ids.append(r["id"])
        prompts.append(r["prompt"])
        idfs.append(float(r["idf_weight"]))
        wcs.append(int(r["word_count"]))
        vecs.append(_blob_to_vec(blob))

    _concept_ids = ids
    _concept_prompts = prompts
    _concept_idf = idfs
    _concept_word_counts = wcs
    _concept_matrix = np.array(vecs, dtype=np.float32) if vecs else None


# ── Core tagging ────────────────────────────────────────────────────────────

def tag_screenshot(
    clip_image_vec: list[float],
) -> list[tuple[str, str, float]]:
    """
    Tag a screenshot using zero-shot CLIP concept matching.

    Returns list of (concept_id, concept_prompt, adjusted_score) sorted
    by score descending, capped at _TOP_K above _MIN_MATCH_THRESHOLD.
    """
    if _concept_matrix is None or len(_concept_ids) == 0:
        return []

    img_vec = np.array(clip_image_vec, dtype=np.float32)
    raw_scores = _concept_matrix @ img_vec  # (N,)

    adjusted = np.empty_like(raw_scores)
    for i in range(len(raw_scores)):
        specificity_bonus = 0.02 * math.log(max(1, _concept_word_counts[i]))
        adjusted[i] = raw_scores[i] * _concept_idf[i] + specificity_bonus

    mask = raw_scores >= _MIN_MATCH_THRESHOLD
    indices = np.where(mask)[0]

    if len(indices) == 0:
        return []

    scored = [(int(idx), float(adjusted[idx])) for idx in indices]
    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:_TOP_K]

    results: list[tuple[str, str, float]] = []
    for idx, score in top:
        cid = _concept_ids[idx]
        prompt = _concept_prompts[idx]
        raw = float(raw_scores[idx])
        metadata_db.record_concept_match(cid, raw)
        results.append((cid, prompt, score))

    return results


def match_query_to_concepts(
    query: str, top_k: int = 5, threshold: float = 0.40,
) -> list[tuple[str, str, float]]:
    """
    Match a text query against concept prompts via MiniLM text embeddings.

    CLIP text-to-text similarity is nearly uniform for English sentences,
    so we use MiniLM (384-dim) for semantic text matching instead.
    Concept prompts are embedded on-the-fly (cheap, ~300 prompts).
    """
    if not _concept_prompts:
        return []

    from pipeline import embedder

    query_vec = embedder.embed_text(query)
    if not query_vec:
        return []

    prompt_vecs = embedder.embed_texts(_concept_prompts)
    if not prompt_vecs:
        return []

    qv = np.array(query_vec, dtype=np.float32)
    pm = np.array(prompt_vecs, dtype=np.float32)
    scores = pm @ qv

    indices = np.where(scores >= threshold)[0]
    if len(indices) == 0:
        return []

    scored = [(int(i), float(scores[i])) for i in indices]
    scored.sort(key=lambda x: x[1], reverse=True)

    return [
        (_concept_ids[i], _concept_prompts[i], s)
        for i, s in scored[:top_k]
    ]


# ── Harvesters ──────────────────────────────────────────────────────────────

def harvest_from_window_titles() -> int:
    """
    Mine recent window titles for new concept candidates.
    Returns the number of new concepts added to probation.
    """
    from pipeline import embedder

    rows = metadata_db.fetch_distinct_window_context(hours=24, limit=300)
    if not rows:
        return 0

    existing_prompts: set[str] = set()
    for r in metadata_db.fetch_active_concepts(limit=10000):
        existing_prompts.add(r["prompt"].strip().lower())
    for r in metadata_db.fetch_probation_concepts(limit=10000):
        existing_prompts.add(r["prompt"].strip().lower())

    templates = [
        "{app} application interface",
        "a {title} screen in {app}",
        "{app} showing {title}",
    ]

    new_prompts: list[tuple[str, str]] = []  # (prompt, category)
    seen_prompts: set[str] = set()

    for row in rows:
        app = (row["app_name"] or "").replace(".exe", "").strip()
        title = (row["window_title"] or "").strip()
        if not app or len(app) < 2:
            continue

        for tpl in templates:
            prompt = tpl.format(app=app, title=title[:60]).strip()
            if prompt.lower() not in existing_prompts and prompt.lower() not in seen_prompts:
                new_prompts.append((prompt, "harvested_window"))
                seen_prompts.add(prompt.lower())

    if not new_prompts:
        return 0

    prompts_only = [p for p, _ in new_prompts]
    vecs = embedder.embed_clip_texts_batch(prompts_only)
    if len(vecs) != len(prompts_only):
        return 0

    added = 0
    for (prompt, cat), vec in zip(new_prompts, vecs):
        blob = _vec_to_blob(vec)
        metadata_db.insert_concept(
            prompt=prompt,
            category=cat,
            source="window_title",
            clip_embedding=blob,
            status="probation",
        )
        added += 1

    if added:
        logger.info(f"Window title harvester: added {added} concepts to probation")
    return added


def harvest_from_ocr_nouns(min_occurrences: int = 5) -> int:
    """
    Extract frequent nouns from recent OCR text and create concept candidates.
    """
    from pipeline import embedder

    rows = metadata_db.fetch_recent_capture_texts(hours=24, limit=800)
    if not rows:
        return 0

    counter: Counter[str] = Counter()
    for r in rows:
        text = r["content"] or ""
        words = text.split()
        for w in words:
            clean = w.strip(".,;:!?\"'()[]{}").strip()
            if len(clean) >= 4 and clean[0].isupper() and clean.isalpha():
                counter[clean] += 1

    existing_prompts: set[str] = set()
    for r in metadata_db.fetch_active_concepts(limit=10000):
        existing_prompts.add(r["prompt"].strip().lower())
    for r in metadata_db.fetch_probation_concepts(limit=10000):
        existing_prompts.add(r["prompt"].strip().lower())

    templates = [
        "a screen showing {noun}",
        "{noun} visible on the display",
    ]

    new_prompts: list[tuple[str, str]] = []
    for noun, count in counter.most_common(100):
        if count < min_occurrences:
            break
        for tpl in templates:
            prompt = tpl.format(noun=noun).strip()
            if prompt.lower() not in existing_prompts:
                new_prompts.append((prompt, "harvested_ocr"))

    if not new_prompts:
        return 0

    prompts_only = [p for p, _ in new_prompts]
    vecs = embedder.embed_clip_texts_batch(prompts_only)
    if len(vecs) != len(prompts_only):
        return 0

    added = 0
    for (prompt, cat), vec in zip(new_prompts, vecs):
        blob = _vec_to_blob(vec)
        metadata_db.insert_concept(
            prompt=prompt,
            category=cat,
            source="ocr_noun",
            clip_embedding=blob,
            status="probation",
        )
        added += 1

    if added:
        logger.info(f"OCR noun harvester: added {added} concepts to probation")
    return added


# ── Lifecycle management ────────────────────────────────────────────────────

def promote_probation_concepts() -> int:
    """Promote probation concepts that have proved useful. Returns count promoted."""
    rows = metadata_db.fetch_probation_concepts()
    promoted = 0
    now = datetime.utcnow()

    for r in rows:
        match_count = int(r["match_count"])
        avg_conf = float(r["avg_confidence"])
        created = r["created_at"] or ""

        if match_count >= 3 and avg_conf >= 0.18:
            metadata_db.promote_concept(r["id"])
            promoted += 1
        elif created:
            try:
                age = now - datetime.fromisoformat(created)
                if age > timedelta(days=7) and match_count < 3:
                    metadata_db.update_concept_status(r["id"], "dormant")
            except (ValueError, TypeError):
                pass

    if promoted:
        logger.info(f"Promoted {promoted} concepts from probation")
    return promoted


def apply_relevance_decay(decay_factor: float = 0.98) -> int:
    """Decay all active concept relevance scores; dormant low-scorers."""
    rows = metadata_db.fetch_active_concepts()
    dormant_count = 0

    for r in rows:
        rel = float(r["relevance_score"])
        new_rel = rel * decay_factor
        if new_rel < 0.05:
            metadata_db.update_concept_status(r["id"], "dormant")
            dormant_count += 1
        else:
            metadata_db.update_concept_relevance(r["id"], new_rel)

    if dormant_count:
        logger.info(f"Relevance decay: {dormant_count} concepts became dormant")
    return dormant_count


def recalculate_idf() -> None:
    """Recompute IDF weights for all active concepts."""
    total_docs = max(1, metadata_db.count_indexed_screenshots())
    rows = metadata_db.fetch_active_concepts()

    for r in rows:
        mc = max(0, int(r["match_count"]))
        idf = math.log(total_docs / (1 + mc))
        metadata_db.update_concept_idf(r["id"], idf)

    logger.debug(f"IDF recalculated for {len(rows)} concepts (total_docs={total_docs})")


def merge_similar_concepts(threshold: float = 0.95) -> int:
    """Merge near-duplicate concepts (cosine > threshold)."""
    if _concept_matrix is None or len(_concept_ids) < 2:
        return 0

    sim = _concept_matrix @ _concept_matrix.T
    merged = 0
    skip: set[int] = set()

    for i in range(len(_concept_ids)):
        if i in skip:
            continue
        for j in range(i + 1, len(_concept_ids)):
            if j in skip:
                continue
            if float(sim[i, j]) > threshold:
                loser = j
                metadata_db.update_concept_status(_concept_ids[loser], "dormant")
                skip.add(loser)
                merged += 1

    if merged:
        logger.info(f"Merged {merged} near-duplicate concepts")
    return merged


def split_broad_concepts(match_ratio_threshold: float = 0.4) -> int:
    """Flag concepts that match too large a fraction of screenshots."""
    total = max(1, metadata_db.count_indexed_screenshots())
    rows = metadata_db.fetch_active_concepts()
    flagged = 0

    for r in rows:
        mc = int(r["match_count"])
        if mc / total > match_ratio_threshold:
            metadata_db.set_concept_needs_split(r["id"], True)
            flagged += 1
            logger.warning(
                f"Concept too broad ({mc}/{total} = {mc/total:.0%}): "
                f"{r['prompt'][:60]}"
            )

    return flagged


def run_harvest_cycle() -> None:
    """Full harvest + promote cycle — called by the scheduler."""
    harvest_from_window_titles()
    harvest_from_ocr_nouns()
    promote_probation_concepts()
    _rebuild_cache()


def run_decay_cycle() -> None:
    """Full decay + IDF + merge cycle — called by the scheduler."""
    apply_relevance_decay()
    recalculate_idf()
    merge_similar_concepts()
    split_broad_concepts()
    _rebuild_cache()
