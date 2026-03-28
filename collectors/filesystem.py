"""
Filesystem watcher collector.

Uses watchdog to monitor configured directories for file open/create/modify
events. When a supported document type is touched, its text content is
extracted and enqueued for embedding.

Supported formats: .txt, .md, .py, .js, .ts, .pdf, .docx
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger
from watchdog.events import FileModifiedEvent, FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from pipeline import queue_manager

_observer: Optional[Observer] = None


def _extract_text(path: Path) -> Optional[str]:
    """Extract plain text from a file based on its extension."""
    suffix = path.suffix.lower()
    try:
        if suffix in {".txt", ".md", ".py", ".js", ".ts", ".csv", ".json", ".yaml", ".yml"}:
            return path.read_text(encoding="utf-8", errors="replace")

        if suffix == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)

        if suffix == ".docx":
            from docx import Document
            doc = Document(str(path))
            return "\n".join(p.text for p in doc.paragraphs)

    except Exception as exc:
        logger.debug(f"Text extraction failed for {path.name}: {exc}")

    return None


class _EngramFileHandler(FileSystemEventHandler):
    def __init__(self, watched_extensions: list[str], excluded_apps: list[str]) -> None:
        super().__init__()
        self._extensions = {ext.lower() for ext in watched_extensions}
        self._excluded_apps = excluded_apps
        self._lock = threading.Lock()
        # Debounce: track last-enqueued path+timestamp to avoid double-fires
        self._last: dict[str, datetime] = {}

    def _should_process(self, path: Path) -> bool:
        if path.suffix.lower() not in self._extensions:
            return False
        if not path.is_file():
            return False
        # Skip hidden files and Engram's own data directory
        if any(part.startswith(".") for part in path.parts):
            return False
        # Debounce: ignore events within 5 s of the last event for the same file
        now = datetime.utcnow()
        with self._lock:
            last = self._last.get(str(path))
            if last and (now - last).total_seconds() < 5:
                return False
            self._last[str(path)] = now
        return True

    def _handle(self, event_path: str) -> None:
        path = Path(event_path)
        if not self._should_process(path):
            return
        content = _extract_text(path)
        if not content or not content.strip():
            return
        queue_manager.enqueue(
            source_type="file",
            timestamp=datetime.utcnow(),
            raw_path=str(path),
            content=content,
            window_title=path.name,
            app_name="filesystem",
        )
        logger.debug(f"File indexed: {path.name}")

    def on_modified(self, event: FileModifiedEvent) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._handle(event.src_path)

    def on_created(self, event: FileCreatedEvent) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._handle(event.src_path)


def start(
    watched_dirs: list[str],
    watched_extensions: list[str],
    excluded_apps: list[str] | None = None,
) -> None:
    """Start the filesystem observer in a background daemon thread."""
    global _observer
    if _observer is not None:
        logger.warning("Filesystem watcher already running")
        return

    handler = _EngramFileHandler(
        watched_extensions=watched_extensions,
        excluded_apps=excluded_apps or [],
    )
    _observer = Observer()
    for raw_dir in watched_dirs:
        expanded = Path(raw_dir).expanduser()
        if expanded.is_dir():
            _observer.schedule(handler, str(expanded), recursive=True)
            logger.info(f"Watching: {expanded}")
        else:
            logger.warning(f"Watched dir not found, skipping: {expanded}")

    _observer.daemon = True
    _observer.start()
    logger.info("Filesystem watcher started")


def stop() -> None:
    global _observer
    if _observer:
        _observer.stop()
        _observer.join()
        _observer = None
        logger.info("Filesystem watcher stopped")
