"""
System tray integration using pystray.

Provides a persistent tray icon that:
  - Shows Engram is running
  - Lets the user pause/resume capture
  - Opens the dashboard in the default browser
  - Shows current status (indexed count, queue depth)
  - Gracefully shuts down the daemon

Must be called from the main thread (pystray requirement on Windows).
"""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from typing import Optional

from loguru import logger

try:
    import pystray
    from PIL import Image, ImageDraw
    _TRAY_AVAILABLE = True
except ImportError:
    _TRAY_AVAILABLE = False
    logger.warning("pystray or Pillow not available — system tray disabled")


def _make_icon() -> "Image.Image":
    """Generate a simple brain-dot icon programmatically (no image file needed)."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Outer circle (accent purple)
    draw.ellipse([4, 4, size - 4, size - 4], fill="#7c6af7")
    # Inner white dot
    draw.ellipse([24, 24, size - 24, size - 24], fill="#ffffff")
    return img


_paused = False
_icon_instance: Optional["pystray.Icon"] = None


def _toggle_pause(icon, item) -> None:
    global _paused
    _paused = not _paused
    from daemon import scheduler
    if _paused:
        scheduler.stop()
        logger.info("Capture paused via tray")
    else:
        scheduler.start()
        logger.info("Capture resumed via tray")
    _refresh_menu(icon)


def _open_dashboard(icon, item) -> None:
    webbrowser.open("http://127.0.0.1:8765")


def _capture_now(icon, item) -> None:
    import requests
    try:
        requests.post("http://127.0.0.1:8765/api/capture/manual", timeout=5)
        logger.info("Manual capture triggered from tray")
    except Exception as exc:
        logger.warning(f"Manual capture from tray failed: {exc}")


def _get_status_label() -> str:
    try:
        import requests
        r = requests.get("http://127.0.0.1:8765/api/status", timeout=3)
        if r.ok:
            s = r.json()
            return f"Indexed: {s['indexed_captures']} | Queue: {s['pending_queue']} | {s['storage_mb']} MB"
    except Exception:
        pass
    return "Status unavailable"


def _refresh_menu(icon: "pystray.Icon") -> None:
    icon.menu = _build_menu()


def _build_menu() -> "pystray.Menu":
    return pystray.Menu(
        pystray.MenuItem("Engram", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(_get_status_label(), None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open dashboard", _open_dashboard),
        pystray.MenuItem("Capture now", _capture_now),
        pystray.MenuItem("Pause capture" if not _paused else "Resume capture", _toggle_pause),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit Engram", _quit),
    )


def _quit(icon: "pystray.Icon", item) -> None:
    logger.info("Quit requested from tray")
    from daemon import scheduler
    scheduler.stop()
    icon.stop()


def show_digest() -> None:
    """
    Show the daily morning digest as a system tray notification (Phase 5).

    Displays:
      1. Yesterday's consolidated insight summary
      2. Top app by time spent
      3. One resurfaced memory from 7-21 days ago

    Called daily at 8 AM by APScheduler. Fails silently if tray is unavailable.
    """
    try:
        from datetime import datetime, timedelta
        from storage import metadata_db, vector_db
        from pipeline import embedder

        yesterday = (datetime.utcnow() - timedelta(days=1)).date().isoformat()

        # 1. Yesterday's insight
        rows = metadata_db.fetch_insights_for_day(yesterday)
        if rows:
            summary = rows[0]["summary"][:120]
        else:
            summary = "No insight summary available for yesterday."

        # 2. Top app by time (screenshot count as proxy)
        with metadata_db._connect() as conn:
            row = conn.execute(
                """
                SELECT app_name, COUNT(*) as cnt FROM captures
                WHERE date(timestamp) = ? AND source_type='screenshot'
                  AND app_name IS NOT NULL AND app_name != ''
                GROUP BY app_name ORDER BY cnt DESC LIMIT 1
                """,
                (yesterday,),
            ).fetchone()
        top_app = f"Top app: {row['app_name']}" if row else ""

        # 3. Resurface: find an old capture similar to yesterday's insight
        resurface_text = ""
        if rows:
            insight_vec = embedder.embed_text(rows[0]["summary"])
            if insight_vec:
                old_cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
                older_cutoff = (datetime.utcnow() - timedelta(days=21)).isoformat()
                old_results = vector_db.query_text(
                    insight_vec,
                    top_k=5,
                    where={"timestamp": {"$lte": old_cutoff, "$gte": older_cutoff}},
                )
                if old_results:
                    preview = old_results[0].get("content_preview", "")[:80]
                    old_ts = old_results[0].get("timestamp", "")[:10]
                    resurface_text = f"Memory from {old_ts}: {preview}"

        lines = [f"Yesterday: {summary}"]
        if top_app:
            lines.append(top_app)
        if resurface_text:
            lines.append(resurface_text)

        message = "\n".join(lines)
        title = "Engram — Good morning"

        if _icon_instance and _TRAY_AVAILABLE:
            _icon_instance.notify(message, title)
            logger.info("Daily digest notification shown")
        else:
            logger.info(f"Daily digest (no tray): {message}")

    except Exception as exc:
        logger.warning(f"Daily digest failed: {exc}")


def _status_refresh_loop(icon: "pystray.Icon") -> None:
    """Refresh tray menu every 30 seconds to update status counts."""
    import time
    while True:
        time.sleep(30)
        try:
            _refresh_menu(icon)
        except Exception:
            break


def run() -> None:
    """
    Start the system tray icon. Blocks the calling thread (run from main thread).
    Must be called AFTER the daemon scheduler is started.
    """
    if not _TRAY_AVAILABLE:
        logger.warning("Tray unavailable — running headless")
        import signal, sys
        signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
        signal.pause()
        return

    global _icon_instance
    icon_image = _make_icon()
    _icon_instance = pystray.Icon(
        name="Engram",
        icon=icon_image,
        title="Engram — your second brain",
        menu=_build_menu(),
    )

    # Status refresh in background thread
    t = threading.Thread(target=_status_refresh_loop, args=(_icon_instance,), daemon=True)
    t.start()

    logger.info("System tray started")
    _icon_instance.run()
