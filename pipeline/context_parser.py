"""
Rich context extraction from window titles and app metadata.

Parses structured signals from raw strings like:
  "embedder.py - Engram - Cursor"       → file=embedder.py, project=Engram, editor=cursor
  "Stack Overflow - How to fix CLIP"    → type=research, question="How to fix CLIP"
  "GitHub - Engram/pipeline/worker.py"  → type=code_review, repo=Engram

These structured signals power:
  1. Richer searchable text per capture (worker.py) — so "what am I working on" works
  2. Project / entity memory accumulation
  3. Real-time session understanding for Ask context
"""

from __future__ import annotations

import re
from typing import Any

_EDITOR_APPS = {
    "cursor.exe", "code.exe", "cursor", "code",
    "pycharm64.exe", "idea64.exe", "nvim", "vim", "sublime_text.exe",
}
_BROWSER_APPS = {
    "brave.exe", "chrome.exe", "firefox.exe", "msedge.exe",
    "brave", "chrome", "firefox", "safari",
}
_TERMINAL_APPS = {
    "windowsterminal.exe", "cmd.exe", "powershell.exe", "wt.exe",
    "bash", "zsh", "iterm2", "alacritty", "wezterm.exe",
}
_COMM_APPS = {
    "slack.exe", "discord.exe", "teams.exe", "whatsapp.exe",
    "slack", "discord", "telegram.exe",
}

_EDITOR_DISPLAY_NAMES = {"cursor", "visual studio code", "code", "pycharm", "intellij idea", "vim", "nvim"}


def parse_window(window_title: str, app_name: str) -> dict[str, Any]:
    """
    Extract structured context from window title + app name.

    Returns a dict with:
      app_category  — 'editor' | 'browser' | 'terminal' | 'communication' | 'other'
      file          — filename being edited (e.g. "embedder.py")
      project       — project/repo name (e.g. "Engram")
      editor        — cleaned editor name (e.g. "cursor")
      type          — sub-type for browsers: 'research' | 'code_review' | 'documentation' | 'video'
      question      — research question from Stack Overflow / Reddit title
      repo          — GitHub/GitLab repository path
      domain        — website domain
      terminal_cmd  — visible command in terminal title
      rich_text     — human-readable enriched description for indexing
    """
    ctx: dict[str, Any] = {
        "app_category": _categorize_app(app_name),
        "file": None,
        "project": None,
        "editor": None,
        "type": None,
        "question": None,
        "repo": None,
        "domain": None,
        "terminal_cmd": None,
        "rich_text": "",
    }

    app_lower = (app_name or "").lower().replace(".exe", "")
    title = (window_title or "").strip()

    cat = ctx["app_category"]

    if cat == "editor":
        ctx["editor"] = app_lower
        _parse_editor_title(title, ctx)

    elif cat == "browser":
        ctx["type"] = "browsing"
        _parse_browser_title(title, ctx)

    elif cat == "terminal":
        ctx["type"] = "terminal"
        _parse_terminal_title(title, ctx)

    elif cat == "communication":
        ctx["type"] = "communication"

    ctx["rich_text"] = _build_rich_text(ctx, title, app_name)
    return ctx


def _categorize_app(app_name: str) -> str:
    low = (app_name or "").lower()
    if any(kw in low for kw in ("cursor", "code", "pycharm", "idea", "vim", "nvim", "sublime")):
        return "editor"
    if any(kw in low for kw in ("brave", "chrome", "firefox", "edge", "safari")):
        return "browser"
    if any(kw in low for kw in ("terminal", "cmd", "powershell", "bash", "zsh", "wt", "alacritty", "wezterm")):
        return "terminal"
    if any(kw in low for kw in ("slack", "discord", "teams", "whatsapp", "telegram")):
        return "communication"
    return "other"


def _parse_editor_title(title: str, ctx: dict) -> None:
    """
    VS Code / Cursor patterns:
      "embedder.py - Engram - Cursor"
      "● worker.py — /path/to/Engram — Visual Studio Code"
      "main.py · some-project"
    """
    # Strip unsaved-file indicator
    title = re.sub(r"^[●•·]\s*", "", title).strip()

    # Split on common separators
    parts = re.split(r"\s*[-—–·]\s*", title)

    if len(parts) >= 2:
        file_part = parts[0].strip()
        project_part = parts[1].strip()

        # Looks like a filename: has extension, not too long, not a path
        if re.search(r"\.\w{1,6}$", file_part) and len(file_part) < 80 and not file_part.startswith(("/", "\\")):
            ctx["file"] = file_part

        # Project: reject if it's just the editor name
        if project_part.lower() not in _EDITOR_DISPLAY_NAMES and len(project_part) < 60:
            # If it looks like a path, take the last segment
            if "/" in project_part or "\\" in project_part:
                project_part = re.split(r"[/\\]", project_part.rstrip("/\\"))[-1]
            if project_part:
                ctx["project"] = project_part

    # Fallback: scan the full title for a filename
    if not ctx["file"]:
        m = re.search(r"\b([a-zA-Z_][a-zA-Z0-9_]*\.[a-z]{1,6})\b", title)
        if m:
            ctx["file"] = m.group(1)


def _parse_browser_title(title: str, ctx: dict) -> None:
    """
    Browser title patterns:
      "How to fix CLIP meta tensor - Stack Overflow"
      "Engram/pipeline/worker.py at main · GitHub"
      "os.path — Python 3 documentation"
      "How to install PyTorch - YouTube"
    """
    title_lower = title.lower()

    # GitHub / GitLab repo or file
    gh_match = re.search(
        r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:\s+at\s+\S+)?\s*[·|–-]?\s*(?:GitHub|GitLab)",
        title, re.IGNORECASE,
    )
    if gh_match:
        ctx["type"] = "code_review"
        ctx["repo"] = gh_match.group(1)
        ctx["project"] = gh_match.group(1).split("/")[-1]
        return

    # Stack Overflow / Reddit / Forums — research question
    forum_match = re.match(
        r"(.+?)\s*[-–|]\s*(Stack Overflow|Reddit|Hacker News|PyTorch Forums|"
        r"GitHub Issues|GitHub Discussions|Super User|Ask Ubuntu|Dev\.to)",
        title, re.IGNORECASE,
    )
    if forum_match:
        ctx["type"] = "research"
        ctx["question"] = forum_match.group(1).strip()[:150]
        return

    # Python / MDN / Library docs
    if any(kw in title_lower for kw in ("documentation", "docs —", "api reference", "— python", "mdn web", "read the docs")):
        ctx["type"] = "documentation"
        return

    # YouTube
    if "youtube" in title_lower:
        ctx["type"] = "video"
        ctx["question"] = title.replace(" - YouTube", "").strip()[:100]
        return

    # Generic: extract domain keyword from title
    domain_m = re.search(r"\b([a-zA-Z0-9-]+\.(com|io|org|dev|ai|net|app|py|sh))\b", title)
    if domain_m:
        ctx["domain"] = domain_m.group(1)


def _parse_terminal_title(title: str, ctx: dict) -> None:
    """Windows Terminal often shows: 'powershell' or 'cmd - python main.py'."""
    cmd_m = re.search(r"[-:]\s*(.+)$", title)
    if cmd_m:
        cmd = cmd_m.group(1).strip()
        if len(cmd) < 120:
            ctx["terminal_cmd"] = cmd


def _build_rich_text(ctx: dict, original_title: str, app_name: str) -> str:
    """Convert structured context into a searchable natural-language description."""
    cat = ctx.get("app_category", "other")
    parts: list[str] = []

    if cat == "editor":
        file_ = ctx.get("file")
        proj = ctx.get("project")
        editor = ctx.get("editor", app_name)
        if file_ and proj:
            parts.append(f"Editing {file_} in project {proj}")
        elif file_:
            parts.append(f"Editing {file_}")
        elif proj:
            parts.append(f"Working in project {proj} in {editor}")
        else:
            parts.append(f"Using code editor {editor}")

    elif cat == "browser":
        btype = ctx.get("type", "browsing")
        if btype == "code_review" and ctx.get("repo"):
            parts.append(f"Viewing code repository {ctx['repo']} on GitHub")
        elif btype == "research" and ctx.get("question"):
            parts.append(f"Researching: {ctx['question']}")
        elif btype == "documentation":
            parts.append(f"Reading documentation: {original_title[:80]}")
        elif btype == "video" and ctx.get("question"):
            parts.append(f"Watching video: {ctx['question']}")
        else:
            parts.append(f"Browsing: {original_title[:100]}")

    elif cat == "terminal":
        if ctx.get("terminal_cmd"):
            parts.append(f"Running terminal command: {ctx['terminal_cmd']}")
        else:
            parts.append(f"Using terminal: {original_title[:60]}")

    elif cat == "communication":
        clean = (app_name or "").lower().replace(".exe", "")
        parts.append(f"Using {clean}: {original_title[:60]}")

    else:
        if original_title:
            parts.append(original_title[:100])

    return " | ".join(parts) if parts else (original_title or app_name or "")


def extract_project_name(ctx: dict) -> str | None:
    """Return the most salient project/repo name from a parsed context dict."""
    return ctx.get("project") or ctx.get("repo")
