"""Encrypted audit logging."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List

from cryptography.fernet import Fernet


def config_dir() -> Path:
    return Path(os.environ.get("DAT_CONFIG_DIR", Path.home() / ".config" / "dat"))


def log_file() -> Path:
    return config_dir() / "auditlog.jsonl"


def key_file() -> Path:
    return config_dir() / "auditlog.key"


def _ensure_key() -> bytes:
    key_path = key_file()
    cfg_dir = key_path.parent
    cfg_dir.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        return key_path.read_bytes()
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    os.chmod(key_path, 0o600)
    return key


def _decrypt_entries(data: Iterable[bytes], key: bytes) -> List[Dict[str, Any]]:
    fernet = Fernet(key)
    entries: List[Dict[str, Any]] = []
    for raw in data:
        line = raw.strip()
        if not line:
            continue
        try:
            decrypted = fernet.decrypt(line)
            entries.append(json.loads(decrypted.decode("utf-8")))
        except Exception:  # pragma: no cover - corrupt entries are skipped
            continue
    return entries


def _write_entries(entries: Iterable[Dict[str, Any]], key: bytes) -> None:
    log_path = log_file()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fernet = Fernet(key)
    with log_path.open("wb") as handle:
        for entry in entries:
            token = fernet.encrypt(json.dumps(entry, sort_keys=True).encode("utf-8"))
            handle.write(token + b"\n")


def append_encrypted_log(payload: Dict[str, Any]) -> None:
    """Encrypt and persist *payload* into the audit log."""

    key = _ensure_key()
    log_path = log_file()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    token = Fernet(key).encrypt(json.dumps(payload, sort_keys=True).encode("utf-8"))
    with log_path.open("ab") as handle:
        handle.write(token + b"\n")


def read_audit_log() -> List[Dict[str, Any]]:
    """Return decrypted audit log entries."""

    log_path = log_file()
    key_path = key_file()
    if not log_path.exists() or not key_path.exists():
        return []
    key = key_path.read_bytes()
    with log_path.open("rb") as handle:
        return _decrypt_entries(handle, key)


def rotate_audit_key() -> None:
    """Rotate the encryption key while preserving existing log entries."""

    key_path = key_file()
    if not key_path.exists():
        _ensure_key()
        return
    old_key = key_path.read_bytes()
    log_path = log_file()
    existing: List[Dict[str, Any]] = []
    if log_path.exists():
        with log_path.open("rb") as handle:
            existing = _decrypt_entries(handle, old_key)
    new_key = Fernet.generate_key()
    key_path.write_bytes(new_key)
    os.chmod(key_path, 0o600)
    if existing:
        _write_entries(existing, new_key)
    else:
        log_path.write_text("", encoding="utf-8")


def log_system_event(event: str, payload: Dict[str, Any]) -> None:
    """Helper used during package initialisation to record system events."""

    entry = dict(payload)
    entry["event"] = event
    append_encrypted_log(entry)


__all__ = [
    "append_encrypted_log",
    "config_dir",
    "key_file",
    "log_file",
    "log_system_event",
    "read_audit_log",
    "rotate_audit_key",
]

