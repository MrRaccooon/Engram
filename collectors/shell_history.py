"""
Shell history collector.

Watches shell history files for new commands — completely passive,
requires zero user action. Every command typed in PowerShell, CMD,
bash, or zsh is automatically indexed.

This tells the system:
  - "python main.py" → you started the server
  - "pip install rapidocr-onnxruntime" → you installed something
  - "git commit -m 'fix CLIP loading'" → you committed with a description
  - "pytest pipeline/test_embedder.py -v" → you ran specific tests
  - "git diff HEAD pipeline/embedder.py" → you inspected a specific file

History file locations:
  Windows PowerShell: %APPDATA%\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt
  Windows CMD:        No persistent history by default (we skip it)
  macOS/Linux bash:   ~/.bash_history
  macOS/Linux zsh:    ~/.zsh_history
"""

from __future__ import annotations

import platform
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from pipeline import queue_manager

_watcher_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()

# Tracks the last line count per history file to detect new entries
_last_line_count: dict[str, int] = {}


def _get_history_paths() -> list[Path]:
    """Return shell history file paths that exist on this system."""
    paths: list[Path] = []
    system = platform.system()

    if system == "Windows":
        appdata = Path.home() / "AppData" / "Roaming"
        ps_history = appdata / "Microsoft" / "Windows" / "PowerShell" / "PSReadLine" / "ConsoleHost_history.txt"
        if ps_history.parent.exists():
            paths.append(ps_history)

    elif system in ("Darwin", "Linux"):
        bash_history = Path.home() / ".bash_history"
        zsh_history = Path.home() / ".zsh_history"
        for p in (bash_history, zsh_history):
            if p.exists():
                paths.append(p)

    return paths


def _parse_new_commands(path: Path) -> list[str]:
    """
    Read new lines from the history file since the last check.
    Returns a list of new command strings.
    """
    key = str(path)
    try:
        if not path.exists():
            return []

        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        prev_count = _last_line_count.get(key, len(lines))
        _last_line_count[key] = len(lines)

        if len(lines) <= prev_count:
            return []

        new_lines = lines[prev_count:]
        # Filter: skip blank lines and zsh timestamps (lines starting with #)
        commands = [l.strip() for l in new_lines if l.strip() and not l.startswith("#")]
        return commands

    except Exception as exc:
        logger.debug(f"Shell history read failed ({path.name}): {exc}")
        return []


def _enrich_command(cmd: str) -> str:
    """
    Add semantic description to a raw shell command.
    "pytest pipeline/test_embedder.py -v" → "Running tests: pipeline/test_embedder.py"
    "git commit -m 'fix CLIP'" → "Git commit: fix CLIP"
    """
    import re
    cmd = cmd.strip()

    # git operations
    if cmd.startswith("git "):
        git_match = re.match(r"git (\w+)(.*)", cmd)
        if git_match:
            subcmd, rest = git_match.group(1), git_match.group(2).strip()
            if subcmd == "commit":
                msg_match = re.search(r'-m\s+["\'](.+?)["\']', rest)
                if msg_match:
                    return f"Git commit: {msg_match.group(1)}"
                return f"Git commit"
            if subcmd in ("push", "pull", "fetch"):
                return f"Git {subcmd}: {rest[:60]}"
            if subcmd == "diff":
                return f"Viewing git diff: {rest[:60]}"
            if subcmd == "checkout":
                return f"Git checkout: {rest[:60]}"
            if subcmd == "merge":
                return f"Git merge: {rest[:60]}"

    # pip / pip3
    if re.match(r"pip3?\s+install", cmd):
        pkg = cmd.split("install")[-1].strip()
        return f"Installing Python package: {pkg[:80]}"
    if re.match(r"pip3?\s+uninstall", cmd):
        pkg = cmd.split("uninstall")[-1].strip()
        return f"Uninstalling Python package: {pkg[:80]}"

    # pytest
    if cmd.startswith("pytest") or cmd.startswith("python -m pytest"):
        return f"Running tests: {cmd[6:].strip()[:80]}"

    # python script
    if re.match(r"python3?\s+\S+\.py", cmd):
        script = re.search(r"python3?\s+(\S+\.py)", cmd)
        if script:
            return f"Running Python script: {script.group(1)}"

    # npm / yarn
    if re.match(r"npm\s+run\s+\w+", cmd):
        task = cmd.split("npm run")[-1].strip()
        return f"npm run {task}"

    # cd command
    if re.match(r"cd\s+", cmd):
        dest = cmd[2:].strip()
        return f"Changed directory to: {dest}"

    return f"Shell command: {cmd}"


def _poll_history_files(history_paths: list[Path]) -> None:
    """Poll loop — check history files every 3 seconds for new commands."""
    logger.info(f"Shell history watcher started: {[str(p) for p in history_paths]}")

    # Prime the line counts (don't index history that already exists)
    for path in history_paths:
        if path.exists():
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                _last_line_count[str(path)] = len(lines)
            except Exception:
                pass

    while not _stop_event.is_set():
        for path in history_paths:
            new_cmds = _parse_new_commands(path)
            for cmd in new_cmds:
                enriched = _enrich_command(cmd)
                try:
                    capture_id = queue_manager.enqueue(
                        source_type="clipboard",   # reuse clipboard type for plain text
                        timestamp=datetime.utcnow(),
                        content=enriched,
                        window_title=f"Shell: {cmd[:60]}",
                        app_name=path.stem,        # "ConsoleHost_history" or "bash_history"
                    )
                    logger.debug(f"Shell command indexed: {enriched[:60]} → {capture_id[:8]}")
                except Exception as exc:
                    logger.debug(f"Shell history enqueue failed: {exc}")

        _stop_event.wait(timeout=3)


def start() -> None:
    """Start the shell history watcher in a background thread."""
    global _watcher_thread
    history_paths = _get_history_paths()

    if not history_paths:
        logger.info("Shell history watcher: no history files found, skipping")
        return

    _stop_event.clear()
    _watcher_thread = threading.Thread(
        target=_poll_history_files,
        args=(history_paths,),
        daemon=True,
        name="shell-history-watcher",
    )
    _watcher_thread.start()


def stop() -> None:
    """Stop the shell history watcher."""
    _stop_event.set()
    if _watcher_thread:
        _watcher_thread.join(timeout=5)
    logger.info("Shell history watcher stopped")
