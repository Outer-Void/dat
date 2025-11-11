"""Artifact signing helpers."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path


class SigningError(RuntimeError):
    """Raised when an artifact cannot be signed."""


def _ensure_artifact(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(path)


def sign_artifact(path: Path) -> Path:
    """Create a detached signature for *path* using GPG when available."""

    _ensure_artifact(path)
    signer = shutil.which("gpg")
    signature_path = path.with_suffix(path.suffix + ".asc")
    if signer:
        result = subprocess.run(
            [
                signer,
                "--batch",
                "--yes",
                "--armor",
                "--detach-sign",
                "--output",
                str(signature_path),
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and signature_path.exists():
            return signature_path
    # fallback: generate sha256 digest file
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    signature_path.write_text(digest, encoding="utf-8")
    return signature_path


def verify_signature(path: Path, signature_path: Path) -> bool:
    """Validate *signature_path* against *path* using GPG or digest fallback."""

    if not path.exists() or not signature_path.exists():
        return False

    signer = shutil.which("gpg")
    if signer:
        result = subprocess.run(
            [signer, "--verify", str(signature_path), str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True

    try:
        expected = signature_path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not expected:
        return False
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest == expected


__all__ = ["SigningError", "sign_artifact", "verify_signature"]
