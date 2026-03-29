"""
APScheduler daemon.

Wires all hot-path collectors to configurable intervals and starts the
cold-path embedding worker. Also initialises the storage layer (SQLite +
ChromaDB) so everything is ready before the first job fires.

Run directly:
    python -m daemon.scheduler

Or import start() / stop() from other entry points.
"""

from __future__ import annotations

import signal
import sys
from pathlib import Path

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

# ── resolve config ────────────────────────────────────────────────────────────
_CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"


def _load_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── storage init (called once before scheduler starts) ────────────────────────
def _init_storage(cfg: dict) -> Path:
    from storage import metadata_db, vector_db

    base = Path(cfg["storage"]["base_path"]).expanduser()
    metadata_db.init(base / "metadata.db")
    vector_db.init(base / "chromadb")
    return base


# ── job functions (each wraps a collector call) ───────────────────────────────

def _job_screenshot(storage_root: Path, cfg: dict) -> None:
    from collectors import screenshot, window_context

    cap_cfg = cfg.get("capture", {})
    excluded_apps: list[str] = cap_cfg.get("excluded_apps", [])
    suppress_incognito: bool = cap_cfg.get("suppress_incognito", True)
    thumb_size: int = cfg.get("storage", {}).get("thumbnail_size", 400)

    window_title, app_name = window_context.get_active_window()
    if window_context.is_excluded(app_name, excluded_apps):
        logger.debug(f"Screenshot suppressed: excluded app '{app_name}'")
        return
    if suppress_incognito and window_context.is_incognito(window_title):
        logger.debug("Screenshot suppressed: incognito window detected")
        return

    screenshot.capture(storage_root=storage_root, thumbnail_size=thumb_size)


def _job_clipboard(cfg: dict) -> None:
    from collectors import clipboard

    cap_cfg = cfg.get("capture", {})
    excluded_apps: list[str] = cap_cfg.get("excluded_apps", [])

    from collectors import window_context
    _, app_name = window_context.get_active_window()
    if window_context.is_excluded(app_name, excluded_apps):
        return

    clipboard.poll()


def _job_browser_history(cfg: dict) -> None:
    from collectors import browser_history

    excluded_domains: list[str] = cfg.get("capture", {}).get("excluded_domains", [])
    browser_history.collect_all(excluded_domains)


def _job_embedding_worker() -> None:
    from pipeline.worker import process_batch
    process_batch()


def _job_consolidation() -> None:
    from pipeline.consolidation_worker import run_consolidation
    run_consolidation(days_back=1)


def _job_daily_digest() -> None:
    from daemon.tray import show_digest
    show_digest()


# ── public API ────────────────────────────────────────────────────────────────
_scheduler: BackgroundScheduler | None = None


def start() -> None:
    global _scheduler

    cfg = _load_config()
    storage_root = _init_storage(cfg)

    cap = cfg.get("capture", {})
    emb = cfg.get("embedding", {})

    screenshot_interval: int = cap.get("screenshot_interval_seconds", 30)
    clipboard_interval: int = cap.get("clipboard_poll_seconds", 2)
    worker_interval_min: int = emb.get("worker_interval_minutes", 2)

    _scheduler = BackgroundScheduler(daemon=True)

    _scheduler.add_job(
        _job_screenshot,
        trigger="interval",
        seconds=screenshot_interval,
        kwargs={"storage_root": storage_root, "cfg": cfg},
        id="screenshot",
        max_instances=1,
        coalesce=True,
    )

    _scheduler.add_job(
        _job_clipboard,
        trigger="interval",
        seconds=clipboard_interval,
        kwargs={"cfg": cfg},
        id="clipboard",
        max_instances=1,
        coalesce=True,
    )

    _scheduler.add_job(
        _job_browser_history,
        trigger="interval",
        minutes=15,
        kwargs={"cfg": cfg},
        id="browser_history",
        max_instances=1,
        coalesce=True,
    )

    _scheduler.add_job(
        _job_embedding_worker,
        trigger="interval",
        minutes=worker_interval_min,
        id="embedding_worker",
        max_instances=1,
        coalesce=True,
    )

    # Nightly consolidation (Phase 2)
    cons_cfg = cfg.get("consolidation", {})
    cons_hour = cons_cfg.get("run_hour", 2)
    cons_minute = cons_cfg.get("run_minute", 0)
    _scheduler.add_job(
        _job_consolidation,
        trigger="cron",
        hour=cons_hour,
        minute=cons_minute,
        id="consolidation",
        max_instances=1,
        coalesce=True,
    )

    # Daily digest notification (Phase 5)
    _scheduler.add_job(
        _job_daily_digest,
        trigger="cron",
        hour=8,
        minute=0,
        id="daily_digest",
        max_instances=1,
        coalesce=True,
    )

    # Start filesystem watcher (runs in its own watchdog thread)
    from collectors import filesystem
    filesystem.start(
        watched_dirs=cap.get("watched_directories", []),
        watched_extensions=cap.get("watched_extensions", []),
        excluded_apps=cap.get("excluded_apps", []),
    )

    _scheduler.start()
    logger.info(
        f"Engram scheduler started | "
        f"screenshot every {screenshot_interval}s | "
        f"clipboard every {clipboard_interval}s | "
        f"embed every {worker_interval_min}m"
    )


def stop() -> None:
    from collectors import filesystem
    filesystem.stop()
    if _scheduler:
        _scheduler.shutdown(wait=False)
    logger.info("Engram scheduler stopped")


# ── standalone entry point ────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.add("logs/engram_{time}.log", rotation="10 MB", retention="7 days")

    start()

    def _handle_exit(sig, frame):  # type: ignore[no-untyped-def]
        logger.info(f"Signal {sig} received, shutting down…")
        stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_exit)
    signal.signal(signal.SIGTERM, _handle_exit)

    logger.info("Engram running. Press Ctrl+C to stop.")
    signal.pause()
