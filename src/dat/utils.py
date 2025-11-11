"""Utility helpers for the Dev Audit Tool."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from colorama import Fore, Style
from colorama import init as colorama_init


try:  # pragma: no cover - python-magic is optional on Windows
    import magic  # type: ignore
except Exception:  # pragma: no cover
    magic = None  # type: ignore

DEFAULT_ENCODING = "utf-8"
ENCODING_FALLBACKS = ("utf-8", "utf-8-sig", "latin-1", "utf-16")

colorama_init(strip=False, convert=False, autoreset=True)


@dataclass(frozen=True)
class TerminalStyle:
    """Reusable terminal style snippets."""

    success: str = Fore.GREEN
    warning: str = Fore.YELLOW
    error: str = Fore.RED
    reset: str = Style.RESET_ALL


TERMINAL_STYLE = TerminalStyle()


def detect_encoding(path: Path) -> str:
    """Best-effort encoding detection for *path*."""

    for encoding in ENCODING_FALLBACKS:
        try:
            path.read_text(encoding=encoding)
            return encoding
        except UnicodeDecodeError:
            continue
        except OSError:
            break
    return DEFAULT_ENCODING


def read_text(path: Path) -> str:
    """Load text content handling binary files gracefully."""

    for encoding in ENCODING_FALLBACKS:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    # Fallback to latin-1 to preserve bytes
    with path.open("r", encoding="latin-1", errors="ignore") as handle:
        return handle.read()


def is_binary(path: Path) -> bool:
    """Return True if the file is binary."""

    if magic is not None:
        with suppress(Exception):
            mime = magic.from_file(str(path), mime=True)  # type: ignore[attr-defined]
            if mime:
                return not mime.startswith("text/")
    with suppress(OSError):
        chunk = path.read_bytes()[:1024]
        if b"\0" in chunk:
            return True
    return False


def terminal_width(default: int = 80) -> int:
    """Return the terminal width or *default* when unavailable."""

    with suppress(OSError):
        return shutil.get_terminal_size((default, 20)).columns
    return default


def color_text(text: str, colour: str | None) -> str:
    """Wrap *text* with the provided colour, resetting afterwards."""

    if not colour:
        return text
    return f"{colour}{text}{TERMINAL_STYLE.reset}"


def iter_ignore_patterns(patterns: Sequence[str]) -> Iterator[str]:
    """Yield ignore patterns while filtering empty values."""

    for pattern in patterns:
        if pattern:
            yield pattern


def atomic_write(path: Path, data: bytes) -> None:
    """Atomically persist *data* to *path*."""

    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, dir=str(target.parent)) as handle:
        handle.write(data)
        temp_name = Path(handle.name)
    temp_name.chmod(stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temp_name, target)


def run_gpg_sign(data_path: Path, output_path: Path) -> bool:
    """Attempt to sign *data_path* using gpg, writing to *output_path*."""

    command = [
        "gpg",
        "--armor",
        "--output",
        str(output_path),
        "--detach-sign",
        str(data_path),
    ]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
    except FileNotFoundError:
        return False
    if completed.returncode != 0:
        return False
    return output_path.exists()


def load_json(path: Path) -> dict:
    """Load JSON content from *path* returning an empty dict on failure."""

    with suppress(OSError, json.JSONDecodeError):
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def merge_dicts(base: dict, override: dict) -> dict:
    """Deep merge dictionaries with override precedence."""

    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def ensure_home_config(path: Path) -> None:
    """Ensure *path* exists with secure permissions."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("[]", encoding="utf-8")
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


_SECRET_PATTERNS = [
    r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*['\"][A-Za-z0-9_\-\/+=]{16,}['\"]",
    r"(?i)password\s*[:=]\s*['\"][^'\"]{4,}['\"]",
    r"(?i)bearer\s+[A-Za-z0-9_\-\.=+/]{16,}",
    r"['\"][A-Za-z0-9_\-]{24,}['\"]",  # generic long tokens
]


def _strip_leading_inline_flags(p: str) -> str:
    """
    Remove leading inline flag blocks like (?i), (?im), (?imsx), etc.,
    so we can join multiple patterns safely under one compiled regex.
    """
    return re.sub(r"^\(\?[imsx]+\)", "", p)


def _compile_union(patterns, flags=0) -> re.Pattern[str]:
    cleaned = []
    for p in patterns:
        p = _strip_leading_inline_flags(p)
        cleaned.append(f"(?:{p})")
    return re.compile("|".join(cleaned), flags)


# Case-insensitive by default; add MULTILINE if your rules expect ^ / $ on lines.
_SECRET_RX = _compile_union(_SECRET_PATTERNS, re.IGNORECASE | re.MULTILINE)


def mask_secrets(text: str) -> str:
    """Redact likely secrets from text with a neutral marker."""
    return _SECRET_RX.sub("•••", text)


def lang_from_suffix(path: str) -> str:
    """Detect language from file extension for syntax highlighting."""
    s = Path(path).suffix.lstrip(".").lower()
    # some common tweaks for nicer fences
    return {"py": "python", "sh": "bash", "yml": "yaml"}.get(s, s or "")


def safe_mkdir(path: Path) -> None:
    """
    Create a directory if it doesn't exist.
    If a *file* exists at that path, raise a clear error instead of crashing deep in mkdir.
    """
    if path.exists() and path.is_file():
        raise RuntimeError(
            f"Cannot create directory '{path}': a file with that name already exists."
        )
    path.mkdir(parents=True, exist_ok=True)


def human_readable_size(size_bytes: int) -> str:
    """Convert bytes to human readable format."""
    if size_bytes == 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    size = float(size_bytes)

    while size >= 1024.0 and unit_index < len(units) - 1:
        size /= 1024.0
        unit_index += 1

    return f"{size:.1f} {units[unit_index]}"


def is_hidden(path: Path) -> bool:
    """Check if a file or directory is hidden."""
    if path.name.startswith("."):
        return True

    # Check for hidden files on Windows
    if os.name == "nt":
        try:
            import ctypes

            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
            return attrs != -1 and bool(attrs & 2)  # FILE_ATTRIBUTE_HIDDEN
        except (AttributeError, OSError):
            pass

    return False


def find_files(
    root: Path,
    patterns: Sequence[str] | None = None,
    ignore_patterns: Sequence[str] | None = None,
    max_depth: int | None = None,
) -> Iterator[Path]:
    """
    Find files matching patterns while ignoring specified patterns.

    Args:
        root: Root directory to search
        patterns: File patterns to include (e.g., ["*.py", "*.js"])
        ignore_patterns: Patterns to exclude (e.g., ["__pycache__", "*.log"])
        max_depth: Maximum directory depth to search
    """
    import fnmatch

    patterns = patterns or ["*"]
    ignore_patterns = ignore_patterns or []

    def should_include(path: Path) -> bool:
        rel_path = path.relative_to(root) if path.is_relative_to(root) else path
        path_str = str(rel_path)

        # Check ignore patterns first
        for ignore_pattern in ignore_patterns:
            if fnmatch.fnmatch(path_str, ignore_pattern):
                return False

        # Check include patterns
        for pattern in patterns:
            if fnmatch.fnmatch(path_str, pattern):
                return True

        return False

    def _scan(current: Path, depth: int) -> Iterator[Path]:
        if max_depth is not None and depth > max_depth:
            return

        try:
            for item in current.iterdir():
                if item.is_dir():
                    if should_include(item):
                        yield from _scan(item, depth + 1)
                elif should_include(item):
                    yield item
        except (PermissionError, OSError):
            # Skip directories we can't access
            pass

    yield from _scan(root, 0)


def truncate_text(text: str, max_length: int, ellipsis: str = "...") -> str:
    """Truncate text to maximum length with ellipsis if needed."""
    if len(text) <= max_length:
        return text
    return text[: max_length - len(ellipsis)] + ellipsis


def get_file_hash(path: Path, algorithm: str = "sha256") -> str:
    """Calculate file hash for change detection."""
    import hashlib

    hash_obj = hashlib.new(algorithm)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_obj.update(chunk)

    return hash_obj.hexdigest()


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human readable format."""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}m {secs:.0f}s"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours}h {minutes}m"
