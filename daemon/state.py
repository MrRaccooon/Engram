"""
Persistent daemon state.

Tracks the last-run timestamp of each scheduled job across restarts.
Stored as a simple JSON file at ~/.engram/daemon_state.json so it
survives shutdowns, crashes, and updates.

On startup, the scheduler reads this file to determine:
  - How many days of consolidation were missed (and catches up)
  - Whether today's digest has been shown (and shows it if past 8 AM)
  - Whether any other periodic jobs need an immediate run

Format:
    {
      "consolidation": "2026-03-28T02:03:14",
      "daily_digest":  "2026-03-28T08:00:07",
      "retention":     "2026-03-27T03:00:00"
    }
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

_STATE_FILE_NAME = "daemon_state.json"
_state_path: Optional[Path] = None


def init(base_path: Path) -> None:
    """Call once at scheduler startup with the Engram base data directory."""
    global _state_path
    _state_path = base_path / _STATE_FILE_NAME
    if not _state_path.exists():
        _state_path.write_text("{}", encoding="utf-8")
        logger.debug(f"Daemon state file created at {_state_path}")


def _read() -> dict:
    if _state_path is None or not _state_path.exists():
        return {}
    try:
        return json.loads(_state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(data: dict) -> None:
    if _state_path is None:
        return
    try:
        _state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed to write daemon state: {exc}")


def get_last_run(job_id: str) -> Optional[datetime]:
    """Return the last run datetime for a job, or None if never run."""
    data = _read()
    ts = data.get(job_id)
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def record_run(job_id: str) -> None:
    """Record the current UTC time as the last run for a job."""
    data = _read()
    data[job_id] = datetime.utcnow().isoformat()
    _write(data)
    logger.debug(f"State: recorded run for '{job_id}'")


def days_since_last_run(job_id: str) -> Optional[int]:
    """
    Return the number of whole calendar days since the job last ran.
    Returns None if the job has never run.
    """
    last = get_last_run(job_id)
    if last is None:
        return None
    delta = datetime.utcnow() - last
    return delta.days


def missed_today(job_id: str, run_hour: int, run_minute: int = 0) -> bool:
    """
    Return True if a daily job scheduled at run_hour:run_minute UTC has not
    yet run today and the current time is already past that scheduled time.

    Used to decide whether to fire a catch-up run immediately on startup.
    """
    now = datetime.utcnow()
    last = get_last_run(job_id)

    # Job has never run
    if last is None:
        return now.hour >= run_hour

    # Job last ran today already
    if last.date() == now.date():
        return False

    # Job last ran before today — check if we're past its scheduled time today
    return now.hour > run_hour or (now.hour == run_hour and now.minute >= run_minute)
