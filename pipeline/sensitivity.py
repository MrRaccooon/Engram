"""
Sensitivity classifier for the privacy pipeline.

Scores each retrieved capture chunk 0.0 (safe) → 1.0 (never send).
Chunks at or above the configured threshold are silently dropped before
any data reaches an external API.

Two layers of detection:
  1. Hard-block rules — always return 1.0 regardless of threshold
     (password managers, banking domains, credential patterns)
  2. Soft-score rules — cumulative score from regex pattern matches

No external dependencies; runs entirely in-process on the CPU.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from loguru import logger

# ── Hard-block: these apps always produce score 1.0 ─────────────────────────

_HARD_BLOCK_APPS = {
    "1password", "1password.exe",
    "keepass", "keepass.exe", "keepassxc", "keepassxc.exe",
    "bitwarden", "bitwarden.exe",
    "lastpass", "lastpass.exe",
    "dashlane", "dashlane.exe",
    "kwallet", "kwallet5",
    "enpass", "enpass.exe",
    "roboform", "roboform.exe",
}

_HARD_BLOCK_DOMAIN_PATTERNS = [
    "*.bank.*", "*.banking.*",
    "bankofamerica.com", "chase.com", "wellsfargo.com",
    "citibank.com", "capitalone.com", "barclays.com",
    "hsbc.com", "lloydsbank.com", "natwest.com",
    "paypal.com", "venmo.com",
    "irs.gov", "*.gov",
    "localhost", "127.0.0.1",
]

# ── Soft-score: each match adds to the cumulative score ─────────────────────

_SCORED_PATTERNS: list[tuple[float, str]] = [
    # Credit / debit card numbers
    (0.8, r"\b(?:\d[ -]?){13,16}\b"),
    # US Social Security Number
    (0.9, r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b"),
    # Indian Aadhaar number
    (0.9, r"\b\d{4}\s\d{4}\s\d{4}\b"),
    # Passwords / secrets in text
    (0.6, r"\b(?:password|passwd|secret|api[_\s]?key|token|bearer|private[_\s]?key)\b"),
    # Financial amounts with currency symbols
    (0.3, r"(?:₹|\$|€|£|¥)\s*[\d,]+(?:\.\d+)?"),
    # Salary / compensation patterns
    (0.4, r"\b(?:salary|ctc|compensation|package|lpa|lakh|crore)\b"),
    # Email addresses
    (0.3, r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    # Phone numbers (various formats)
    (0.3, r"\b(?:\+\d{1,3}[\s-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b"),
    # Passport / ID numbers (generic alphanumeric 8-12 chars)
    (0.4, r"\b[A-Z]{1,2}\d{6,9}\b"),
    # SSH / PGP / JWT-like long base64 strings
    (0.7, r"[A-Za-z0-9+/]{40,}={0,2}"),
    # Private key headers
    (0.95, r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    # Environment variable assignments with values
    (0.5, r"[A-Z_]{3,}=(?:\"[^\"]{8,}\"|'[^']{8,}'|\S{8,})"),
]

_COMPILED: list[tuple[float, re.Pattern]] = [
    (score, re.compile(pattern, re.IGNORECASE))
    for score, pattern in _SCORED_PATTERNS
]


def _is_hard_blocked(chunk: dict[str, Any], extra_excluded_apps: list[str],
                     extra_excluded_domains: list[str]) -> bool:
    app = (chunk.get("app_name") or "").lower().strip()
    url = (chunk.get("url") or "").lower().strip()

    all_blocked_apps = _HARD_BLOCK_APPS | {a.lower() for a in extra_excluded_apps}
    if any(app == blocked or app.startswith(blocked) for blocked in all_blocked_apps):
        return True

    all_blocked_domains = _HARD_BLOCK_DOMAIN_PATTERNS + extra_excluded_domains
    for pattern in all_blocked_domains:
        if fnmatch(url, pattern):
            return True
        hostname = url.split("/")[0].split("?")[0]
        if fnmatch(hostname, pattern):
            return True

    return False


def score(
    chunk: dict[str, Any],
    excluded_apps: list[str] | None = None,
    excluded_domains: list[str] | None = None,
) -> float:
    """
    Return a sensitivity score in [0.0, 1.0] for a retrieved chunk dict.

    Args:
        chunk: dict with at least 'content_preview', 'app_name', 'url' keys.
        excluded_apps: additional app names from config to hard-block.
        excluded_domains: additional domain patterns from config to hard-block.

    Returns:
        1.0 for hard-blocked chunks, cumulative score (capped at 1.0) otherwise.
    """
    excluded_apps = excluded_apps or []
    excluded_domains = excluded_domains or []

    if _is_hard_blocked(chunk, excluded_apps, excluded_domains):
        logger.debug(
            f"Sensitivity: hard-blocked chunk from app='{chunk.get('app_name')}' "
            f"url='{chunk.get('url', '')[:60]}'"
        )
        return 1.0

    content = (chunk.get("content_preview") or "")
    cumulative = 0.0

    for pattern_score, regex in _COMPILED:
        if regex.search(content):
            cumulative += pattern_score
            if cumulative >= 1.0:
                break

    return min(cumulative, 1.0)


def filter_chunks(
    chunks: list[dict[str, Any]],
    threshold: float = 0.4,
    excluded_apps: list[str] | None = None,
    excluded_domains: list[str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """
    Filter a list of retrieved chunks by sensitivity score.

    Returns:
        (passing_chunks, blocked_count)
    """
    passing = []
    blocked = 0

    for chunk in chunks:
        s = score(chunk, excluded_apps, excluded_domains)
        if s >= threshold:
            blocked += 1
            logger.debug(
                f"Sensitivity: blocked chunk score={s:.2f} "
                f"source={chunk.get('source_type')} ts={chunk.get('timestamp', '')[:19]}"
            )
        else:
            chunk = {**chunk, "_sensitivity_score": round(s, 3)}
            passing.append(chunk)

    if blocked:
        logger.info(f"Sensitivity filter: {blocked}/{len(chunks)} chunks blocked before API call")

    return passing, blocked
