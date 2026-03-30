"""
Git diff collector.

When a file in a git-tracked repo is saved, this captures a structured
description of exactly what changed — not just the whole file content.

Example output for a capture:
  "Changes to embedder.py in repo Engram:
   Modified function: _get_clip (lines 87-120)
   Added: _clip_broken = False  |  if _clip_broken: raise RuntimeError  |  logger.error(...)
   Removed: pass"

This is the highest-signal input for understanding code work because it
describes intent, not just state. The full-file capture still runs in
parallel (via filesystem.py) — the diff is additive context.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from loguru import logger


def get_repo_root(file_path: Path) -> Optional[Path]:
    """Return the git repo root for a file, or None if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(file_path.parent),
            capture_output=True,
            text=True,
            timeout=4,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except Exception:
        pass
    return None


def get_file_diff(file_path: Path) -> Optional[str]:
    """
    Return the git diff for a specific file (staged or unstaged changes).
    Tries staged diff first, then unstaged. Returns None if no changes or
    not in a git repo.
    """
    repo_root = get_repo_root(file_path)
    if not repo_root:
        return None

    try:
        # Staged changes (git add already done)
        staged = subprocess.run(
            ["git", "diff", "--cached", "--unified=2", "--", str(file_path)],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()

        # Unstaged changes (saved but not added)
        unstaged = subprocess.run(
            ["git", "diff", "--unified=2", "--", str(file_path)],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()

        diff = staged or unstaged
        return diff[:4000] if diff else None

    except Exception as exc:
        logger.debug(f"git diff failed for {file_path.name}: {exc}")
        return None


def _extract_changed_functions(diff_text: str) -> list[str]:
    """
    Parse a unified diff to find which function/class names appear near
    changed lines (using the @@ ... @@ hunk headers).
    """
    functions = []
    # Git unified diff hunk header: @@ -a,b +c,d @@ def function_name(...):
    hunk_pattern = re.compile(r"^@@ [^@]+ @@ (.+)$", re.MULTILINE)
    for match in hunk_pattern.finditer(diff_text):
        context = match.group(1).strip()
        # Extract function/class name
        fn_match = re.match(r"(?:def|class|function|const|let|var|async def)\s+(\w+)", context)
        if fn_match:
            functions.append(fn_match.group(1))
    return list(dict.fromkeys(functions))  # deduplicate preserving order


def summarize_diff(diff_text: str, file_path: Path) -> str:
    """
    Convert a raw git diff into a human-readable, searchable description.
    Focuses on what changed and where, not raw patch syntax.
    """
    if not diff_text:
        return ""

    file_name = file_path.name

    added_lines: list[str] = []
    removed_lines: list[str] = []

    for line in diff_text.split("\n"):
        if line.startswith("+") and not line.startswith("+++"):
            content = line[1:].strip()
            if content and not content.startswith("#"):
                added_lines.append(content)
        elif line.startswith("-") and not line.startswith("---"):
            content = line[1:].strip()
            if content and not content.startswith("#"):
                removed_lines.append(content)

    changed_fns = _extract_changed_functions(diff_text)
    parts = [f"Code change in {file_name}"]

    if changed_fns:
        parts.append(f"Modified: {', '.join(changed_fns[:4])}")

    if added_lines:
        sample = " | ".join(added_lines[:6])
        parts.append(f"Added ({len(added_lines)} lines): {sample[:300]}")

    if removed_lines:
        sample = " | ".join(removed_lines[:4])
        parts.append(f"Removed ({len(removed_lines)} lines): {sample[:200]}")

    return "\n".join(parts)


def capture_diff_if_changed(
    file_path: Path,
    window_title: str = "",
    app_name: str = "",
) -> Optional[str]:
    """
    If `file_path` is inside a git repo and has uncommitted changes,
    enqueue a diff-based capture and return the capture UUID.
    Returns None if no diff or git is unavailable.
    """
    diff_text = get_file_diff(file_path)
    if not diff_text:
        return None

    summary = summarize_diff(diff_text, file_path)
    if not summary.strip():
        return None

    repo_root = get_repo_root(file_path)
    repo_name = repo_root.name if repo_root else ""

    # Use the context_parser to get a rich window description if no title provided
    if not window_title:
        window_title = f"{file_path.name} — {repo_name}"
    if not app_name:
        app_name = "git"

    try:
        from pipeline import queue_manager
        from datetime import datetime

        capture_id = queue_manager.enqueue(
            source_type="file",
            timestamp=datetime.utcnow(),
            raw_path=str(file_path),
            content=summary,
            window_title=window_title,
            app_name=app_name,
        )
        logger.debug(f"Git diff captured: {file_path.name} → {len(diff_text)} bytes of diff → {capture_id[:8]}")
        return capture_id

    except Exception as exc:
        logger.debug(f"Git diff capture enqueue failed: {exc}")
        return None
