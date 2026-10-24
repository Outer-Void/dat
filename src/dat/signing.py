"""Detached GPG signatures for written reports.

There is no fallback. The pre-4.0 code wrote a bare SHA-256 into `<file>.asc` when gpg was missing,
which looked like a signature but proved nothing. Now signing either produces a real signature or fails.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from dat.errors import DatError, ErrorCode

GPG_TIMEOUT_S = 120


def sign(path: Path, *, key: str | None = None) -> Path:
    gpg = shutil.which("gpg")
    if gpg is None:
        raise DatError(
            ErrorCode.SIGNING_FAILED, "gpg is not installed; nothing was signed.", details={"file": path.name}
        )
    signature = path.with_name(path.name + ".asc")
    argv = [gpg, "--batch", "--yes", "--armor", "--detach-sign", "--output", str(signature)]
    if key:
        argv += ["--local-user", key]
    try:
        proc = subprocess.run([*argv, str(path)], capture_output=True, timeout=GPG_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        raise DatError(ErrorCode.SIGNING_FAILED, "gpg timed out.", details={"file": path.name}) from None
    if proc.returncode != 0 or not signature.is_file():
        signature.unlink(missing_ok=True)
        raise DatError(
            ErrorCode.SIGNING_FAILED,
            "gpg could not sign the file.",
            details={"file": path.name, "exit_code": proc.returncode},
        )
    return signature


def verify(path: Path, signature: Path) -> bool:
    gpg = shutil.which("gpg")
    if gpg is None or not path.is_file() or not signature.is_file():
        return False
    try:
        proc = subprocess.run(
            [gpg, "--batch", "--verify", str(signature), str(path)],
            capture_output=True,
            timeout=GPG_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0
