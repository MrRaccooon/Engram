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

    try:
        from pipeline.concept_vocabulary import init as init_concepts
        init_concepts()
    except Exception as exc:
        logger.warning(f"Concept vocabulary init deferred: {exc}")

    return base


# ── job functions (each wraps a collector call) ───────────────────────────────

_TERMINAL_APP_KEYWORDS = (
    "windowsterminal", "cmd", "powershell", "wt", "bash", "zsh",
    "alacritty", "wezterm", "conhost", "mintty",
)
# Tracks how many 5-second ticks have passed since last screenshot in
# normal (non-terminal) mode so we only fire every N ticks for normal apps.
_screenshot_tick = 0


def _job_screenshot(storage_root: Path, cfg: dict) -> None:
    global _screenshot_tick
    from collectors import screenshot, window_context

    cap_cfg = cfg.get("capture", {})
    excluded_apps: list[str] = cap_cfg.get("excluded_apps", [])
    suppress_incognito: bool = cap_cfg.get("suppress_incognito", True)
    thumb_size: int = cfg.get("storage", {}).get("thumbnail_size", 1024)
    normal_interval: int = cap_cfg.get("screenshot_interval_seconds", 30)

    window_title, app_name = window_context.get_active_window()
    app_lower = (app_name or "").lower()

    if window_context.is_excluded(app_name, excluded_apps):
        logger.debug(f"Screenshot suppressed: excluded app '{app_name}'")
        return
    if suppress_incognito and window_context.is_incognito(window_title):
        logger.debug("Screenshot suppressed: incognito window detected")
        return

    is_terminal = any(kw in app_lower for kw in _TERMINAL_APP_KEYWORDS)
    _screenshot_tick += 1

    # Adaptive rate based on diff-detected activity level.
    # Base tick = 2s. Multipliers: high=1 (2s), medium=3 (6s), low=15 (30s).
    # Terminals always capture every tick (output disappears fast).
    if is_terminal:
        screenshot.capture(storage_root=storage_root, thumbnail_size=thumb_size)
    else:
        try:
            from pipeline.diff_analyzer import get_activity_level
            activity = get_activity_level()
        except Exception:
            activity = "low"

        if activity == "high":
            do_capture = True
        elif activity == "medium":
            do_capture = (_screenshot_tick % 3 == 0)
        else:
            ticks_needed = max(1, normal_interval // 2)
            do_capture = (_screenshot_tick % ticks_needed == 0)

        if do_capture:
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


def _job_consolidation(days_back: int = 1) -> None:
    from pipeline.consolidation_worker import run_consolidation
    from daemon import state
    run_consolidation(days_back=days_back)
    state.record_run("consolidation")


def _job_micro_consolidation() -> None:
    from pipeline.consolidation_worker import run_micro_consolidation
    run_micro_consolidation()


def _job_weekly_rollup() -> None:
    from pipeline.consolidation_worker import run_weekly_rollup
    from daemon import state
    run_weekly_rollup()
    state.record_run("weekly_rollup")


def _job_concept_harvest() -> None:
    from pipeline.concept_vocabulary import run_harvest_cycle
    run_harvest_cycle()


def _job_concept_decay() -> None:
    from pipeline.concept_vocabulary import run_decay_cycle
    run_decay_cycle()


def _job_daily_digest() -> None:
    from daemon.tray import show_digest
    from daemon import state
    show_digest()
    state.record_run("daily_digest")


# ── public API ────────────────────────────────────────────────────────────────
_scheduler: BackgroundScheduler | None = None


def _run_catchup_jobs(cfg: dict, storage_root: Path) -> None:
    """
    Run any scheduled jobs that were missed while the machine was off.
    Called once at startup before the regular schedule begins.
    Executes in a background thread so it doesn't block the scheduler start.
    """
    import threading
    from daemon import state

    cons_cfg = cfg.get("consolidation", {})
    cons_hour: int = cons_cfg.get("run_hour", 2)
    cons_minute: int = cons_cfg.get("run_minute", 0)

    def _catchup() -> None:
        # ── Consolidation catch-up ────────────────────────────────────────
        # Calculate how many days of consolidation were missed.
        # consolidation_worker already skips days that have insights,
        # so passing a larger days_back is safe and idempotent.
        days_missed = state.days_since_last_run("consolidation")

        if days_missed is None:
            # Never run before — consolidate up to last 7 days on first launch
            catchup_days = 7
            logger.info("Consolidation: first run, catching up on last 7 days")
        elif days_missed >= 1:
            # Machine was off — consolidate every day we missed
            catchup_days = min(days_missed + 1, 30)  # cap at 30 days
            logger.info(
                f"Consolidation: machine was off for ~{days_missed} day(s), "
                f"running catch-up for {catchup_days} days"
            )
        else:
            catchup_days = 0

        if catchup_days > 0:
            _job_consolidation(days_back=catchup_days)

        # ── Daily digest catch-up ─────────────────────────────────────────
        # If it's past 8 AM and today's digest hasn't been shown, show it now.
        if state.missed_today("daily_digest", run_hour=8):
            logger.info("Daily digest: missed scheduled time, showing now")
            _job_daily_digest()

    t = threading.Thread(target=_catchup, daemon=True, name="engram-catchup")
    t.start()


def start() -> None:
    global _scheduler

    cfg = _load_config()
    storage_root = _init_storage(cfg)

    # Init persistent state tracking
    from daemon import state
    state.init(storage_root)

    cap = cfg.get("capture", {})
    emb = cfg.get("embedding", {})

    screenshot_interval: int = cap.get("screenshot_interval_seconds", 30)
    clipboard_interval: int = cap.get("clipboard_poll_seconds", 2)
    worker_interval_min: int = emb.get("worker_interval_minutes", 2)

    _scheduler = BackgroundScheduler(daemon=True)

    # Screenshots run on a fixed 2-second tick.
    # _job_screenshot internally decides whether to actually capture based
    # on diff-detected activity level (high=2s, medium=6s, low=30s).
    _scheduler.add_job(
        _job_screenshot,
        trigger="interval",
        seconds=2,
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

    # Micro-consolidation every 2 hours during active use
    _scheduler.add_job(
        _job_micro_consolidation,
        trigger="interval",
        hours=2,
        id="micro_consolidation",
        max_instances=1,
        coalesce=True,
    )

    # Weekly rollup on Sundays at 3 AM
    _scheduler.add_job(
        _job_weekly_rollup,
        trigger="cron",
        day_of_week="sun",
        hour=3,
        minute=0,
        id="weekly_rollup",
        max_instances=1,
        coalesce=True,
    )

    # Concept vocabulary: harvest every 6 hours
    _scheduler.add_job(
        _job_concept_harvest,
        trigger="interval",
        hours=6,
        id="concept_harvest",
        max_instances=1,
        coalesce=True,
    )

    # Concept vocabulary: relevance decay + IDF + merge — daily at 1 AM
    _scheduler.add_job(
        _job_concept_decay,
        trigger="cron",
        hour=1,
        minute=0,
        id="concept_decay",
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

    # Start shell history watcher (passively indexes every command typed)
    from collectors import shell_history
    shell_history.start()

    _scheduler.start()
    logger.info(
        f"Engram scheduler started | "
        f"screenshot every {screenshot_interval}s | "
        f"clipboard every {clipboard_interval}s | "
        f"embed every {worker_interval_min}m"
    )

    # Catch up on any jobs missed while the machine was off
    _run_catchup_jobs(cfg, storage_root)


def stop() -> None:
    from collectors import filesystem
    from collectors import shell_history
    filesystem.stop()
    shell_history.stop()
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
