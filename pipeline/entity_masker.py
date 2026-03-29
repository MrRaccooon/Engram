"""
PII entity masker using spaCy NER.

Replaces named entities (PERSON, ORG, MONEY, GPE, etc.) in text with
consistent numbered placeholders before sending to any external API.
A local lookup table maps placeholders back to real values so they can
be restored in the API's response.

Model: en_core_web_sm — 12 MB, fully offline, CPU-only.

Usage:
    masked_text, entity_map = mask("John works at Stripe for $200k")
    # masked_text  → "[PERSON_1] works at [ORG_1] for [MONEY_1]"
    # entity_map   → {"[PERSON_1]": "John", "[ORG_1]": "Stripe", ...}

    response = call_api(masked_text)
    final    = unmask(response, entity_map)
    # final → "John works at Stripe for $200k"

The masker is also used for NER tag extraction during the cold-path
worker (Phase 3): extract_tags() returns a flat list of (tag, type) pairs
without modifying the text.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

_nlp = None

# Entity labels that are considered identifiable / sensitive
_MASK_LABELS = {
    "PERSON", "ORG", "GPE", "LOC", "MONEY", "CARDINAL",
    "NORP", "FAC", "PRODUCT", "EVENT", "WORK_OF_ART",
}

# Labels to keep as searchable tags (NER phase 3 tagging)
_TAG_LABELS = {
    "PERSON": "PERSON",
    "ORG": "ORG",
    "GPE": "TOPIC",
    "LOC": "TOPIC",
    "PRODUCT": "TECH",
    "WORK_OF_ART": "TOPIC",
    "NORP": "TOPIC",
}


def _get_nlp():
    global _nlp
    if _nlp is None:
        try:
            import spacy
            logger.info("Loading spaCy en_core_web_sm…")
            _nlp = spacy.load("en_core_web_sm")
            logger.info("spaCy model ready")
        except OSError:
            logger.warning(
                "spaCy model 'en_core_web_sm' not found. "
                "Run: python -m spacy download en_core_web_sm"
            )
            _nlp = None
    return _nlp


def mask(text: str) -> tuple[str, dict[str, str]]:
    """
    Replace named entities in text with numbered placeholders.

    Returns:
        (masked_text, entity_map)
        entity_map maps "[LABEL_N]" → original entity string.
        If spaCy is unavailable, returns (text, {}) unchanged.
    """
    nlp = _get_nlp()
    if not nlp or not text.strip():
        return text, {}

    try:
        doc = nlp(text)
    except Exception as exc:
        logger.warning(f"Entity masking failed: {exc}")
        return text, {}

    entity_map: dict[str, str] = {}
    counters: dict[str, int] = {}
    # We need a reverse map to reuse the same placeholder for the same entity value
    value_to_placeholder: dict[str, str] = {}

    # Collect entities in order (we'll replace from right to left to preserve offsets)
    ents = [ent for ent in doc.ents if ent.label_ in _MASK_LABELS]

    masked = text
    # Process right to left to keep character offsets valid
    for ent in reversed(ents):
        original = ent.text
        # Reuse placeholder if we've seen the same string before
        if original in value_to_placeholder:
            placeholder = value_to_placeholder[original]
        else:
            label = ent.label_
            counters[label] = counters.get(label, 0) + 1
            placeholder = f"[{label}_{counters[label]}]"
            entity_map[placeholder] = original
            value_to_placeholder[original] = placeholder

        masked = masked[: ent.start_char] + placeholder + masked[ent.end_char :]

    return masked, entity_map


def mask_chunks(
    chunks: list[dict[str, Any]],
    field: str = "content_preview",
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """
    Mask entities across a list of chunk dicts, accumulating a single shared
    entity map (so the same person is [PERSON_1] in all chunks).

    Returns:
        (masked_chunks, combined_entity_map)
    """
    combined_map: dict[str, str] = {}
    value_to_placeholder: dict[str, str] = {}
    masked_chunks = []

    nlp = _get_nlp()

    for chunk in chunks:
        text = chunk.get(field, "") or ""
        if not nlp or not text.strip():
            masked_chunks.append(chunk)
            continue

        try:
            doc = nlp(text)
        except Exception as exc:
            logger.warning(f"Entity masking skipped for chunk: {exc}")
            masked_chunks.append(chunk)
            continue

        ents = [ent for ent in doc.ents if ent.label_ in _MASK_LABELS]
        masked = text
        counters: dict[str, int] = {
            label: sum(1 for k in combined_map if k.startswith(f"[{label}_"))
            for label in _MASK_LABELS
        }

        for ent in reversed(ents):
            original = ent.text
            if original in value_to_placeholder:
                placeholder = value_to_placeholder[original]
            else:
                label = ent.label_
                counters[label] = counters.get(label, 0) + 1
                placeholder = f"[{label}_{counters[label]}]"
                combined_map[placeholder] = original
                value_to_placeholder[original] = placeholder

            masked = masked[: ent.start_char] + placeholder + masked[ent.end_char :]

        masked_chunks.append({**chunk, field: masked})

    return masked_chunks, combined_map


def unmask(text: str, entity_map: dict[str, str]) -> str:
    """
    Restore real entity values in the API's response text.

    Replaces placeholders like [PERSON_1] with their original values.
    """
    if not entity_map:
        return text
    for placeholder, original in entity_map.items():
        text = text.replace(placeholder, original)
    return text


def extract_tags(text: str) -> list[tuple[str, str]]:
    """
    Extract named entity tags from text without modifying it.

    Returns list of (entity_text, tag_type) pairs, e.g.:
        [("OpenAI", "ORG"), ("Python", "TECH"), ("India", "TOPIC")]

    Used by the cold-path worker to populate capture_tags table (Phase 3).
    """
    nlp = _get_nlp()
    if not nlp or not text.strip():
        return []

    try:
        doc = nlp(text)
    except Exception as exc:
        logger.warning(f"Tag extraction failed: {exc}")
        return []

    tags: list[tuple[str, str]] = []
    seen = set()

    for ent in doc.ents:
        tag_type = _TAG_LABELS.get(ent.label_)
        if tag_type and ent.text not in seen:
            tags.append((ent.text.strip(), tag_type))
            seen.add(ent.text)

    return tags
