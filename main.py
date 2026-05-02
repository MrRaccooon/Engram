"""
Engram — main entry point.

Starts three things in the correct order:
  1. FastAPI server (uvicorn, background thread)
  2. Collector scheduler (APScheduler, background threads)
  3. Global hotkey listener (pynput, background thread)
  4. System tray (pystray, main thread — must be last)

Run with: python main.py
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

# Prevent OpenMP duplicate-library crash (torch + easyocr both bundle it)
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import uvicorn
import yaml
from dotenv import load_dotenv
from loguru import logger
from pynput import keyboard

# Load .env before anything else so API keys reach os.environ
load_dotenv(Path(__file__).parent / ".env")

logger.add("logs/engram_{time}.log", rotation="10 MB", retention="7 days", enqueue=True)

_CONFIG_PATH = Path(__file__).parent / "config" / "config.yaml"


def _load_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── 1. FastAPI server ─────────────────────────────────────────────────────────

def _start_api(cfg: dict) -> None:
    api_cfg = cfg.get("api", {})
    config = uvicorn.Config(
        "api.main:app",
        host=api_cfg.get("host", "127.0.0.1"),
        port=api_cfg.get("port", 8765),
        log_level="warning",
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    server.run()


# ── 2. Global hotkey (Ctrl+Shift+M → manual capture) ─────────────────────────

def _start_hotkey_listener(cfg: dict) -> None:
    hotkey_str: str = cfg.get("capture", {}).get("manual_hotkey", "ctrl+shift+m")

    def on_activate():
        logger.info("Global hotkey triggered — manual capture")
        try:
            import requests
            requests.post("http://127.0.0.1:8765/api/capture/manual", timeout=5)
        except Exception as exc:
            logger.warning(f"Hotkey capture POST failed: {exc}")

    # Parse hotkey string into pynput format
    try:
        parts = [p.strip() for p in hotkey_str.lower().split("+")]
        modifiers = {
            "ctrl":  keyboard.Key.ctrl_l,
            "shift": keyboard.Key.shift,
            "alt":   keyboard.Key.alt_l,
            "cmd":   keyboard.Key.cmd,
        }
        keys = set()
        char_key = None
        for p in parts:
            if p in modifiers:
                keys.add(modifiers[p])
            else:
                char_key = keyboard.KeyCode.from_char(p)
                keys.add(char_key)

        hotkey = keyboard.HotKey(keys, on_activate)

        def for_canonical(f):
            return lambda k: f(listener.canonical(k))

        listener = keyboard.Listener(
            on_press=for_canonical(hotkey.press),
            on_release=for_canonical(hotkey.release),
        )
        listener.daemon = True
        listener.start()
        logger.info(f"Global hotkey registered: {hotkey_str}")
    except Exception as exc:
        logger.warning(f"Failed to register hotkey '{hotkey_str}': {exc}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cfg = _load_config()

    # API server in background thread
    api_thread = threading.Thread(target=_start_api, args=(cfg,), daemon=True)
    api_thread.start()
    logger.info("FastAPI server starting…")
    time.sleep(2)  # Give the server time to bind

    # Collector scheduler
    from daemon import scheduler
    scheduler.start()

    # Pre-warm embedding models in background so first search isn't 22s
    def _prewarm():
        try:
            from pipeline import embedder
            embedder._get_text_model()
            embedder._get_clip()
            logger.info("Model pre-warm complete")
        except Exception as exc:
            logger.warning(f"Model pre-warm failed (will retry on first use): {exc}")

    threading.Thread(target=_prewarm, daemon=True, name="model-prewarm").start()

    # Hotkey listener
    _start_hotkey_listener(cfg)

    # MCP server (optional, localhost only)
    from mcp_server import start_mcp_server_thread
    start_mcp_server_thread()

    # System tray (blocks main thread)
    logger.info("Engram fully started — opening system tray")
    from daemon import tray
    tray.run()
