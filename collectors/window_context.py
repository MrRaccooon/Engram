"""
Active window context collector.

Uses the Win32 API to retrieve the title and executable name of the
currently focused window. This metadata is attached to every capture
so search results can be filtered and sorted by application.

Also exposes is_excluded() to check whether the active app should
suppress capture (password managers, banking apps, etc.).
"""

from __future__ import annotations

import os
from typing import Optional

import psutil
import win32gui
import win32process
from loguru import logger


def get_active_window() -> tuple[str, str]:
    """
    Return (window_title, app_name) for the currently focused window.
    Returns ('', '') on failure.
    """
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd) or ""
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            proc = psutil.Process(pid)
            exe_name = os.path.basename(proc.exe()).lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            exe_name = ""
        return title, exe_name
    except Exception as exc:
        logger.debug(f"get_active_window failed: {exc}")
        return "", ""


def is_excluded(app_name: str, excluded_apps: list[str]) -> bool:
    """
    Return True if app_name (lower-case exe basename) matches any entry
    in the excluded_apps list from config.
    """
    name = app_name.lower()
    return any(name == ex.lower() for ex in excluded_apps)


def is_incognito(window_title: str) -> bool:
    """
    Heuristic: detect if the focused window is a private/incognito browser tab
    by checking for known title suffixes used by Chrome, Edge, and Firefox.
    """
    title_lower = window_title.lower()
    markers = [
        "- incognito",       # Chrome
        "- inprivate",       # Edge
        "(private browsing)", # Firefox
        "private — mozilla", # Firefox (some versions)
    ]
    return any(m in title_lower for m in markers)
