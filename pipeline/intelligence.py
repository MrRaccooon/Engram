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

def _get_chunk_text(chunk: dict[str, Any]) -> str:
    """Extract the best available text from a chunk, preferring full content."""
    return chunk.get("content") or chunk.get("content_preview") or ""


def _local_summarize(chunks: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    """
    Call a local Ollama model to compress each chunk's text content.
    Falls back gracefully if Ollama is not running.
    """
    if not model:
        return chunks

    try:
        import requests as req
        summarized = []
        for chunk in chunks:
            text = _get_chunk_text(chunk)
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
                summarized.append({**chunk, "content": summary})
            else:
                summarized.append(chunk)

        return summarized

    except Exception as exc:
        logger.warning(f"Local pre-summarizer unavailable ({exc}), skipping compression")
        return chunks


# ── Prompt assembly ───────────────────────────────────────────────────────────

_MAX_CHARS_PER_CAPTURE = 1200


def _assemble_prompt(
    query: str,
    chunks: list[dict[str, Any]],
    max_tokens: int,
    insights: list[dict[str, Any]] | None = None,
) -> str:
    """Build a structured, temporally-ordered prompt for the frontier API."""
    sections: list[str] = []
    token_count = 0

    if insights:
        insight_lines = []
        for ins in insights:
            summary = ins.get("summary_preview") or ins.get("summary") or ""
            date = ins.get("date", "")
            if summary:
                insight_lines.append(f"- [{date}] {summary[:300]}")
        if insight_lines:
            block = "## Session Summaries\n" + "\n".join(insight_lines)
            token_count += len(block.split())
            sections.append(block)

    sorted_chunks = sorted(
        chunks,
        key=lambda c: c.get("timestamp") or "",
    )

    capture_lines = []
    for chunk in sorted_chunks:
        text = _get_chunk_text(chunk)
        if not text.strip():
            continue

        if len(text) > _MAX_CHARS_PER_CAPTURE:
            text = text[:_MAX_CHARS_PER_CAPTURE] + "…"

        source = chunk.get("source_type", "unknown")
        ts_raw = chunk.get("timestamp") or ""
        app = (chunk.get("app_name") or "").replace(".exe", "")
        url = chunk.get("url", "")
        window = chunk.get("window_title") or ""

        try:
            from datetime import datetime as _dt
            ts_dt = _dt.fromisoformat(ts_raw.replace("Z", "+00:00"))
            ts_label = ts_dt.strftime("%I:%M %p")
        except Exception:
            ts_label = ts_raw[:16]

        header = f"### {ts_label} — {app or source.upper()}"
        if window and app and window != app:
            header += f" ({window[:60]})"
        if url:
            header += f"\n{url[:80]}"

        entry = f"{header}\n{text}"
        entry_tokens = len(entry.split())

        if token_count + entry_tokens > max_tokens:
            break

        capture_lines.append(entry)
        token_count += entry_tokens

    if capture_lines:
        sections.append(
            "## Relevant Captures (chronological)\n\n"
            + "\n\n".join(capture_lines)
        )

    context_block = "\n\n".join(sections) if sections else "(No relevant context found.)"

    return (
        f"{context_block}\n\n"
        f"---\n\n"
        f"Question: {query}"
    )


# ── API providers ─────────────────────────────────────────────────────────────

_RESPONSE_MAX_TOKENS = 2048


def _call_anthropic(system: str, user_prompt: str, model: str, api_key: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=_RESPONSE_MAX_TOKENS,
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
        max_tokens=_RESPONSE_MAX_TOKENS,
    )
    return response.choices[0].message.content or ""


def _call_openrouter(system: str, user_prompt: str, model: str, api_key: str) -> str:
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
        max_tokens=_RESPONSE_MAX_TOKENS,
    )
    return response.choices[0].message.content or ""


# ── Public API ────────────────────────────────────────────────────────────────

def build_preview(
    query: str,
    retrieved_chunks: list[dict[str, Any]],
    session_context: str = "",
    insights: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Run the privacy pipeline up to (but not including) the API call.

    Returns a preview dict that the frontend can show the user before
    they confirm sending.
    """
    full_cfg = _load_full_config()
    intel_cfg = _load_intelligence_config()
    cap_cfg = full_cfg.get("capture", {})

    threshold = intel_cfg.get("sensitivity_threshold", 0.4)
    max_tokens = intel_cfg.get("max_context_tokens", 800)
    local_model = intel_cfg.get("local_summarizer", "")
    excluded_apps = cap_cfg.get("excluded_apps", [])
    excluded_domains = cap_cfg.get("excluded_domains", [])

    passing, blocked_count = sensitivity.filter_chunks(
        retrieved_chunks, threshold, excluded_apps, excluded_domains
    )
    masked_chunks, entity_map = entity_masker.mask_chunks(passing)
    compressed_chunks = _local_summarize(masked_chunks, local_model)
    user_prompt = _assemble_prompt(query, compressed_chunks, max_tokens, insights=insights)
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
    insights: list[dict[str, Any]] | None = None,
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

    preview = build_preview(query, retrieved_chunks, session_context=session_context, insights=insights)
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
