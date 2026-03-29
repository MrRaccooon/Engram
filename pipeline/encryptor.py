"""
At-rest encryption for Engram data.

Uses Windows DPAPI to generate a machine-bound master key that is
cryptographically tied to the Windows user account. The key is stored
encrypted by DPAPI at ~/.engram/master.key.dpapi. Only the same Windows
user on the same machine can decrypt it — physical disk theft alone
cannot expose the data.

The master key is then used with Fernet (AES-128-CBC + HMAC-SHA256)
to encrypt text content fields in SQLite and binary files (thumbnails).

Falls back to a warning log when:
  - DPAPI is unavailable (non-Windows platform)
  - privacy.encrypt_at_rest is false in config.yaml

Usage:
    from pipeline.encryptor import encrypt_text, decrypt_text

    stored = encrypt_text("my secret content")
    original = decrypt_text(stored)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from loguru import logger

_fernet = None
_enabled: Optional[bool] = None

_KEY_FILE_NAME = "master.key.dpapi"
_ENGRAM_PREFIX = b"ENC:"


def _is_enabled() -> bool:
    global _enabled
    if _enabled is None:
        try:
            from pathlib import Path as P
            import yaml
            cfg_path = P(__file__).parent.parent / "config" / "config.yaml"
            with open(cfg_path) as f:
                cfg = yaml.safe_load(f)
            _enabled = cfg.get("privacy", {}).get("encrypt_at_rest", False)
        except Exception:
            _enabled = False
    return _enabled


def _get_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet

    from cryptography.fernet import Fernet

    key_path = Path.home() / ".engram" / _KEY_FILE_NAME
    key_path.parent.mkdir(parents=True, exist_ok=True)

    if key_path.exists():
        # Load and decrypt the stored key using DPAPI
        encrypted_key = key_path.read_bytes()
        raw_key = _dpapi_decrypt(encrypted_key)
    else:
        # Generate a new Fernet key and protect it with DPAPI
        raw_key = Fernet.generate_key()
        encrypted_key = _dpapi_encrypt(raw_key)
        key_path.write_bytes(encrypted_key)
        logger.info(f"Encryptor: new master key generated at {key_path}")

    _fernet = Fernet(raw_key)
    return _fernet


def _dpapi_encrypt(data: bytes) -> bytes:
    """Encrypt bytes using Windows DPAPI (machine+user binding)."""
    try:
        import win32crypt
        encrypted, _ = win32crypt.CryptProtectData(
            data,
            "Engram master key",
            None,
            None,
            None,
            0,
        )
        return encrypted
    except ImportError:
        # Non-Windows: fall back to storing the key with file-system protection only
        logger.warning(
            "DPAPI not available (non-Windows). "
            "Key stored with filesystem permissions only."
        )
        return data


def _dpapi_decrypt(data: bytes) -> bytes:
    """Decrypt DPAPI-protected bytes."""
    try:
        import win32crypt
        _, decrypted = win32crypt.CryptUnprotectData(data, None, None, None, 0)
        return decrypted
    except ImportError:
        return data


# ── Public API ────────────────────────────────────────────────────────────────

def encrypt_text(text: str) -> str:
    """
    Encrypt a string. Returns the ciphertext prefixed with 'ENC:' so
    decrypt_text can detect and skip already-plain or null values.
    """
    if not _is_enabled() or not text:
        return text
    try:
        fernet = _get_fernet()
        cipher = fernet.encrypt(text.encode("utf-8"))
        return _ENGRAM_PREFIX.decode() + cipher.decode("utf-8")
    except Exception as exc:
        logger.warning(f"Encryption failed, storing plaintext: {exc}")
        return text


def decrypt_text(text: str) -> str:
    """
    Decrypt a string previously encrypted by encrypt_text.
    Passes through non-encrypted values unchanged.
    """
    if not text or not text.startswith(_ENGRAM_PREFIX.decode()):
        return text
    try:
        fernet = _get_fernet()
        cipher = text[len(_ENGRAM_PREFIX):].encode("utf-8")
        return fernet.decrypt(cipher).decode("utf-8")
    except Exception as exc:
        logger.warning(f"Decryption failed: {exc}")
        return text


def encrypt_file(path: Path) -> None:
    """Encrypt a file in-place. Skips if encryption is disabled."""
    if not _is_enabled():
        return
    try:
        fernet = _get_fernet()
        data = path.read_bytes()
        if data[:4] == _ENGRAM_PREFIX:
            return  # already encrypted
        encrypted = _ENGRAM_PREFIX + fernet.encrypt(data)
        path.write_bytes(encrypted)
    except Exception as exc:
        logger.warning(f"File encryption failed for {path.name}: {exc}")


def decrypt_file(path: Path) -> bytes:
    """
    Read and decrypt a file. Returns raw bytes.
    If the file is not encrypted, returns raw bytes unchanged.
    """
    try:
        data = path.read_bytes()
        if not data.startswith(_ENGRAM_PREFIX):
            return data
        fernet = _get_fernet()
        return fernet.decrypt(data[len(_ENGRAM_PREFIX):])
    except Exception as exc:
        logger.warning(f"File decryption failed for {path.name}: {exc}")
        return path.read_bytes()
