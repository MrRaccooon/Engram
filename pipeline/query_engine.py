"""
Query understanding engine for the Ask pipeline.

Extracts structured signals from natural-language questions before
retrieval, so the system can route to the right data sources:

  - Temporal parsing:  "last Tuesday" → date range filter
  - App detection:     "in VS Code"   → app_name filter
  - Entity extraction: "about Engram" → tag search
  - Intent:            "what did I do" vs "where is" vs "how much time"
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger


@dataclass
class ParsedQuery:
    raw: str
    cleaned: str = ""
    intent: str = "recall"  # recall | locate | person | temporal | activity
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    app_filters: list[str] = field(default_factory=list)
    entity_filters: list[str] = field(default_factory=list)
    has_temporal: bool = False


# ── Temporal parsing ──────────────────────────────────────────────────────────

_WEEKDAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3,
    "thurs": 3, "fri": 4, "sat": 5, "sun": 6,
}

_RELATIVE_DAY = re.compile(
    r"\b(today|yesterday|day before yesterday)\b", re.IGNORECASE,
)

_LAST_WEEKDAY = re.compile(
    r"\blast\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b",
    re.IGNORECASE,
)

_N_DAYS_AGO = re.compile(
    r"\b(\d+)\s+days?\s+ago\b", re.IGNORECASE,
)

_THIS_LAST_WEEK = re.compile(
    r"\b(this|last)\s+week\b", re.IGNORECASE,
)

_TIME_OF_DAY = re.compile(
    r"\b(this\s+morning|this\s+afternoon|this\s+evening|last\s+night|tonight)\b",
    re.IGNORECASE,
)

_EARLIER_RECENTLY = re.compile(
    r"\b(earlier\s+today|earlier|recently|a\s+while\s+ago|couple\s+(?:of\s+)?days?\s+ago)\b",
    re.IGNORECASE,
)


def _today() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _date_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _parse_temporal(query: str) -> tuple[Optional[str], Optional[str]]:
    """Extract date_from / date_to from natural language temporal references."""
    today = _today()
    q = query.lower()

    m = _RELATIVE_DAY.search(q)
    if m:
        word = m.group(1).lower()
        if word == "today":
            return _date_str(today), _date_str(today)
        elif word == "yesterday":
            d = today - timedelta(days=1)
            return _date_str(d), _date_str(d)
        elif word == "day before yesterday":
            d = today - timedelta(days=2)
            return _date_str(d), _date_str(d)

    m = _LAST_WEEKDAY.search(q)
    if m:
        target = _WEEKDAY_MAP.get(m.group(1).lower())
        if target is not None:
            current_wd = today.weekday()
            diff = (current_wd - target) % 7
            if diff == 0:
                diff = 7
            d = today - timedelta(days=diff)
            return _date_str(d), _date_str(d)

    m = _N_DAYS_AGO.search(q)
    if m:
        n = int(m.group(1))
        d = today - timedelta(days=n)
        return _date_str(d), _date_str(d)

    m = _THIS_LAST_WEEK.search(q)
    if m:
        which = m.group(1).lower()
        if which == "this":
            start = today - timedelta(days=today.weekday())
            return _date_str(start), _date_str(today)
        else:
            start = today - timedelta(days=today.weekday() + 7)
            end = start + timedelta(days=6)
            return _date_str(start), _date_str(end)

    m = _EARLIER_RECENTLY.search(q)
    if m:
        word = m.group(1).lower().strip()
        if "earlier" in word:
            return _date_str(today), _date_str(today)
        elif "couple" in word:
            return _date_str(today - timedelta(days=3)), _date_str(today)
        else:
            return _date_str(today - timedelta(days=3)), _date_str(today)

    m = _TIME_OF_DAY.search(q)
    if m:
        period = m.group(1).lower()
        if "morning" in period:
            return _date_str(today), _date_str(today)
        elif "afternoon" in period:
            return _date_str(today), _date_str(today)
        elif "evening" in period or "tonight" in period:
            return _date_str(today), _date_str(today)
        elif "last night" in period:
            d = today - timedelta(days=1)
            return _date_str(d), _date_str(today)

    return None, None


# ── App detection ─────────────────────────────────────────────────────────────

_APP_ALIASES: dict[str, list[str]] = {
    "Code.exe": ["vs code", "vscode", "visual studio code", "code editor"],
    "Code - Insiders": ["vs code insiders", "code insiders"],
    "chrome.exe": ["chrome", "google chrome", "browser"],
    "firefox.exe": ["firefox", "mozilla"],
    "msedge.exe": ["edge", "microsoft edge"],
    "WindowsTerminal.exe": ["terminal", "windows terminal", "wt"],
    "cmd.exe": ["command prompt", "cmd"],
    "powershell.exe": ["powershell", "pwsh"],
    "explorer.exe": ["file explorer", "explorer"],
    "Obsidian.exe": ["obsidian"],
    "Notion.exe": ["notion"],
    "slack.exe": ["slack"],
    "discord.exe": ["discord"],
    "Cursor.exe": ["cursor"],
    "notepad.exe": ["notepad"],
    "notepad++.exe": ["notepad++", "npp"],
}


def _detect_apps(query: str) -> list[str]:
    q = query.lower()
    matched = []
    for exe, aliases in _APP_ALIASES.items():
        for alias in aliases:
            if alias in q:
                matched.append(exe)
                break
    return matched


# ── Intent classification ─────────────────────────────────────────────────────

_INTENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("activity", re.compile(r"\bhow\s+much\s+time\b|\bhow\s+long\b|\btime\s+spent\b", re.I)),
    ("person", re.compile(r"\bwho\b", re.I)),
    ("temporal", re.compile(r"\bwhen\b|\bwhat\s+time\b", re.I)),
    ("locate", re.compile(r"\bwhere\s+is\b|\bfind\b|\blocate\b|\bwhich\s+file\b", re.I)),
    ("recall", re.compile(r"\bwhat\b|\bremind\b|\bremember\b|\btell\s+me\b|\bshow\b", re.I)),
]


def _classify_intent(query: str) -> str:
    for intent, pattern in _INTENT_PATTERNS:
        if pattern.search(query):
            return intent
    return "recall"


# ── Entity extraction ─────────────────────────────────────────────────────────

def _extract_entities(query: str, known_tags: list[str]) -> list[str]:
    """Match query tokens against known tags from capture_tags."""
    if not known_tags:
        return []
    q_lower = query.lower()
    matched = []
    for tag in known_tags:
        if tag.lower() in q_lower and len(tag) > 2:
            matched.append(tag)
    return matched


# ── Public API ────────────────────────────────────────────────────────────────

def parse_query(query: str, known_tags: list[str] | None = None) -> ParsedQuery:
    """
    Parse a user question into structured retrieval signals.

    Args:
        query: The raw user question.
        known_tags: Optional list of tags from capture_tags for entity matching.

    Returns:
        ParsedQuery with temporal, app, entity, and intent signals.
    """
    date_from, date_to = _parse_temporal(query)
    apps = _detect_apps(query)
    intent = _classify_intent(query)
    entities = _extract_entities(query, known_tags or [])

    parsed = ParsedQuery(
        raw=query,
        cleaned=query,
        intent=intent,
        date_from=date_from,
        date_to=date_to,
        app_filters=apps,
        entity_filters=entities,
        has_temporal=date_from is not None,
    )

    logger.debug(
        f"QueryEngine: intent={parsed.intent} temporal={parsed.has_temporal} "
        f"dates={date_from}..{date_to} apps={apps} entities={entities}"
    )

    return parsed
