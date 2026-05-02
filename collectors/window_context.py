"""
Active window context collector.

Cross-platform: uses Win32 API on Windows, osascript on macOS,
and xdotool on Linux (graceful fallback to empty strings).

This metadata is attached to every capture so search results can be
filtered and sorted by application.
"""

from __future__ import annotations

import platform as _platform
from loguru import logger


def get_active_window() -> tuple[str, str]:
    """
    Return (window_title, app_name) for the currently focused window.
    Returns ('', '') on failure or unsupported platform.
    """
    system = _platform.system()
    if system == "Windows":
        return _get_windows()
    elif system == "Darwin":
        return _get_macos()
    elif system == "Linux":
        return _get_linux()
    return "", ""


def _get_windows() -> tuple[str, str]:
    try:
        import os
        import psutil
        import win32gui
        import win32process

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
        logger.debug(f"get_active_window (Windows) failed: {exc}")
        return "", ""


def _get_macos() -> tuple[str, str]:
    try:
        import subprocess

        app_script = (
            'tell application "System Events" to get name of '
            'first application process whose frontmost is true'
        )
        app_result = subprocess.run(
            ["osascript", "-e", app_script],
            capture_output=True, text=True, timeout=2,
        )
        app_name = app_result.stdout.strip()

        title_script = (
            'tell application "System Events"\n'
            '  set fp to first application process whose frontmost is true\n'
            '  tell fp\n'
            '    if (count of windows) > 0 then\n'
            '      return name of front window\n'
            '    else\n'
            '      return ""\n'
            '    end if\n'
            '  end tell\n'
            'end tell'
        )
        title_result = subprocess.run(
            ["osascript", "-e", title_script],
            capture_output=True, text=True, timeout=2,
        )
        title = title_result.stdout.strip()

        return title, app_name.lower()
    except Exception as exc:
        logger.debug(f"get_active_window (macOS) failed: {exc}")
        return "", ""


def _get_linux() -> tuple[str, str]:
    try:
        import subprocess

        result = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True, text=True, timeout=2,
        )
        title = result.stdout.strip()

        pid_result = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowpid"],
            capture_output=True, text=True, timeout=2,
        )
        pid_str = pid_result.stdout.strip()
        app_name = ""
        if pid_str.isdigit():
            import os
            import psutil
            try:
                proc = psutil.Process(int(pid_str))
                app_name = os.path.basename(proc.exe()).lower()
            except Exception:
                pass

        return title, app_name
    except FileNotFoundError:
        logger.debug("xdotool not installed — window context unavailable on Linux")
        return "", ""
    except Exception as exc:
        logger.debug(f"get_active_window (Linux) failed: {exc}")
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
        "- incognito",        # Chrome
        "- inprivate",        # Edge
        "(private browsing)", # Firefox
        "private — mozilla",  # Firefox (some versions)
    ]
    return any(m in title_lower for m in markers)
