"""
FastAPI application factory.

Handles startup/shutdown lifecycle:
  - Initialises SQLite + ChromaDB on startup
  - Registers all route modules
  - Exposes CORS for the local React dev server (localhost:5173)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from api.routes import capture, config, search, ask, activity, insights
from api.middleware import auth as auth_middleware

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"


def _load_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Initialise storage on startup; clean up on shutdown."""
    cfg = _load_config()
    base = Path(cfg["storage"]["base_path"]).expanduser()

    from storage import metadata_db, vector_db
    metadata_db.init(base / "metadata.db")
    vector_db.init(base / "chromadb")

    logger.info("Engram API started")
    yield
    logger.info("Engram API shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Engram",
        description="Local-first semantic lifelogging engine",
        version="0.1.0",
        lifespan=_lifespan,
        docs_url="/api/docs",
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",   # Vite dev server
            "http://127.0.0.1:5173",
            "http://localhost:8765",
            "http://127.0.0.1:8765",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(search.router,    prefix="/api")
    app.include_router(capture.router,   prefix="/api")
    app.include_router(config.router,    prefix="/api")
    app.include_router(ask.router,                  prefix="/api")
    app.include_router(activity.router,             prefix="/api")
    app.include_router(insights.router,             prefix="/api")
    app.include_router(auth_middleware.router,      prefix="/api")

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    cfg = _load_config()
    api_cfg = cfg.get("api", {})
    uvicorn.run(
        "api.main:app",
        host=api_cfg.get("host", "127.0.0.1"),
        port=api_cfg.get("port", 8765),
        reload=False,
        log_level="info",
    )
