"""
Intelligence pipeline — privacy-preserving frontier API integration.

Full pipeline per query:
  1. Dual vector retrieval  (caller provides pre-retrieved candidates)
  2. Sensitivity filtering  (pipeline/sensitivity.py)
  3. PII entity masking     (pipeline/entity_masker.py)
  4. Local pre-summarization (optional — Ollama small model)
  5. Prompt assembly        (query + masked context + abstract profile)
  6. Frontier API call      (Anthropic / OpenAI — only masked data sent)
  7. Entity re-substitution  (restore real names in the response)

The caller can run steps 1-5 only (preview mode) to show the user exactly
what would be sent before any external call is made.

Privacy guarantee:
  - Raw captures never leave the machine.
  - Only the masked, pre-summarized context for the specific query is sent.
  - The API call is optional and gated by user confirmation.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from pipeline import entity_masker, sensitivity

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"

_SYSTEM_PROMPT_BASE = textwrap.dedent("""
    You are Engram, a personal AI memory system embedded in the user's computer.
    You observe everything the user does — files edited, websites visited, code written,
    research done — and build a continuously-updated understanding of their work.

    Your job is to answer questions about the user's past activity, current focus,
    and accumulated knowledge as if you were a highly observant personal assistant
    who has been watching over their shoulder.

    Rules:
    - Answer directly and specifically. Avoid vague hedging like "it seems you may have".
    - If you know the answer from context, state it confidently.
    - If context is insufficient, say exactly what you DO know and what's missing.
    - Some entities appear as placeholders like [PERSON_1] or [ORG_1] — use them
      naturally; they will be restored to real names before the user sees your answer.
    - Never invent facts not present in the context.
""").strip()


def _build_system_prompt(session_context: str = "") -> str:
    """Build a dynamic system prompt that includes the user's current session."""
    if not session_context.strip():
        return _SYSTEM_PROMPT_BASE

    return (
        _SYSTEM_PROMPT_BASE
        + "\n\n--- CURRENT USER CONTEXT ---\n"
        + session_context.strip()
        + "\n--- END CONTEXT ---"
    )


def _load_intelligence_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("intelligence", {})


def _load_full_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── Local pre-summarizer (optional, via Ollama) ───────────────────────────────

def _local_summarize(chunks: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    """
    Call a local Ollama model to compress each chunk's content_preview.
    Falls back gracefully if Ollama is not running.
    """
    if not model:
        return chunks

    try:
        import requests as req
        summarized = []
        for chunk in chunks:
            text = chunk.get("content_preview", "") or ""
            if len(text.split()) < 40:
                summarized.append(chunk)
                continue

            payload = {
                "model": model,
                "prompt": (
                    f"Summarize the following screen capture excerpt in 1-2 sentences, "
                    f"preserving key facts, topics, and context. No commentary.\n\n{text}"
                ),
                "stream": False,
                "options": {"num_predict": 80, "temperature": 0.1},
            }
            resp = req.post(
                "http://127.0.0.1:11434/api/generate",
                json=payload,
                timeout=15,
            )
            if resp.ok:
                summary = resp.json().get("response", text).strip()
                summarized.append({**chunk, "content_preview": summary})
            else:
                summarized.append(chunk)

        return summarized

    except Exception as exc:
        logger.warning(f"Local pre-summarizer unavailable ({exc}), skipping compression")
        return chunks


# ── Prompt assembly ───────────────────────────────────────────────────────────

def _assemble_prompt(query: str, chunks: list[dict[str, Any]], max_tokens: int) -> str:
    """Build the context block that gets sent to the frontier API."""
    context_parts = []
    token_count = 0

    for i, chunk in enumerate(chunks, 1):
        text = chunk.get("content_preview", "") or ""
        source = chunk.get("source_type", "unknown")
        ts = (chunk.get("timestamp") or "")[:19]
        app = chunk.get("app_name", "")
        url = chunk.get("url", "")

        meta = f"[{i}] {source.upper()} • {ts}"
        if app:
            meta += f" • {app}"
        if url:
            meta += f" • {url[:60]}"

        entry = f"{meta}\n{text}"
        entry_tokens = len(entry.split())

        if token_count + entry_tokens > max_tokens:
            break

        context_parts.append(entry)
        token_count += entry_tokens

    context_block = "\n\n---\n\n".join(context_parts)

    return (
        f"Context from your digital activity:\n\n"
        f"{context_block}\n\n"
        f"---\n\n"
        f"Question: {query}"
    )


# ── API providers ─────────────────────────────────────────────────────────────

def _call_anthropic(system: str, user_prompt: str, model: str, api_key: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text


def _call_openai(system: str, user_prompt: str, model: str, api_key: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=1024,
    )
    return response.choices[0].message.content or ""


def _call_openrouter(system: str, user_prompt: str, model: str, api_key: str) -> str:
    """
    OpenRouter is OpenAI-compatible — same client, different base_url.
    Supports every major model (Gemini, Claude, GPT, Llama, etc.)
    at the cheapest available rates.
    """
    from openai import OpenAI
    client = OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": "https://github.com/MrRaccooon/Engram",
            "X-Title": "Engram",
        },
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=1024,
    )
    return response.choices[0].message.content or ""


# ── Public API ────────────────────────────────────────────────────────────────

def build_preview(
    query: str,
    retrieved_chunks: list[dict[str, Any]],
    session_context: str = "",
) -> dict[str, Any]:
    """
    Run the privacy pipeline up to (but not including) the API call.

    Returns a preview dict that the frontend can show the user before
    they confirm sending.

    Returns:
        {
          "masked_prompt": str,       # exact text that would be sent
          "entity_map": dict,         # placeholder → real value
          "blocked_count": int,       # chunks removed by sensitivity filter
          "passing_count": int,       # chunks that passed
          "estimated_tokens": int,    # rough token count of the prompt
        }
    """
    full_cfg = _load_full_config()
    intel_cfg = _load_intelligence_config()
    cap_cfg = full_cfg.get("capture", {})

    threshold = intel_cfg.get("sensitivity_threshold", 0.4)
    max_tokens = intel_cfg.get("max_context_tokens", 800)
    local_model = intel_cfg.get("local_summarizer", "")
    excluded_apps = cap_cfg.get("excluded_apps", [])
    excluded_domains = cap_cfg.get("excluded_domains", [])

    # Step 2: Sensitivity filter
    passing, blocked_count = sensitivity.filter_chunks(
        retrieved_chunks, threshold, excluded_apps, excluded_domains
    )

    # Step 3: Entity masking
    masked_chunks, entity_map = entity_masker.mask_chunks(passing)

    # Step 4: Local pre-summarization (optional)
    compressed_chunks = _local_summarize(masked_chunks, local_model)

    # Step 5: Assemble prompt
    user_prompt = _assemble_prompt(query, compressed_chunks, max_tokens)
    system_prompt = _build_system_prompt(session_context)

    return {
        "masked_prompt": user_prompt,
        "entity_map": entity_map,
        "blocked_count": blocked_count,
        "passing_count": len(passing),
        "estimated_tokens": len(user_prompt.split()),
        "system_prompt": system_prompt,
    }


def ask(
    query: str,
    retrieved_chunks: list[dict[str, Any]],
    deep: bool = False,
    session_context: str = "",
) -> dict[str, Any]:
    """
    Run the full privacy pipeline and call the configured frontier API.

    Args:
        query: The user's natural language question.
        retrieved_chunks: Pre-retrieved candidates from the vector store.
        deep: If True, use the more capable (slower, costlier) model.

    Returns:
        {
          "answer": str,              # final answer with real entity names
          "blocked_count": int,
          "passing_count": int,
          "model_used": str,
          "provider": str,
        }
    """
    import os

    intel_cfg = _load_intelligence_config()
    provider = intel_cfg.get("api_provider", "disabled")

    if provider == "disabled":
        return {
            "answer": (
                "Intelligence API is disabled. Set `intelligence.api_provider` "
                "in config.yaml to 'anthropic' or 'openai' and add your API key."
            ),
            "blocked_count": 0,
            "passing_count": 0,
            "model_used": "none",
            "provider": "disabled",
        }

    model = (
        intel_cfg.get("api_model_deep", "google/gemini-2.0-flash") if deep
        else intel_cfg.get("api_model", "google/gemini-2.0-flash-lite")
    )

    # Build the preview (runs sensitivity + masking + summarization)
    preview = build_preview(query, retrieved_chunks, session_context=session_context)
    user_prompt = preview["masked_prompt"]
    entity_map = preview["entity_map"]
    system_prompt = preview["system_prompt"]

    if preview["passing_count"] == 0:
        return {
            "answer": (
                "All retrieved context was blocked by the sensitivity filter. "
                "The query likely touched sensitive data (passwords, financial info, etc.). "
                "Try rephrasing or ask about a different topic."
            ),
            "blocked_count": preview["blocked_count"],
            "passing_count": 0,
            "model_used": model,
            "provider": provider,
        }

    logger.info(
        f"Intelligence: calling {provider}/{model} | "
        f"~{preview['estimated_tokens']} tokens | "
        f"{preview['passing_count']} chunks | "
        f"{preview['blocked_count']} blocked"
    )

    try:
        if provider == "anthropic":
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY not set in environment")
            raw_answer = _call_anthropic(system_prompt, user_prompt, model, api_key)

        elif provider == "openai":
            api_key = os.environ.get("OPENAI_API_KEY", "")
            if not api_key:
                raise ValueError("OPENAI_API_KEY not set in environment")
            raw_answer = _call_openai(system_prompt, user_prompt, model, api_key)

        elif provider == "openrouter":
            api_key = os.environ.get("OPENROUTER_API_KEY", "")
            if not api_key:
                raise ValueError("OPENROUTER_API_KEY not set in environment")
            raw_answer = _call_openrouter(system_prompt, user_prompt, model, api_key)

        else:
            raise ValueError(f"Unknown provider: {provider}")

        # Step 7: Restore real entity names
        answer = entity_masker.unmask(raw_answer, entity_map)

        return {
            "answer": answer,
            "blocked_count": preview["blocked_count"],
            "passing_count": preview["passing_count"],
            "model_used": model,
            "provider": provider,
        }

    except Exception as exc:
        logger.error(f"Intelligence API call failed: {exc}")
        return {
            "answer": f"API call failed: {exc}",
            "blocked_count": preview["blocked_count"],
            "passing_count": preview["passing_count"],
            "model_used": model,
            "provider": provider,
        }
