"""
Dual embedding engine.

Text embedder : sentence-transformers (all-MiniLM-L6-v2)
                → float[384], CPU-capable, ~80MB

Visual embedder: open-clip-torch (ViT-B/32 / openai pretrained)
                → float[512], CPU-capable, ~350MB

Both models are lazily loaded and cached for the process lifetime.
Batch processing is used to amortise per-call overhead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import torch
from loguru import logger

_text_model = None      # SentenceTransformer
_clip_model = None      # open_clip model
_clip_preprocess = None # open_clip image transform
_clip_tokenizer = None  # open_clip tokenizer
_clip_broken = False    # set True if CLIP init fails — skip all subsequent calls

_TEXT_MODEL_NAME = "all-MiniLM-L6-v2"
_CLIP_MODEL_NAME = "ViT-B-32"
_CLIP_PRETRAINED = "openai"


# ── Text embedding ─────────────────────────────────────────────────────────────

def _get_text_model():
    global _text_model
    if _text_model is None:
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading text embedding model: {_TEXT_MODEL_NAME}…")
        _text_model = SentenceTransformer(_TEXT_MODEL_NAME, device="cpu")
        logger.info("Text embedding model ready")
    return _text_model


def embed_texts(texts: list[str], batch_size: int = 16) -> list[list[float]]:
    """
    Embed a list of text strings.

    Returns a list of float vectors (one per input string).
    Empty strings are skipped and result in zero vectors.
    """
    if not texts:
        return []
    model = _get_text_model()
    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    return [vec.tolist() for vec in embeddings]


def embed_text(text: str) -> list[float]:
    """Embed a single string. Convenience wrapper around embed_texts."""
    results = embed_texts([text])
    return results[0] if results else []


# ── Visual (CLIP) embedding ────────────────────────────────────────────────────

def _get_clip():
    global _clip_model, _clip_preprocess, _clip_tokenizer, _clip_broken
    if _clip_broken:
        raise RuntimeError("CLIP model failed to initialise (see earlier log)")
    if _clip_model is None:
        import open_clip
        logger.info(f"Loading CLIP model: {_CLIP_MODEL_NAME} / {_CLIP_PRETRAINED}…")
        try:
            _clip_model, _, _clip_preprocess = open_clip.create_model_and_transforms(
                _CLIP_MODEL_NAME, pretrained=_CLIP_PRETRAINED, device="cpu"
            )
            _clip_model.eval()
            _clip_tokenizer = open_clip.get_tokenizer(_CLIP_MODEL_NAME)
            logger.info("CLIP model ready")
        except Exception as exc:
            _clip_broken = True
            _clip_model = None
            logger.error(f"CLIP model initialisation failed — visual embeddings disabled: {exc}")
            raise
    return _clip_model, _clip_preprocess, _clip_tokenizer


def embed_image_path(image_path: str | Path) -> Optional[list[float]]:
    """
    Generate a CLIP visual embedding from an image file on disk.
    Returns None if the file cannot be loaded.
    """
    from PIL import Image
    path = Path(image_path)
    if not path.is_file():
        logger.warning(f"CLIP: image not found: {path}")
        return None
    try:
        model, preprocess, _ = _get_clip()
        img = Image.open(path).convert("RGB")
        tensor = preprocess(img).unsqueeze(0)  # type: ignore[operator]
        with torch.no_grad():
            features = model.encode_image(tensor)
            features = features / features.norm(dim=-1, keepdim=True)
        return features[0].cpu().tolist()
    except Exception as exc:
        logger.warning(f"CLIP embedding failed for {path.name}: {exc}")
        return None


def embed_query_text_clip(query: str) -> Optional[list[float]]:
    """
    Embed a text query using CLIP's text encoder so it can be compared
    against visual embeddings in the same vector space.
    """
    try:
        model, _, tokenizer = _get_clip()
        tokens = tokenizer([query])
        with torch.no_grad():
            features = model.encode_text(tokens)
            features = features / features.norm(dim=-1, keepdim=True)
        return features[0].cpu().tolist()
    except Exception as exc:
        logger.warning(f"CLIP text query embedding failed: {exc}")
        return None


def embed_clip_texts_batch(texts: list[str], batch_size: int = 64) -> list[list[float]]:
    """
    Embed multiple text prompts using CLIP's text encoder.
    Useful for building large concept vocabularies.
    """
    if not texts:
        return []
    try:
        model, _, tokenizer = _get_clip()
        vectors: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            tokens = tokenizer(batch)
            with torch.no_grad():
                features = model.encode_text(tokens)
                features = features / features.norm(dim=-1, keepdim=True)
            vectors.extend(features.cpu().tolist())
        return vectors
    except Exception as exc:
        logger.warning(f"CLIP batch text embedding failed: {exc}")
        return []
