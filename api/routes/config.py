"""
Config and data management routes.

GET    /api/config           — read current config.yaml as JSON
PUT    /api/config           — write updated settings to config.yaml
DELETE /api/data             — GDPR-style deletion of captures before a date
GET    /api/export           — export captures as newline-delimited JSON
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from storage import metadata_db, vector_db
from storage.retention import run as run_retention

router = APIRouter(tags=["config"])

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "config.yaml"


def _load_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _save_config(data: dict) -> None:
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


# ── GET /config ───────────────────────────────────────────────────────────────

@router.get("/config")
async def get_config() -> dict:
    try:
        return _load_config()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read config: {exc}")


# ── PUT /config ───────────────────────────────────────────────────────────────

class ConfigUpdateRequest(BaseModel):
    capture: dict[str, Any] | None = None
    storage: dict[str, Any] | None = None
    embedding: dict[str, Any] | None = None
    chunking: dict[str, Any] | None = None
    api: dict[str, Any] | None = None
    privacy: dict[str, Any] | None = None


@router.put("/config")
async def update_config(req: ConfigUpdateRequest) -> dict:
    """
    Deep-merge the provided fields into config.yaml.
    Only the keys present in the request body are updated.
    """
    try:
        cfg = _load_config()
        update = req.model_dump(exclude_none=True)
        for section, values in update.items():
            if section in cfg and isinstance(cfg[section], dict):
                cfg[section].update(values)
            else:
                cfg[section] = values
        _save_config(cfg)
        return {"status": "updated", "config": cfg}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update config: {exc}")


# ── DELETE /data ──────────────────────────────────────────────────────────────

@router.delete("/data")
async def delete_data(
    before: str = Query(..., description="Delete captures before this ISO date (YYYY-MM-DD)"),
) -> dict:
    """
    GDPR-style deletion: remove all captures (SQLite rows, ChromaDB vectors,
    thumbnail files) created before the given date.
    """
    try:
        cutoff_iso = f"{before}T00:00:00"
        deleted = metadata_db.delete_captures_before(cutoff_iso)
        return {
            "status": "deleted",
            "captures_removed": deleted,
            "cutoff": before,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Deletion failed: {exc}")


# ── GET /export ───────────────────────────────────────────────────────────────

@router.get("/export")
async def export_data(
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
) -> StreamingResponse:
    """
    Export captures as newline-delimited JSON (NDJSON).
    Streams the response so large exports don't OOM the server.
    """
    import json

    from storage.metadata_db import _connect

    def _generate():
        with _connect() as conn:
            where_clauses = []
            params = []
            if from_date:
                where_clauses.append("timestamp >= ?")
                params.append(from_date)
            if to_date:
                where_clauses.append("timestamp <= ?")
                params.append(to_date + "T23:59:59")
            sql = "SELECT * FROM captures"
            if where_clauses:
                sql += " WHERE " + " AND ".join(where_clauses)
            sql += " ORDER BY timestamp ASC"
            cursor = conn.execute(sql, params)
            for row in cursor:
                yield json.dumps(dict(row)) + "\n"

    return StreamingResponse(
        _generate(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=engram_export.ndjson"},
    )


# ── POST /retention ───────────────────────────────────────────────────────────

@router.post("/retention/run")
async def run_retention_now() -> dict:
    """Manually trigger the retention policy (normally runs daily)."""
    try:
        cfg = _load_config()
        base = Path(cfg["storage"]["base_path"]).expanduser()
        run_retention(
            base_path=base,
            retention_days=cfg["storage"].get("retention_days", 90),
            max_storage_gb=cfg["storage"].get("max_storage_gb", 10),
        )
        return {"status": "ok", "message": "Retention policy executed"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
