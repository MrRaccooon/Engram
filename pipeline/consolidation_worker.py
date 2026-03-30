"""
Nightly consolidation worker — the "Sleep Cycle".

Runs once daily (default 2 AM). For each un-consolidated day it:
  1. Fetches all indexed captures for that day
  2. Groups them into temporal sessions (configurable gap, default 30 min)
  3. Calls a local Ollama model to produce a ~150-token structured summary
     per session (topics, decisions, projects, context)
  4. Inserts the summary into the `insights` SQLite table
  5. Embeds the summary and upserts it into the ChromaDB insights collection

Insight summaries are *derived* from raw captures but contain no raw data.
They are safe to use as context in the intelligence pipeline (Phase 1).

Falls back gracefully if Ollama is not running — sessions are still recorded
with a heuristic summary built from app names and window titles.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from pipeline import embedder
from storage import metadata_db, vector_db

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"

_CONSOLIDATION_PROMPT = """You are summarizing a person's digital work session.
Given the following screen captures from a single session, write a concise 2-3 sentence
summary covering: (1) what the person was doing, (2) key topics or projects involved,
(3) any notable decisions or outcomes visible.

Be factual, impersonal, and brief. Output ONLY the summary — no preamble.

Session captures:
{captures}"""


def _load_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _group_into_sessions(
    captures: list[Any],
    gap_minutes: int = 30,
) -> list[list[Any]]:
    """
    Group captures into sessions separated by gaps >= gap_minutes.
    Each capture is a sqlite3.Row with a 'timestamp' field.
    """
    if not captures:
        return []

    sessions: list[list[Any]] = []
    current_session = [captures[0]]

    for cap in captures[1:]:
        prev_ts = datetime.fromisoformat(current_session[-1]["timestamp"])
        cur_ts = datetime.fromisoformat(cap["timestamp"])
        gap = (cur_ts - prev_ts).total_seconds() / 60

        if gap >= gap_minutes:
            sessions.append(current_session)
            current_session = [cap]
        else:
            current_session.append(cap)

    sessions.append(current_session)
    return sessions


def _heuristic_summary(session: list[Any]) -> tuple[str, list[str]]:
    """
    Build a basic summary from app names and window titles when
    Ollama is not available.
    """
    apps: dict[str, int] = {}
    titles = set()

    for cap in session:
        app = cap["app_name"] or ""
        title = cap["window_title"] or ""
        if app:
            apps[app] = apps.get(app, 0) + 1
        if title and len(title) > 3:
            titles.add(title[:80])

    top_app = max(apps, key=lambda a: apps[a]) if apps else "unknown"
    title_sample = ", ".join(list(titles)[:3])
    duration_min = round(
        (
            datetime.fromisoformat(session[-1]["timestamp"])
            - datetime.fromisoformat(session[0]["timestamp"])
        ).total_seconds() / 60,
        1,
    )

    summary = (
        f"Session of {duration_min} minutes primarily in {top_app}. "
        f"{('Windows: ' + title_sample + '.') if title_sample else ''}"
    ).strip()

    topics = list(apps.keys())[:5]
    return summary, topics


def _build_capture_texts(session: list[Any]) -> list[str]:
    """Build a list of text snippets from session captures for LLM summarization."""
    capture_texts = []
    for cap in session[:25]:
        # Prefer content, fall back to window title
        text = cap["content"] or cap["window_title"] or ""
        app = (cap["app_name"] or "").replace(".exe", "")
        ts = (cap["timestamp"] or "")[:16]
        if text.strip():
            capture_texts.append(f"[{ts}] {app}: {text[:250]}")
    return capture_texts


def _api_summary(session: list[Any]) -> tuple[str, list[str]] | None:
    """
    Use the configured frontier API (OpenRouter / OpenAI / Anthropic) to summarize
    a session. Returns (summary, topics) or None on failure.
    """
    import os
    try:
        cfg = _load_config()
        intel = cfg.get("intelligence", {})
        provider = intel.get("api_provider", "disabled")
        model = intel.get("api_model", "openai/gpt-4o-mini")

        if provider == "disabled":
            return None

        capture_texts = _build_capture_texts(session)
        if not capture_texts:
            return None

        prompt = _CONSOLIDATION_PROMPT.format(captures="\n".join(capture_texts))
        apps_in_session = list({(cap["app_name"] or "").replace(".exe", "") for cap in session if cap["app_name"]})

        from openai import OpenAI

        if provider == "openrouter":
            api_key = os.environ.get("OPENROUTER_API_KEY", "")
            if not api_key:
                return None
            client = OpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
                default_headers={"HTTP-Referer": "https://github.com/Engram", "X-Title": "Engram"},
            )
        elif provider == "openai":
            api_key = os.environ.get("OPENAI_API_KEY", "")
            if not api_key:
                return None
            client = OpenAI(api_key=api_key)
        else:
            return None

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You summarize a person's digital work session concisely."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=200,
            temperature=0.2,
        )
        summary = (response.choices[0].message.content or "").strip()
        if summary:
            logger.debug(f"Consolidation: API summary generated via {provider}/{model}")
            return summary, apps_in_session[:5]

    except Exception as exc:
        logger.debug(f"API summary failed for consolidation: {exc}")

    return None


def _ollama_summary(session: list[Any], model: str) -> tuple[str, list[str]]:
    """
    Try Ollama first, then frontier API, then heuristic fallback.
    """
    # 1. Try Ollama (local, free)
    if model:
        try:
            import requests as req
            capture_texts = _build_capture_texts(session)
            if capture_texts:
                prompt = _CONSOLIDATION_PROMPT.format(captures="\n".join(capture_texts))
                payload = {
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": 150, "temperature": 0.2},
                }
                resp = req.post("http://127.0.0.1:11434/api/generate", json=payload, timeout=30)
                if resp.ok:
                    summary = resp.json().get("response", "").strip()
                    if summary:
                        apps_in_session = list({cap["app_name"] for cap in session if cap["app_name"]})
                        return summary, apps_in_session[:5]
        except Exception as exc:
            logger.debug(f"Ollama unavailable for consolidation: {exc}")

    # 2. Fall back to frontier API (OpenRouter/OpenAI)
    api_result = _api_summary(session)
    if api_result:
        return api_result

    # 3. Final fallback: heuristic
    return _heuristic_summary(session)


def _consolidate_day(date_str: str, gap_minutes: int, local_model: str) -> int:
    """
    Consolidate a single day. Returns the number of insights created.
    """
    captures = metadata_db.fetch_captures_for_day(date_str)
    indexed = [c for c in captures if c["status"] == "indexed"]

    if not indexed:
        logger.debug(f"Consolidation: no indexed captures for {date_str}, skipping")
        return 0

    sessions = _group_into_sessions(indexed, gap_minutes)
    insights_created = 0

    for session in sessions:
        if len(session) < 2:
            continue

        session_start = session[0]["timestamp"]
        session_end = session[-1]["timestamp"]

        # Generate summary
        if local_model:
            summary, topics = _ollama_summary(session, local_model)
        else:
            summary, topics = _heuristic_summary(session)

        insight_id = str(uuid.uuid4())
        topics_json = json.dumps(topics)

        # Write to SQLite
        metadata_db.insert_insight(
            insight_id=insight_id,
            date=date_str,
            session_start=session_start,
            session_end=session_end,
            summary=summary,
            topics=topics_json,
        )

        # Embed and write to ChromaDB
        embedding = embedder.embed_text(summary)
        if embedding:
            vector_db.upsert_insight(
                doc_id=insight_id,
                embedding=embedding,
                insight_id=insight_id,
                date=date_str,
                summary_preview=summary,
                topics=topics_json,
            )

        insights_created += 1
        logger.debug(
            f"Consolidation: {date_str} session {session_start[:16]}–{session_end[:16]} "
            f"→ {len(session)} captures → insight created"
        )

    return insights_created


def run_consolidation(days_back: int = 1) -> None:
    """
    Main entry point called by APScheduler.

    Consolidates all un-consolidated days from the last `days_back` days.
    Default is 1 (yesterday). On first run, or after a gap, all missing
    days are consolidated automatically.
    """
    cfg = _load_config()
    cons_cfg = cfg.get("consolidation", {})
    intel_cfg = cfg.get("intelligence", {})

    gap_minutes: int = cons_cfg.get("session_gap_minutes", 30)
    local_model: str = intel_cfg.get("local_summarizer", "")

    today = datetime.utcnow().date()
    total_insights = 0

    for offset in range(days_back, 0, -1):
        day = (today - timedelta(days=offset)).isoformat()

        if metadata_db.has_insight_for_day(day):
            logger.debug(f"Consolidation: {day} already consolidated, skipping")
            continue

        logger.info(f"Consolidation: processing {day}…")
        count = _consolidate_day(day, gap_minutes, local_model)
        total_insights += count
        logger.info(f"Consolidation: {day} → {count} insight(s) created")

    if total_insights:
        logger.info(f"Consolidation complete: {total_insights} total insight(s)")
    else:
        logger.info("Consolidation complete: nothing to consolidate")
