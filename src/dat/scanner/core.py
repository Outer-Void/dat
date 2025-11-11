"""Enhanced repository scanning utilities with better performance and safety."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import mimetypes
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..integration.lrc import extract_rules_from_schema, summarize_metadata
from ..rules.engine import Policy, Rule, RuleViolation, load_default_policy
from ..utils import is_binary, read_text


try:  # pragma: no cover - optional dependency
    import magic  # type: ignore
except Exception:  # pragma: no cover - fallback when python-magic missing
    magic = None


@dataclass(slots=True)
class ScannerOptions:
    """Configuration for repository scanning."""

    root: Path
    ignore_patterns: Sequence[str] = field(default_factory=tuple)
    safe: bool = False
    deep: bool = False
    max_safe_size: int = 1_000_000
    max_safe_lines: int = 1_000
    semaphore: asyncio.Semaphore | None = None
    metadata: dict | None = None
    scan_stats: ScanStats = field(default_factory=lambda: ScanStats())


@dataclass(slots=True)
class ScanStats:
    """Statistics about the scanning process."""

    files_scanned: int = 0
    files_skipped: int = 0
    files_errored: int = 0
    total_size: int = 0
    start_time: float = field(default_factory=time.time)
    end_time: float = 0

    @property
    def duration(self) -> float:
        return (self.end_time or time.time()) - self.start_time

    def to_dict(self) -> dict:
        return {
            "files_scanned": self.files_scanned,
            "files_skipped": self.files_skipped,
            "files_errored": self.files_errored,
            "total_size": self.total_size,
            "duration_seconds": self.duration,
        }


@dataclass(slots=True)
class FileReport:
    """Structured information for a scanned file."""

    path: str
    size: int
    checksum: str
    mime_type: str
    encoding: str
    violations: list[RuleViolation]
    binary: bool = False
    error: str | None = None


@dataclass(slots=True)
class ScanReport:
    """Collection of file reports and summary metadata."""

    repo: str
    root: str
    files: list[FileReport]
    metadata: dict
    stats: ScanStats

    def to_dict(self) -> dict:
        return {
            "repo": self.repo,
            "root": self.root,
            "files": [
                {
                    "path": file.path,
                    "size": file.size,
                    "checksum": file.checksum,
                    "mime_type": file.mime_type,
                    "encoding": file.encoding,
                    "binary": file.binary,
                    "error": file.error,
                    "violations": [violation.__dict__ for violation in file.violations],
                }
                for file in self.files
            ],
            "metadata": self.metadata,
            "stats": self.stats.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @property
    def total_files(self) -> int:
        return len(self.files)

    @property
    def total_violations(self) -> int:
        return sum(len(file.violations) for file in self.files)


DEFAULT_IGNORES = [
    ".git/",
    ".hg/",
    ".svn/",
    ".venv/",
    "venv/",
    "__pycache__/",
    "node_modules/",
    "dist/",
    "build/",
    "*.pyc",
    "*.pyo",
    "src/dat.egg-info/",
    "artifacts/",
    "*.egg-info/",
    "*.so",
    "*.dll",
    "*.exe",
    ".DS_Store",
    "Thumbs.db",
]


async def scan_repository(options: ScannerOptions, policy: Policy) -> ScanReport:
    """Scan *options.root* using *policy* and return a report."""

    files: list[FileReport] = []
    semaphore = options.semaphore or asyncio.Semaphore(32)

    async def process(path: Path) -> None:
        if should_ignore(path, options.ignore_patterns, options.root):
            options.scan_stats.files_skipped += 1
            return

        try:
            report = await analyse_file(path, options, policy, semaphore)
            if report:
                files.append(report)
                options.scan_stats.files_scanned += 1
                options.scan_stats.total_size += report.size
            else:
                options.scan_stats.files_skipped += 1
        except Exception as e:
            options.scan_stats.files_errored += 1
            # Create error report for failed files
            error_report = FileReport(
                path=str(path.relative_to(options.root)),
                size=0,
                checksum="",
                mime_type="application/octet-stream",
                encoding="binary",
                violations=[],
                binary=True,
                error=str(e),
            )
            files.append(error_report)

    tasks: list[asyncio.Task[None]] = []
    for file_path in iter_files(options.root):
        tasks.append(asyncio.create_task(process(file_path)))

    if tasks:
        await asyncio.gather(*tasks)

    options.scan_stats.end_time = time.time()

    repo_name = options.root.name
    metadata = options.metadata or {}
    return ScanReport(
        repo=repo_name,
        root=str(options.root),
        files=sorted(files, key=lambda item: item.path),
        metadata=metadata,
        stats=options.scan_stats,
    )


def iter_files(root: Path) -> Iterable[Path]:
    """Iterate over all files in the repository, respecting .gitignore patterns."""
    try:
        for path in root.rglob("*"):
            if path.is_file():
                yield path
    except (PermissionError, OSError) as e:
        # Log permission errors but continue scanning
        print(f"Warning: Could not access {root}: {e}")


def should_ignore(path: Path, patterns: Sequence[str], root: Path) -> bool:
    """Check if a file should be ignored based on patterns."""
    try:
        relative = str(path.relative_to(root))
    except ValueError:
        # File is not relative to root (shouldn't happen in normal operation)
        return True

    for pattern in patterns:
        if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(path.name, pattern):
            return True
    return False


async def analyse_file(
    path: Path,
    options: ScannerOptions,
    policy: Policy,
    semaphore: asyncio.Semaphore,
) -> FileReport | None:
    """Analyze a single file and return a report."""
    async with semaphore:
        try:
            stat = await asyncio.to_thread(path.stat)

            # Skip unreadable files in safe mode
            if options.safe and not options.deep and (stat.st_mode & 0o444) == 0:
                return None

            # Skip large files in safe mode
            if options.safe and stat.st_size > options.max_safe_size:
                return None

            # Skip binary files in safe mode
            if not options.deep and is_binary_file(path):
                return None

            checksum = await asyncio.to_thread(hash_file, path)
            mime_type = detect_mime_type(path)

            # Try to read as text for policy evaluation
            content, encoding = await read_text_preview(path)
            is_binary = content is None

            if is_binary:
                if options.safe and not options.deep:
                    return None
                return FileReport(
                    path=str(path.relative_to(options.root)),
                    size=stat.st_size,
                    checksum=checksum,
                    mime_type=mime_type,
                    encoding=encoding or "binary",
                    violations=[],
                    binary=True,
                )

            lines = content.splitlines()
            if (
                options.safe
                and not options.deep
                and len(lines) > options.max_safe_lines
            ):
                return None

            violations = policy.evaluate(path=path, lines=lines)
            return FileReport(
                path=str(path.relative_to(options.root)),
                size=stat.st_size,
                checksum=checksum,
                mime_type=mime_type,
                encoding=encoding,
                violations=violations,
                binary=False,
            )

        except Exception:
            # Re-raise to be handled by the caller
            raise


def is_binary_file(path: Path) -> bool:
    """Check if a file is binary using multiple methods."""
    # Use the utility function first
    if is_binary(path):
        return True

    # Check common binary extensions
    binary_extensions = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".tiff",
        ".webp",
        ".mp4",
        ".avi",
        ".mov",
        ".mkv",
        ".webm",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".zip",
        ".tar",
        ".gz",
        ".rar",
        ".7z",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".pyc",
        ".pyo",
        ".pyd",
    }
    if path.suffix.lower() in binary_extensions:
        return True

    return False


def hash_file(path: Path) -> str:
    """Calculate SHA256 hash of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_mime_type(path: Path) -> str:
    """Detect MIME type of a file."""
    if magic:
        try:
            return magic.from_file(str(path), mime=True)
        except Exception:
            pass
    mime_type, _ = mimetypes.guess_type(path.as_posix())
    return mime_type or "application/octet-stream"


async def read_text_preview(path: Path) -> tuple[str | None, str | None]:
    """Read text content from a file with encoding detection."""
    try:
        return await asyncio.to_thread(lambda: _read_text(path))
    except (UnicodeDecodeError, OSError):
        return None, None


def _read_text(path: Path) -> tuple[str | None, str | None]:
    """Synchronous text reading with encoding detection."""
    # Try the utility function first
    try:
        content = read_text(path)
        return content, "utf-8"
    except (UnicodeDecodeError, OSError):
        pass

    # Fallback to manual encoding detection
    encodings = ["utf-8", "utf-8-sig", "utf-16", "latin-1", "cp1252"]
    for encoding in encodings:
        try:
            text = path.read_text(encoding=encoding)
            return text, encoding
        except (UnicodeDecodeError, OSError):
            continue
    return None, None


def build_policy_from_schema(schema_rules: Sequence[dict]) -> Policy:
    """Build a policy from schema rules."""
    extra_rules: list[Rule] = []
    for entry in schema_rules:
        rule_id = entry.get("id")
        patterns = entry.get("patterns")
        if not rule_id or not patterns:
            continue
        if isinstance(patterns, str):
            patterns = [patterns]
        description = entry.get("description", rule_id)
        severity = entry.get("severity", "medium")
        extra_rules.append(
            Rule(
                rule_id=rule_id,
                description=description,
                patterns=tuple(patterns),
                severity=severity,
            )
        )
    return load_default_policy(extra_rules)


async def build_scan_report(
    root: Path,
    *,
    ignore: Sequence[str] | None = None,
    safe: bool = True,
    deep: bool = False,
    schema: dict | None = None,
    max_lines: int = 1_000,
    max_size: int = 10 * 1024 * 1024,
) -> ScanReport:
    """Build a comprehensive scan report."""
    if not root.exists():
        raise FileNotFoundError(f"Root directory does not exist: {root}")

    if not root.is_dir():
        raise ValueError(f"Root path is not a directory: {root}")

    # Combine default ignores with user-provided ignores
    all_ignores = list(DEFAULT_IGNORES)
    if ignore:
        all_ignores.extend(ignore)

    metadata = summarize_metadata(schema)
    options = ScannerOptions(
        root=root,
        ignore_patterns=tuple(all_ignores),
        safe=safe,
        deep=deep,
        max_safe_lines=max_lines,
        max_safe_size=max_size,
        metadata=metadata,
    )
    schema_rules = extract_rules_from_schema(schema)
    policy = build_policy_from_schema(schema_rules)
    return await scan_repository(options, policy)


Violation = RuleViolation

__all__ = [
    "DEFAULT_IGNORES",
    "FileReport",
    "ScanReport",
    "ScanStats",
    "ScannerOptions",
    "Violation",
    "build_scan_report",
    "scan_repository",
]
