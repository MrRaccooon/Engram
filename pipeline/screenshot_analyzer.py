"""
Screenshot content analyzer.

Turns raw OCR text into structured, document-quality context by detecting
what type of content is visible and extracting meaning from it.

Instead of storing "def _get_clip():\n    global _clip\n    if _clip_br...",
we store:
  "Code: Python file. Functions visible: _get_clip. Patterns: error handling,
   global state, conditional branching. Keywords: _clip_broken, RuntimeError,
   logger.error, model initialization."

Content types detected:
  - code       → Python/JS/TS/etc. Extract functions, classes, imports, patterns
  - terminal   → Commands + output. Extract commands run, errors, exit codes
  - browser    → Web page. Extract what kind of page, topic, key terms
  - document   → Text/markdown. Extract headings, key sentences
  - mixed      → Multiple content types visible (split screen, IDE with terminal)

This runs locally, zero API cost, and fires per-screenshot after OCR.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ── Content type detection ────────────────────────────────────────────────────

@dataclass
class ScreenContext:
    content_type: str                    # code | terminal | browser | document | mixed | unknown
    language: Optional[str] = None      # python | javascript | typescript | ...
    functions: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)   # "error handling", "async", "loop", etc.
    summary: str = ""                    # human-readable document-style summary


# ── Language detection ────────────────────────────────────────────────────────

_LANG_SIGNALS: list[tuple[str, list[str]]] = [
    ("python",     ["def ", "import ", "from ", "class ", "if __name__", "self.", "    def ", "async def "]),
    ("javascript", ["const ", "let ", "var ", "=>", "function ", "require(", "module.exports"]),
    ("typescript", ["interface ", ": string", ": number", ": boolean", "as string", "Type<"]),
    ("bash",       ["#!/bin/bash", "#!/bin/sh", "echo ", "export ", "| grep", "| awk"]),
    ("sql",        ["SELECT ", "FROM ", "WHERE ", "INSERT INTO", "CREATE TABLE", "JOIN "]),
    ("yaml",       ["---\n", ": true", ": false", "  - ", "name: "]),
    ("json",       ['": "', '": {', '": [', '"type":', '"name":']),
    ("html",       ["<html", "<div", "<body", "<head", "</", "<!DOCTYPE"]),
    ("css",        ["{", "margin:", "padding:", "display:", "font-size:", "@media"]),
]


def _detect_language(text: str) -> Optional[str]:
    scores: dict[str, int] = {}
    for lang, signals in _LANG_SIGNALS:
        score = sum(1 for s in signals if s in text)
        if score > 0:
            scores[lang] = score
    return max(scores, key=lambda k: scores[k]) if scores else None


# ── Content type detection ─────────────────────────────────────────────────────

_TERMINAL_SIGNALS = [
    r"^\$\s", r"^>\s", r"^PS\s+\S+>", r"^C:\\",  # prompts
    r"Traceback \(most recent call last\)",
    r"\bError:\s", r"\bWarning:\s",
    r"pip install", r"npm install", r"python\s+\w+\.py",
    r"\d+\.\d+\.\d+",  # version numbers common in terminal
    r"^\s*(Running|Building|Compiling|Installing|Collecting)",
    r"exit code \d+", r"Process finished",
]

_CODE_SIGNALS = [
    r"\bdef \w+\(", r"\bclass \w+[:(]",
    r"\bimport \w+", r"\bfrom \w+ import",
    r"\bconst \w+ =", r"\bfunction \w+\(",
    r"//\s+\w+", r"#\s+\w+",  # code comments
    r"\breturn\s+", r"\bif\s+\w+",
    r"    \w+",  # indented code
]

_BROWSER_SIGNALS = [
    r"https?://", r"Stack Overflow", r"GitHub", r"MDN",
    r"Documentation", r"npm\s+package", r"\bREADME\b",
    r"Search results", r"Sign in", r"Log in",
]


def _detect_content_type(text: str, window_title: str = "", app_name: str = "") -> str:
    app_lower = (app_name or "").lower()
    title_lower = (window_title or "").lower()

    # Terminal apps always produce terminal content
    _TERMINAL_APPS = ("terminal", "cmd", "powershell", "bash", "wt", "conhost", "alacritty")
    if any(k in app_lower for k in _TERMINAL_APPS):
        return "terminal"

    # Browser apps
    _BROWSER_APPS = ("brave", "chrome", "firefox", "edge", "safari", "opera", "vivaldi")
    if any(k in app_lower for k in _BROWSER_APPS):
        return "browser"

    # Score each type
    term_score = sum(1 for p in _TERMINAL_SIGNALS if re.search(p, text, re.MULTILINE))
    code_score = sum(1 for p in _CODE_SIGNALS if re.search(p, text, re.MULTILINE))
    browser_score = sum(1 for p in _BROWSER_SIGNALS if re.search(p, text, re.IGNORECASE))

    scores = {"terminal": term_score, "code": code_score, "browser": browser_score}
    best = max(scores, key=lambda k: scores[k])

    # Need at least 2 signals to be confident
    if scores[best] < 2:
        return "document"

    # If code and terminal are both high, it's probably an IDE with a terminal panel
    if code_score >= 3 and term_score >= 2:
        return "mixed"

    return best


# ── Code extractor ────────────────────────────────────────────────────────────

_CODE_PATTERNS = {
    "error handling": [r"\btry\b", r"\bexcept\b", r"\bcatch\b", r"\bfinally\b", r"\braise\b"],
    "async": [r"\basync\b", r"\bawait\b", r"\bPromise\b", r"\basyncio\b"],
    "loops": [r"\bfor\b.+\bin\b", r"\bwhile\b", r"\.forEach\b", r"\.map\("],
    "class definition": [r"\bclass\s+\w+"],
    "decorators": [r"@\w+"],
    "type hints": [r":\s*(str|int|float|bool|list|dict|Optional|Any|Union)\b"],
    "imports": [r"\bimport\b", r"\bfrom\b.+\bimport\b"],
    "logging": [r"\blogger\.", r"\blogging\.", r"console\.log", r"print\("],
    "database": [r"\bSELECT\b", r"\bINSERT\b", r"\.execute\(", r"\.query\("],
    "testing": [r"\bassert\b", r"\btest_\w+", r"\bpytest\b", r"\bdescribe\(", r"\bit\("],
    "api call": [r"\brequests\.\b", r"\bfetch\(", r"\baxios\.", r"\bhttpx\b"],
    "file operations": [r"\bopen\(", r"\.read\(", r"\.write\(", r"\bPath\("],
}


def _extract_code_context(text: str) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    """Extract functions, classes, imports, keywords, patterns from code text."""
    functions = list(dict.fromkeys(re.findall(r"(?:def|function|async def)\s+(\w+)\s*\(", text)))[:8]
    classes = list(dict.fromkeys(re.findall(r"class\s+(\w+)\s*[:(]", text)))[:5]

    raw_imports = re.findall(r"(?:import\s+(\S+)|from\s+(\S+)\s+import)", text)
    imports = list(dict.fromkeys(
        (m[0] or m[1]).split(".")[0] for m in raw_imports if (m[0] or m[1])
    ))[:6]

    # Extract notable identifiers (all-caps constants, camelCase, snake_case)
    identifiers = re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]{3,})\b", text)
    # Filter out Python keywords and common noise
    _NOISE = {
        "self", "True", "False", "None", "return", "import", "from", "class",
        "def", "async", "await", "else", "elif", "pass", "with", "lambda",
        "global", "raise", "except", "finally", "yield", "break", "continue",
        "print", "type", "list", "dict", "str", "int", "bool",
    }
    unique_ids = list(dict.fromkeys(w for w in identifiers if w not in _NOISE))[:10]

    patterns: list[str] = []
    for label, regexes in _CODE_PATTERNS.items():
        if any(re.search(r, text) for r in regexes):
            patterns.append(label)

    return functions, classes, imports, unique_ids, patterns


# ── Terminal extractor ─────────────────────────────────────────────────────────

def _extract_terminal_context(text: str) -> tuple[list[str], list[str]]:
    """Extract commands run and errors/warnings from terminal output."""
    commands: list[str] = []
    errors: list[str] = []

    for line in text.split("\n"):
        stripped = line.strip()

        # Detect shell prompt lines
        if re.match(r"^(\$|>|PS\s+\S+>|C:\\[^>]+>)\s+", stripped):
            cmd = re.sub(r"^(\$|>|PS\s+\S+>|C:\\[^>]+>)\s+", "", stripped).strip()
            if cmd and len(cmd) > 2:
                commands.append(cmd[:100])

        # Detect error lines
        err_match = re.search(
            r"((?:[A-Z][a-zA-Z]+Error|Exception|FAILED|ERROR|err!|Error:)[^\n]{0,120})",
            stripped, re.IGNORECASE,
        )
        if err_match:
            errors.append(err_match.group(1).strip()[:120])

    return commands[:6], list(dict.fromkeys(errors))[:4]


# ── Document extractor ────────────────────────────────────────────────────────

def _extract_document_context(text: str) -> tuple[list[str], list[str]]:
    """Extract headings and key terms from document/browser content."""
    headings: list[str] = []
    # Markdown headings
    for match in re.finditer(r"^#{1,3}\s+(.+)$", text, re.MULTILINE):
        headings.append(match.group(1).strip())
    # Title-case lines (common for page headings, documentation titles)
    for line in text.split("\n"):
        stripped = line.strip()
        if (
            4 < len(stripped) < 80
            and stripped[0].isupper()
            and not stripped.endswith((".", ",", ";"))
            and stripped.count(" ") < 10
            and not any(c in stripped for c in ("=", "{", "}", "(", ")"))
        ):
            headings.append(stripped)

    unique_headings = list(dict.fromkeys(headings))[:5]

    # Key noun phrases (crude: capitalized multi-word sequences)
    noun_phrases = re.findall(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)+)\b", text)
    unique_np = list(dict.fromkeys(noun_phrases))[:8]

    return unique_headings, unique_np


# ── Summary builder ───────────────────────────────────────────────────────────

def _build_summary(ctx: ScreenContext, window_title: str, app_name: str) -> str:
    """Build a human-readable, document-style description of the screenshot."""
    parts: list[str] = []
    app_clean = (app_name or "").replace(".exe", "")

    if ctx.content_type == "code":
        lang = ctx.language or "code"
        parts.append(f"Code editor ({lang})")
        if ctx.functions:
            parts.append(f"Functions visible: {', '.join(ctx.functions)}")
        if ctx.classes:
            parts.append(f"Classes: {', '.join(ctx.classes)}")
        if ctx.imports:
            parts.append(f"Using: {', '.join(ctx.imports)}")
        if ctx.patterns:
            parts.append(f"Patterns: {', '.join(ctx.patterns)}")
        if ctx.keywords:
            parts.append(f"Identifiers: {', '.join(ctx.keywords[:6])}")
        if ctx.errors:
            parts.append(f"ERRORS: {' | '.join(ctx.errors)}")

    elif ctx.content_type == "terminal":
        parts.append(f"Terminal ({app_clean})")
        if ctx.commands:
            parts.append(f"Commands: {' → '.join(ctx.commands)}")
        if ctx.errors:
            parts.append(f"ERRORS: {' | '.join(ctx.errors)}")

    elif ctx.content_type == "browser":
        parts.append(f"Browser ({app_clean}): {window_title[:80]}")
        if ctx.headings:
            parts.append(f"Content: {', '.join(ctx.headings[:3])}")
        if ctx.keywords:
            parts.append(f"Topics: {', '.join(ctx.keywords[:5])}")
        if ctx.errors:
            parts.append(f"HTTP errors: {' | '.join(ctx.errors)}")

    elif ctx.content_type == "mixed":
        parts.append("IDE with terminal panel")
        if ctx.functions:
            parts.append(f"Code: {', '.join(ctx.functions[:3])}")
        if ctx.errors:
            parts.append(f"ERRORS: {' | '.join(ctx.errors)}")
        if ctx.commands:
            parts.append(f"Commands: {', '.join(ctx.commands[:2])}")

    else:
        parts.append(f"{app_clean}: {window_title[:80]}")
        if ctx.headings:
            parts.append(f"Content: {', '.join(ctx.headings[:3])}")

    return " | ".join(parts)


# ── Public API ────────────────────────────────────────────────────────────────

def analyze(
    ocr_text: str,
    window_title: str = "",
    app_name: str = "",
) -> ScreenContext:
    """
    Analyze OCR text from a screenshot and return a ScreenContext with
    structured understanding of what was visible.
    """
    if not ocr_text or not ocr_text.strip():
        return ScreenContext(content_type="unknown", summary="")

    content_type = _detect_content_type(ocr_text, window_title, app_name)
    ctx = ScreenContext(content_type=content_type)

    if content_type in ("code", "mixed"):
        ctx.language = _detect_language(ocr_text)
        ctx.functions, ctx.classes, ctx.imports, ctx.keywords, ctx.patterns = (
            _extract_code_context(ocr_text)
        )
        # Also extract any terminal errors from the mixed view
        cmds, errs = _extract_terminal_context(ocr_text)
        if content_type == "mixed":
            ctx.commands = cmds
        ctx.errors.extend(errs)

    elif content_type == "terminal":
        ctx.commands, ctx.errors = _extract_terminal_context(ocr_text)

    elif content_type == "browser":
        ctx.headings, ctx.keywords = _extract_document_context(ocr_text)
        # Check for HTTP errors in the page
        http_errs = re.findall(r"\b[45]\d{2}\s+[A-Za-z ]+", ocr_text)
        ctx.errors = http_errs[:3]

    else:  # document
        ctx.headings, ctx.keywords = _extract_document_context(ocr_text)

    ctx.summary = _build_summary(ctx, window_title, app_name)
    return ctx


def to_searchable_text(ctx: ScreenContext, raw_ocr: str = "") -> str:
    """
    Combine the structured context summary with the raw OCR for maximum
    searchability. The summary provides document-quality signal; the raw
    OCR ensures nothing is missed.
    """
    parts: list[str] = []

    if ctx.summary:
        parts.append(ctx.summary)

    # Include errors prominently — they're high-signal
    if ctx.errors:
        parts.append("ERRORS: " + " | ".join(ctx.errors))

    # Include raw OCR but truncated (the summary is primary)
    if raw_ocr.strip():
        parts.append(raw_ocr[:800])

    return "\n".join(parts)
