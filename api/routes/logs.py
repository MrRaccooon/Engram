"""
Frontend log ingestion endpoint.

POST /api/logs  — accepts a batch of structured log events from the
                  frontend sessionLogger and writes them to loguru
                  so everything ends up in the same log file.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

router = APIRouter(tags=["logs"])


class LogBatch(BaseModel):
    events: list[dict[str, Any]]


@router.post("/logs")
async def ingest_logs(batch: LogBatch) -> dict:
    for event in batch.events:
        cat = event.get("cat", "?")
        action = event.get("action", "?")
        detail = event.get("detail", "")
        ms = event.get("ms")
        sid = event.get("sid", "?")

        parts = [f"[frontend:{sid}] {cat} | {action}"]
        if detail:
            parts.append(str(detail))
        if ms is not None:
            parts.append(f"{ms}ms")

        logger.info(" ".join(parts))

    return {"accepted": len(batch.events)}
