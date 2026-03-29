"""
Engram MCP Server.

Exposes Engram's memory as Model Context Protocol (MCP) tools so that
Claude Desktop, Cursor, Continue.dev, and any other MCP-compatible
AI assistant can query your personal memory.

Strictly binds to 127.0.0.1 — data never leaves the local machine.

Tools exposed:
  engram_search(query, top_k)    — semantic search over captures
  engram_ask(question)           — privacy-pipeline + frontier API answer
  engram_timeline(date)          — structured day summary
  engram_insights(date)          — consolidated daily insight summaries

Configuration (config.yaml):
  mcp:
    enabled: true
    host: "127.0.0.1"
    port: 8766

Launch:
  python mcp_server.py                  # standalone
  # Or automatically via main.py when mcp.enabled: true
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional

import yaml
from loguru import logger

_CONFIG_PATH = Path(__file__).parent / "config" / "config.yaml"


def _load_config() -> dict:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── Tool implementations ──────────────────────────────────────────────────────

def _tool_search(query: str, top_k: int = 10) -> dict[str, Any]:
    """Semantic search over all captures."""
    from pipeline import embedder, reranker
    from storage import vector_db

    top_k = min(max(top_k, 1), 20)
    retrieval_k = min(top_k * 5, 50)

    text_vec = embedder.embed_text(query)
    text_results = vector_db.query_text(text_vec, top_k=retrieval_k)

    visual_vec = embedder.embed_query_text_clip(query)
    visual_results = []
    if visual_vec:
        visual_results = vector_db.query_visual(visual_vec, top_k=retrieval_k // 2)

    merged: dict[str, dict] = {}
    for r in text_results:
        merged[r["id"]] = r
    for r in visual_results:
        if r["id"] not in merged:
            merged[r["id"]] = r

    reranked = reranker.rerank(
        query=query,
        candidates=list(merged.values()),
        top_n=top_k,
    )

    return {
        "query": query,
        "results": [
            {
                "source_type": r.get("source_type"),
                "timestamp": r.get("timestamp"),
                "content": r.get("content_preview", ""),
                "app": r.get("app_name"),
                "url": r.get("url"),
                "relevance": round(r.get("rerank_score", r.get("score", 0)), 3),
            }
            for r in reranked
        ],
        "count": len(reranked),
    }


def _tool_ask(question: str) -> dict[str, Any]:
    """Privacy-preserving answer from the intelligence pipeline."""
    from pipeline import embedder, reranker, intelligence
    from storage import vector_db

    text_vec = embedder.embed_text(question)
    candidates = vector_db.query_text(text_vec, top_k=50)
    reranked = reranker.rerank(question, candidates, top_n=10)

    result = intelligence.ask(question, reranked)
    return result


def _tool_timeline(date: str) -> dict[str, Any]:
    """Return structured captures for a given day."""
    from storage import metadata_db
    rows = metadata_db.fetch_captures_for_day(date)
    return {
        "date": date,
        "count": len(rows),
        "captures": [
            {
                "source_type": r["source_type"],
                "timestamp": r["timestamp"],
                "content": (r["content"] or "")[:300],
                "app": r["app_name"],
                "window_title": r["window_title"],
                "url": r["url"],
            }
            for r in rows
        ],
    }


def _tool_insights(date: Optional[str] = None) -> dict[str, Any]:
    """Return consolidated daily insights."""
    from storage import metadata_db
    if date:
        rows = metadata_db.fetch_insights_for_day(date)
    else:
        rows = metadata_db.fetch_recent_insights(days=7)
    return {
        "insights": [
            {
                "date": r["date"],
                "session_start": r["session_start"],
                "session_end": r["session_end"],
                "summary": r["summary"],
                "topics": json.loads(r["topics"] or "[]"),
            }
            for r in rows
        ],
        "count": len(rows),
    }


# ── MCP message handling ──────────────────────────────────────────────────────

_TOOLS = {
    "engram_search": {
        "description": "Semantic search over your personal digital memory (screenshots, clipboard, browser history, files).",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "top_k": {"type": "integer", "default": 10, "description": "Number of results"},
            },
            "required": ["query"],
        },
    },
    "engram_ask": {
        "description": "Ask a question and get a synthesized answer from your personal memory, with privacy filtering.",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Natural language question"},
            },
            "required": ["question"],
        },
    },
    "engram_timeline": {
        "description": "Get all captured activity for a specific date.",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
            },
            "required": ["date"],
        },
    },
    "engram_insights": {
        "description": "Get consolidated daily insight summaries (last 7 days if no date given).",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Optional: YYYY-MM-DD"},
            },
        },
    },
}


def _handle_mcp_request(request: dict) -> dict:
    method = request.get("method", "")
    req_id = request.get("id")

    def ok(result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def err(code: int, msg: str) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": msg}}

    if method == "initialize":
        return ok({
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "engram", "version": "1.0.0"},
        })

    if method == "tools/list":
        tools = [
            {"name": name, "description": spec["description"], "inputSchema": spec["parameters"]}
            for name, spec in _TOOLS.items()
        ]
        return ok({"tools": tools})

    if method == "tools/call":
        params = request.get("params", {})
        tool_name = params.get("name", "")
        args = params.get("arguments", {})

        try:
            if tool_name == "engram_search":
                result = _tool_search(args["query"], args.get("top_k", 10))
            elif tool_name == "engram_ask":
                result = _tool_ask(args["question"])
            elif tool_name == "engram_timeline":
                result = _tool_timeline(args["date"])
            elif tool_name == "engram_insights":
                result = _tool_insights(args.get("date"))
            else:
                return err(-32601, f"Unknown tool: {tool_name}")

            return ok({
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
            })

        except Exception as exc:
            logger.error(f"MCP tool '{tool_name}' failed: {exc}")
            return err(-32603, str(exc))

    if method == "notifications/initialized":
        return None  # type: ignore[return-value]

    return err(-32601, f"Method not found: {method}")


# ── FastAPI-based MCP HTTP server ─────────────────────────────────────────────

def create_mcp_app():
    """Create a minimal FastAPI app serving the MCP protocol over HTTP."""
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    app = FastAPI(title="Engram MCP Server", docs_url=None, redoc_url=None)

    @app.post("/mcp")
    async def mcp_endpoint(request: Request):
        body = await request.json()
        response = _handle_mcp_request(body)
        if response is None:
            return JSONResponse(content={}, status_code=204)
        return JSONResponse(content=response)

    @app.get("/mcp/health")
    async def mcp_health():
        return {"status": "ok", "server": "engram-mcp"}

    return app


def start_mcp_server_thread() -> Optional[threading.Thread]:
    """
    Launch the MCP server in a daemon thread.
    Called from main.py when mcp.enabled: true.
    Returns the thread, or None if MCP is disabled.
    """
    cfg = _load_config()
    mcp_cfg = cfg.get("mcp", {})

    if not mcp_cfg.get("enabled", False):
        return None

    host = mcp_cfg.get("host", "127.0.0.1")
    port = int(mcp_cfg.get("port", 8766))

    # Enforce localhost-only binding
    if host not in ("127.0.0.1", "localhost"):
        logger.warning(f"MCP host '{host}' overridden to 127.0.0.1 for privacy")
        host = "127.0.0.1"

    def _run():
        import uvicorn
        # Init storage for MCP process (shares the same DB files)
        storage_cfg = cfg.get("storage", {})
        base = Path(storage_cfg.get("base_path", "~/.engram")).expanduser()
        from storage import metadata_db, vector_db
        metadata_db.init(base / "metadata.db")
        vector_db.init(base / "chromadb")

        mcp_app = create_mcp_app()
        logger.info(f"MCP server starting on {host}:{port}")
        uvicorn.run(mcp_app, host=host, port=port, log_level="warning")

    t = threading.Thread(target=_run, daemon=True, name="engram-mcp")
    t.start()
    logger.info(f"MCP server thread started (127.0.0.1:{port})")
    return t


# ── Standalone entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    cfg = _load_config()
    mcp_cfg = cfg.get("mcp", {})
    host = "127.0.0.1"  # always localhost
    port = int(mcp_cfg.get("port", 8766))

    storage_cfg = cfg.get("storage", {})
    base = Path(storage_cfg.get("base_path", "~/.engram")).expanduser()
    from storage import metadata_db, vector_db
    metadata_db.init(base / "metadata.db")
    vector_db.init(base / "chromadb")

    mcp_app = create_mcp_app()
    logger.add("logs/engram_mcp_{time}.log", rotation="10 MB", retention="7 days")
    logger.info(f"Starting Engram MCP server on {host}:{port}")
    uvicorn.run(mcp_app, host=host, port=port)
