"""
Text chunker.

Splits long text into overlapping windows before embedding so that:
  1. Each chunk fits within the model's token limit (512 tokens).
  2. Overlap between chunks preserves context at boundaries.
  3. Short texts (< chunk_size) are returned as a single chunk without splitting.

Uses a word-based approximation (1 token ≈ 0.75 words) which is accurate
enough for English text without requiring a tokenizer dependency.
"""

from __future__ import annotations

WORDS_PER_TOKEN = 0.75  # approximation: 1 token ≈ 0.75 words


def chunk(
    text: str,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
) -> list[str]:
    """
    Split `text` into overlapping word-window chunks.

    Args:
        text:          Input text to chunk.
        chunk_size:    Maximum tokens per chunk (word approximation used).
        chunk_overlap: Token overlap between consecutive chunks.

    Returns:
        List of non-empty string chunks. Minimum length is 1.
    """
    if not text or not text.strip():
        return []

    max_words = int(chunk_size / WORDS_PER_TOKEN)
    overlap_words = int(chunk_overlap / WORDS_PER_TOKEN)
    step = max(1, max_words - overlap_words)

    words = text.split()

    if len(words) <= max_words:
        return [text.strip()]

    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + max_words, len(words))
        chunk_text = " ".join(words[start:end]).strip()
        if chunk_text:
            chunks.append(chunk_text)
        if end >= len(words):
            break
        start += step

    return chunks or [text.strip()]
