"""
Consolidation worker — structured session summaries + topic threads.

Three modes:
  - micro:   Runs every 2 hours, summarizes recent activity
  - daily:   Runs nightly (2 AM), produces structured day-level summaries
  - weekly:  Runs weekly, rolls up daily summaries into week-level narratives

Summaries are structured JSON with narrative, topics, projects,
decisions, problems, and outcomes. Topic threads accumulate knowledge
across sessions for recurring topics.
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

_STRUCTURED_PROMPT = """You are summarizing a person's digital work session from their screen captures.

Produce a JSON object with these fields:
- "narrative": A detailed 4-6 sentence summary of what happened, in chronological order. Include specific file names, URLs, topics, and decisions visible in the captures.
- "topics": 2-5 semantic topic tags (not app names). Use lowercase-hyphenated format like "vector-databases", "api-design".
- "projects": Project names visible in window titles or file paths.
- "files_touched": Files that appear in editor windows or terminal commands.
- "decisions": Any decisions or conclusions visible in the activity.
- "problems": Issues or errors the user appeared to encounter.
- "outcomes": What was accomplished or completed.

Be specific and factual. Use details from the captures, not generic descriptions.
Return ONLY valid JSON — no markdown fences, no explanation.

Session captures:
{captures}"""

_MICRO_PROMPT = """Summarize this recent activity in 2-3 sentences. Be specific about what was being done, which files/tools were used, and what topics were involved.

Activity:
{captures}"""


def _load_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _group_into_sessions(
    captures: list[Any],
    gap_minutes: int = 30,
) -> list[list[Any]]:
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


def _session_duration_minutes(session: list[Any]) -> float:
    if len(session) < 2:
        return 0
    start = datetime.fromisoformat(session[0]["timestamp"])
    end = datetime.fromisoformat(session[-1]["timestamp"])
    return round((end - start).total_seconds() / 60, 1)


def _heuristic_summary(session: list[Any]) -> dict[str, Any]:
    apps: dict[str, int] = {}
    titles: set[str] = set()
    files: list[str] = []

    for cap in session:
        app = cap["app_name"] or ""
        title = cap["window_title"] or ""
        if app:
            apps[app] = apps.get(app, 0) + 1
        if title and len(title) > 3:
            titles.add(title[:80])

    top_app = max(apps, key=lambda a: apps[a]) if apps else "unknown"
    duration = _session_duration_minutes(session)
    title_sample = ", ".join(list(titles)[:3])

    narrative = (
        f"Session of {duration} minutes primarily in {top_app}. "
        f"{('Windows: ' + title_sample + '.') if title_sample else ''}"
    ).strip()

    return {
        "narrative": narrative,
        "topics": list(apps.keys())[:5],
        "projects": [],
        "files_touched": files,
        "decisions": [],
        "problems": [],
        "outcomes": [],
    }


def _build_capture_texts(session: list[Any], max_captures: int = 25) -> list[str]:
    capture_texts = []
    for cap in session[:max_captures]:
        text = cap["content"] or cap["window_title"] or ""
        app = (cap["app_name"] or "").replace(".exe", "")
        ts = (cap["timestamp"] or "")[:16]
        cap_id = cap.get("id", "")

        extras: list[str] = []

        if cap_id:
            try:
                concept_rows = metadata_db.fetch_concepts_for_capture(cap_id, limit=5)
                if concept_rows:
                    concept_labels = [r["prompt"][:40] for r in concept_rows]
                    extras.append(f"[concepts: {', '.join(concept_labels)}]")
            except Exception:
                pass

            try:
                event_rows = metadata_db.fetch_events_for_capture(cap_id, limit=3)
                if event_rows:
                    actions = []
                    for ev in event_rows:
                        ct = ev["change_type"] or ""
                        ct_text = (ev["changed_text"] or "")[:80]
                        if ct and ct != "idle":
                            actions.append(f"{ct}: {ct_text}" if ct_text else ct)
                    if actions:
                        extras.append(f"[actions: {'; '.join(actions)}]")
            except Exception:
                pass

        suffix = " ".join(extras)
        if text.strip():
            line = f"[{ts}] {app}: {text[:400]}"
            if suffix:
                line = f"{line} {suffix}"
            capture_texts.append(line)

    return capture_texts


def _get_llm_client(cfg: dict):
    """Return (client, model, provider) or None."""
    import os
    from openai import OpenAI

    intel = cfg.get("intelligence", {})
    provider = intel.get("api_provider", "disabled")
    model = intel.get("api_model", "openai/gpt-4o-mini")

    if provider == "disabled":
        return None

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

    return client, model, provider


def _parse_structured_json(raw: str) -> dict[str, Any] | None:
    """Parse LLM output as JSON, stripping markdown fences if present."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict) and "narrative" in data:
            for key in ("topics", "projects", "files_touched", "decisions", "problems", "outcomes"):
                if key not in data or not isinstance(data[key], list):
                    data[key] = []
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    return None


def _llm_structured_summary(session: list[Any], cfg: dict) -> dict[str, Any] | None:
    """Get a structured JSON summary via LLM. Returns parsed dict or None."""
    result = _get_llm_client(cfg)
    if not result:
        return None

    client, model, provider = result
    capture_texts = _build_capture_texts(session)
    if not capture_texts:
        return None

    prompt = _STRUCTURED_PROMPT.format(captures="\n".join(capture_texts))

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You produce structured JSON summaries of digital work sessions."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=500,
            temperature=0.2,
        )
        raw = (response.choices[0].message.content or "").strip()
        parsed = _parse_structured_json(raw)
        if parsed:
            logger.debug(f"Consolidation: structured summary via {provider}/{model}")
            return parsed

        logger.debug(f"Consolidation: JSON parse failed, using raw text as narrative")
        return {
            "narrative": raw[:600],
            "topics": [],
            "projects": [],
            "files_touched": [],
            "decisions": [],
            "problems": [],
            "outcomes": [],
        }
    except Exception as exc:
        logger.debug(f"LLM structured summary failed: {exc}")

    return None


def _ollama_structured_summary(session: list[Any], model: str) -> dict[str, Any] | None:
    """Try Ollama for structured extraction."""
    if not model:
        return None
    try:
        import requests as req
        capture_texts = _build_capture_texts(session)
        if not capture_texts:
            return None

        prompt = _STRUCTURED_PROMPT.format(captures="\n".join(capture_texts))
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": 500, "temperature": 0.2},
        }
        resp = req.post("http://127.0.0.1:11434/api/generate", json=payload, timeout=45)
        if resp.ok:
            raw = resp.json().get("response", "").strip()
            return _parse_structured_json(raw)
    except Exception as exc:
        logger.debug(f"Ollama structured summary unavailable: {exc}")

    return None


def _summarize_session(session: list[Any], cfg: dict) -> dict[str, Any]:
    """Produce a structured summary using the best available method."""
    local_model = cfg.get("intelligence", {}).get("local_summarizer", "")

    result = _ollama_structured_summary(session, local_model)
    if result:
        return result

    result = _llm_structured_summary(session, cfg)
    if result:
        return result

    return _heuristic_summary(session)


def _save_insight(
    date_str: str,
    session: list[Any],
    structured: dict[str, Any],
    consolidation_type: str = "daily",
) -> str:
    """Save a structured insight to SQLite + ChromaDB. Returns insight ID."""
    insight_id = str(uuid.uuid4())
    session_start = session[0]["timestamp"]
    session_end = session[-1]["timestamp"]

    summary = structured.get("narrative", "")[:500]
    topics_list = structured.get("topics", [])

    metadata_db.insert_insight(
        insight_id=insight_id,
        date=date_str,
        session_start=session_start,
        session_end=session_end,
        summary=summary,
        topics=json.dumps(topics_list),
        narrative=structured.get("narrative"),
        topics_structured=json.dumps(topics_list),
        projects=json.dumps(structured.get("projects", [])),
        files_touched=json.dumps(structured.get("files_touched", [])),
        decisions=json.dumps(structured.get("decisions", [])),
        problems=json.dumps(structured.get("problems", [])),
        outcomes=json.dumps(structured.get("outcomes", [])),
        consolidation_type=consolidation_type,
    )

    embedding = embedder.embed_text(summary)
    if embedding:
        vector_db.upsert_insight(
            doc_id=insight_id,
            embedding=embedding,
            insight_id=insight_id,
            date=date_str,
            summary_preview=summary[:300],
            topics=json.dumps(topics_list),
        )

    return insight_id


# ── Topic thread updates ──────────────────────────────────────────────────────

_TOPIC_THREAD_THRESHOLD = 3  # create a thread after this many occurrences


def _update_topic_threads(structured: dict[str, Any], duration_min: float) -> None:
    """Update topic threads based on extracted topics from a session."""
    topics = structured.get("topics", [])
    if not topics:
        return

    for topic in topics:
        topic_clean = topic.strip().lower()
        if not topic_clean or len(topic_clean) < 3:
            continue

        occurrences = metadata_db.count_topic_occurrences(topic_clean)
        if occurrences < _TOPIC_THREAD_THRESHOLD:
            continue

        narrative = structured.get("narrative", "")
        projects = structured.get("projects", [])
        files = structured.get("files_touched", [])
        decisions = structured.get("decisions", [])

        existing = metadata_db.fetch_topic_thread(topic_clean)
        if existing and existing["summary"]:
            summary = existing["summary"]
            if len(summary) < 2000 and narrative:
                summary = summary + "\n" + narrative[:400]
        else:
            summary = narrative[:600] if narrative else topic_clean

        metadata_db.upsert_topic_thread(
            topic=topic_clean,
            summary=summary,
            session_count_delta=1,
            minutes_delta=duration_min,
            projects=json.dumps(projects) if projects else None,
            files_touched=json.dumps(files) if files else None,
            decisions=json.dumps(decisions) if decisions else None,
        )


# ── Consolidation modes ───────────────────────────────────────────────────────

def _consolidate_day(date_str: str, gap_minutes: int, cfg: dict) -> int:
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

        structured = _summarize_session(session, cfg)
        _save_insight(date_str, session, structured, consolidation_type="daily")
        duration = _session_duration_minutes(session)
        _update_topic_threads(structured, duration)
        insights_created += 1

        logger.debug(
            f"Consolidation: {date_str} session "
            f"{session[0]['timestamp'][:16]}–{session[-1]['timestamp'][:16]} "
            f"→ {len(session)} captures → insight created"
        )

    return insights_created


def run_consolidation(days_back: int = 1) -> None:
    """Nightly consolidation entry point (called by APScheduler)."""
    cfg = _load_config()
    cons_cfg = cfg.get("consolidation", {})
    gap_minutes: int = cons_cfg.get("session_gap_minutes", 30)

    today = datetime.utcnow().date()
    total_insights = 0

    for offset in range(days_back, 0, -1):
        day = (today - timedelta(days=offset)).isoformat()

        if metadata_db.has_insight_for_day(day, consolidation_type="daily"):
            logger.debug(f"Consolidation: {day} already has daily insights, skipping")
            continue

        logger.info(f"Consolidation: processing {day}…")
        count = _consolidate_day(day, gap_minutes, cfg)
        total_insights += count
        logger.info(f"Consolidation: {day} → {count} insight(s) created")

    if total_insights:
        logger.info(f"Consolidation complete: {total_insights} total insight(s)")
    else:
        logger.info("Consolidation complete: nothing to consolidate")


def run_micro_consolidation() -> None:
    """
    Lightweight consolidation of the last 2 hours.
    Runs every 2 hours via APScheduler. Produces micro-insights so
    recent activity is queryable immediately, not just after the nightly run.
    """
    cfg = _load_config()
    gap_minutes = cfg.get("consolidation", {}).get("session_gap_minutes", 30)
    now = datetime.utcnow()
    cutoff = (now - timedelta(hours=2)).isoformat()
    today_str = now.date().isoformat()

    try:
        recent = metadata_db.fetch_recent_indexed_captures(cutoff, limit=200)
    except Exception as exc:
        logger.debug(f"Micro-consolidation: fetch failed: {exc}")
        return

    if len(recent) < 3:
        logger.debug("Micro-consolidation: too few recent captures, skipping")
        return

    sessions = _group_into_sessions(recent, gap_minutes)
    created = 0

    for session in sessions:
        if len(session) < 2:
            continue

        capture_texts = _build_capture_texts(session, max_captures=15)
        if not capture_texts:
            continue

        result = _get_llm_client(cfg)
        if result:
            client, model, _ = result
            prompt = _MICRO_PROMPT.format(captures="\n".join(capture_texts))
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=200,
                    temperature=0.2,
                )
                narrative = (resp.choices[0].message.content or "").strip()
            except Exception:
                narrative = ""
        else:
            heuristic = _heuristic_summary(session)
            narrative = heuristic["narrative"]

        if not narrative:
            continue

        structured = {
            "narrative": narrative,
            "topics": [],
            "projects": [],
            "files_touched": [],
            "decisions": [],
            "problems": [],
            "outcomes": [],
        }
        _save_insight(today_str, session, structured, consolidation_type="micro")
        created += 1

    if created:
        logger.info(f"Micro-consolidation: {created} micro-insight(s) created")


def run_weekly_rollup() -> None:
    """
    Weekly rollup: summarize the past 7 days of daily insights
    into a single week-level narrative. Runs once per week.
    """
    cfg = _load_config()
    result = _get_llm_client(cfg)
    if not result:
        logger.debug("Weekly rollup: no LLM available, skipping")
        return

    client, model, _ = result
    now = datetime.utcnow()
    week_start = (now - timedelta(days=7)).date().isoformat()

    daily_insights = metadata_db.fetch_recent_insights(days=7)
    daily_only = [
        i for i in daily_insights
        if i["consolidation_type"] in ("daily", None, "")
    ]

    if len(daily_only) < 2:
        logger.debug("Weekly rollup: not enough daily insights, skipping")
        return

    summaries = []
    for ins in daily_only:
        narrative = ins["narrative"] or ins["summary"] or ""
        if narrative:
            summaries.append(f"[{ins['date']}] {narrative[:300]}")

    prompt = (
        "Summarize this week's work sessions into a 4-6 sentence narrative. "
        "Identify key themes, projects progressed, recurring topics, and overall focus.\n\n"
        + "\n".join(summaries)
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.2,
        )
        weekly_narrative = (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.debug(f"Weekly rollup LLM failed: {exc}")
        return

    if not weekly_narrative:
        return

    all_topics: list[str] = []
    all_projects: list[str] = []
    for ins in daily_only:
        try:
            all_topics.extend(json.loads(ins["topics_structured"] or "[]"))
            all_projects.extend(json.loads(ins["projects"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            pass

    topics_deduped = list(dict.fromkeys(all_topics))[:10]
    projects_deduped = list(dict.fromkeys(all_projects))[:10]

    structured = {
        "narrative": weekly_narrative,
        "topics": topics_deduped,
        "projects": projects_deduped,
        "files_touched": [],
        "decisions": [],
        "problems": [],
        "outcomes": [],
    }

    insight_id = str(uuid.uuid4())
    week_end = now.date().isoformat()

    metadata_db.insert_insight(
        insight_id=insight_id,
        date=week_end,
        session_start=week_start,
        session_end=week_end,
        summary=weekly_narrative[:500],
        topics=json.dumps(topics_deduped),
        narrative=weekly_narrative,
        topics_structured=json.dumps(topics_deduped),
        projects=json.dumps(projects_deduped),
        consolidation_type="weekly",
    )

    embedding = embedder.embed_text(weekly_narrative)
    if embedding:
        vector_db.upsert_insight(
            doc_id=insight_id,
            embedding=embedding,
            insight_id=insight_id,
            date=week_end,
            summary_preview=weekly_narrative[:300],
            topics=json.dumps(topics_deduped),
        )

    logger.info(f"Weekly rollup: created week-level insight for {week_start}..{week_end}")
