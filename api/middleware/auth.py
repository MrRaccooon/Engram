"""
Local PIN authentication middleware.

When `privacy.require_local_auth: true` in config.yaml, all /api/* routes
(except /api/auth/unlock and /api/health) require an X-Engram-Session header
containing a valid session token.

The token is a HMAC-SHA256 of (PIN + machine_id), generated at unlock time
and stored only in process memory — never written to disk.

Endpoints:
  POST /api/auth/unlock   {"pin": "1234"}  → {"token": "..."}
  POST /api/auth/lock                      → {"status": "locked"}
  GET  /api/auth/status                    → {"locked": bool}
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from loguru import logger

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "config.yaml"

# In-memory session store: token_hex → True
# In a real multi-user system this would be a proper store, but Engram
# is single-user so one active token is sufficient.
_active_token: Optional[str] = None

router = APIRouter(tags=["auth"])


def _load_auth_config() -> tuple[bool, str]:
    """Returns (require_auth, stored_pin_hash)."""
    try:
        with open(_CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        require = cfg.get("privacy", {}).get("require_local_auth", False)
        pin_hash = cfg.get("privacy", {}).get("pin_hash", "")
        return require, pin_hash
    except Exception:
        return False, ""


def _machine_id() -> str:
    """Return a stable per-machine identifier (Windows machine GUID)."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
        )
        machine_guid, _ = winreg.QueryValueEx(key, "MachineGuid")
        return machine_guid
    except Exception:
        return "fallback-machine-id"


def _make_token(pin: str) -> str:
    """Derive a session token from the PIN and machine ID."""
    secret = (_machine_id() + pin).encode("utf-8")
    return hmac.new(secret, os.urandom(16), hashlib.sha256).hexdigest()


def _verify_pin(pin: str, stored_hash: str) -> bool:
    """Verify a PIN against its stored SHA-256 hash."""
    given_hash = hashlib.sha256(pin.encode("utf-8")).hexdigest()
    return hmac.compare_digest(given_hash, stored_hash)


# ── FastAPI dependency ────────────────────────────────────────────────────────

async def require_session(
    x_engram_session: Optional[str] = Header(default=None),
) -> None:
    """
    FastAPI dependency that enforces authentication when enabled.
    Add to any route: `_: None = Depends(require_session)`
    """
    require, _ = _load_auth_config()
    if not require:
        return  # auth disabled, pass through

    if not _active_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Engram is locked. POST /api/auth/unlock to authenticate.",
        )

    if x_engram_session != _active_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token.",
        )


# ── Routes ────────────────────────────────────────────────────────────────────

class UnlockRequest(BaseModel):
    pin: str


@router.post("/auth/unlock")
async def unlock(req: UnlockRequest) -> dict:
    """Verify the PIN and issue a session token."""
    global _active_token
    require, stored_hash = _load_auth_config()

    if not require:
        return {"token": "auth-disabled", "message": "Authentication is disabled"}

    if not stored_hash:
        # First-time setup: store the hash of whatever PIN is provided
        new_hash = hashlib.sha256(req.pin.encode("utf-8")).hexdigest()
        _save_pin_hash(new_hash)
        stored_hash = new_hash
        logger.info("Auth: PIN set for the first time")

    if not _verify_pin(req.pin, stored_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect PIN.",
        )

    token = _make_token(req.pin)
    _active_token = token
    logger.info("Auth: session unlocked")
    return {"token": token}


@router.post("/auth/lock")
async def lock() -> dict:
    """Invalidate the current session."""
    global _active_token
    _active_token = None
    logger.info("Auth: session locked")
    return {"status": "locked"}


@router.get("/auth/status")
async def auth_status() -> dict:
    require, stored_hash = _load_auth_config()
    return {
        "auth_enabled": require,
        "locked": require and _active_token is None,
        "pin_configured": bool(stored_hash),
    }


def _save_pin_hash(pin_hash: str) -> None:
    """Persist the PIN hash into config.yaml under privacy.pin_hash."""
    try:
        with open(_CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        cfg.setdefault("privacy", {})["pin_hash"] = pin_hash
        with open(_CONFIG_PATH, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    except Exception as exc:
        logger.error(f"Failed to save PIN hash: {exc}")
