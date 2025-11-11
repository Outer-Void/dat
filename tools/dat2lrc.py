#!/usr/bin/env python3
"""
DAT to LRC (Lightweight Reproducible Container) exporter.

Converts a directory structure into an LRC schema file that can reconstruct
the original structure with file permissions and content.

Usage:
    python dat2lrc.py /path/to/source /path/to/output.lrc
"""

from __future__ import annotations

import argparse
import fnmatch
import mimetypes
import os
import platform
import stat
import sys
from pathlib import Path


# Cross-platform defaults
DEFAULT_IGNORES = {
    ".git",
    "__pycache__",
    ".DS_Store",
    ".venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".coverage",
    "*.pyc",
    "*.pyo",
    "Thumbs.db",
    "ehthumbs.db",
    "Desktop.ini",
    ".Spotlight-V100",
}

# Configuration
INLINE_MAX_BYTES = 64 * 1024  # 64KB
INLINE_MAX_LINES = 500
BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".ico",
    ".svg",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".rar",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".a",
    ".lib",
    ".class",
    ".jar",
    ".war",
    ".ear",
    ".mp3",
    ".mp4",
    ".avi",
    ".mkv",
    ".mov",
    ".wmv",
    ".db",
    ".sqlite",
    ".mdb",
    ".accdb",
}

IS_WINDOWS = platform.system() == "Windows"


def is_text_file(p: Path, sniff: int = 8192) -> bool:
    """
    Enhanced text file detection using multiple heuristics.
    Returns True if file is likely text, False if binary.
    """
    if p.suffix.lower() in BINARY_EXTENSIONS:
        return False

    try:
        # Get file size
        stat_info = p.stat()
        if stat_info.st_size == 0:
            return True  # Empty files are considered text

        # Read sample for analysis
        with p.open("rb") as f:
            sample = f.read(sniff)

        if not sample:
            return True

        # Null byte check (definitive binary indicator)
        if b"\x00" in sample:
            return False

        # MIME type detection
        mt, _ = mimetypes.guess_type(str(p))
        if mt:
            if mt.startswith("text/"):
                return True
            # Special case: application/x-shellscript, application/x-python, etc.
            if mt.startswith("application/") and "script" in mt:
                return True

        # Character distribution analysis
        # Count control characters (excluding common whitespace: \t, \n, \r)
        control_chars = 0
        printable_chars = 0

        for byte in sample:
            if byte < 32:  # Control characters
                if byte not in (9, 10, 13):  # Not tab, LF, or CR
                    control_chars += 1
            elif 32 <= byte <= 126 or byte > 126:  # Printable ASCII
                printable_chars += 1

        total_chars = len(sample)
        if total_chars == 0:
            return True

        # If >5% control characters or <70% printable, likely binary
        control_ratio = control_chars / total_chars
        printable_ratio = printable_chars / total_chars

        return control_ratio < 0.05 and printable_ratio > 0.7

    except OSError:
        return False


def read_text(p: Path, encodings: list[str] | None = None) -> str:
    """
    Robust text reading with multiple encoding fallbacks.
    """
    if encodings is None:
        encodings = ["utf-8", "latin-1", "cp1252", "iso-8859-1", "utf-16", "ascii"]

    for encoding in encodings:
        try:
            return p.read_text(encoding=encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue

    # Final fallback with replacement
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except UnicodeDecodeError:
        # Ultimate fallback: latin-1 never fails
        return p.read_text(encoding="latin-1")


def should_ignore(path: Path, ignore_patterns: set[str], root: Path) -> bool:
    """
    Enhanced pattern matching for ignore rules.
    Supports:
    - Exact names: ".git"
    - Wildcards: "*.pyc"
    - Directory markers: ".git/"
    - Path segments: "__pycache__"
    """
    if not ignore_patterns:
        return False

    path_str = str(path)
    name = path.name
    parts = set(path.parts)
    rel_path = path.relative_to(root) if path.is_relative_to(root) else path

    for pattern in ignore_patterns:
        pattern = pattern.strip()
        if not pattern:
            continue

        # Exact name match
        if name == pattern:
            return True

        # Directory marker (ends with /)
        if pattern.endswith("/") and path.is_dir() and name == pattern[:-1]:
            return True

        # Wildcard matching in current directory
        if "*" in pattern or "?" in pattern:
            # Try matching against relative path
            if fnmatch.fnmatch(str(rel_path), pattern):
                return True
            # Try matching against name only
            if fnmatch.fnmatch(name, pattern):
                return True

        # Path segment matching
        if pattern in parts:
            return True

        # Substring in full path (conservative)
        if pattern in path_str:
            # Ensure it's a full path segment, not partial match
            if f"/{pattern}/" in f"/{path_str}/" or path_str.endswith(f"/{pattern}"):
                return True

    return False


def get_relative_path(file_path: Path, base_dir: Path) -> str:
    """
    Get relative path, handling cases where file_path is not relative to base_dir.
    """
    try:
        return str(file_path.relative_to(base_dir))
    except ValueError:
        # Fall back to absolute path with / normalization
        return str(file_path).replace("\\", "/")


def choose_heredoc_marker(text: str, used_markers: set[str]) -> str:
    """
    Choose a unique heredoc marker that doesn't conflict with content.
    """
    base_markers = ["EOF", "END", "EOT", "DOC", "MARKER"]

    for base in base_markers:
        marker = base
        counter = 1

        # Ensure marker is unique and not in content
        while marker in used_markers or marker in text:
            marker = f"{base}_{counter}"
            counter += 1

        used_markers.add(marker)
        return marker

    # Fallback
    marker = "HEREDOC"
    counter = 1
    while marker in used_markers:
        marker = f"HEREDOC_{counter}"
        counter += 1

    used_markers.add(marker)
    return marker


def detect_executable_files(root: Path) -> set[Path]:
    """
    Detect executable files that should get @chmod directives.
    """
    executables = set()

    try:
        for file_path in root.rglob("*"):
            if file_path.is_file():
                try:
                    # Unix: check execute permissions
                    if not IS_WINDOWS:
                        mode = file_path.stat().st_mode
                        if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
                            executables.add(file_path)

                    # Windows & Unix: check file extensions and shebangs
                    if file_path.suffix.lower() in (
                        ".sh",
                        ".py",
                        ".pl",
                        ".rb",
                        ".bash",
                        ".zsh",
                    ):
                        executables.add(file_path)

                    # Check for shebang
                    if file_path.suffix.lower() in (
                        ".py",
                        ".pl",
                        ".rb",
                        ".sh",
                        ".bash",
                    ):
                        try:
                            with file_path.open("rb") as f:
                                first_line = f.readline().strip()
                                if first_line.startswith(b"#!"):
                                    executables.add(file_path)
                        except OSError:
                            pass

                except OSError:
                    continue

    except OSError:
        pass  # Can't traverse some directories

    return executables


def export_folder(
    root: Path,
    out_path: Path,
    ignores: set[str],
    dry_run: bool,
    force: bool,
    verbose: bool = False,
) -> None:
    """
    Main export function that generates LRC schema from folder structure.
    """
    if not root.exists():
        print(f"[ERROR] Root directory not found: {root}", file=sys.stderr)
        sys.exit(2)

    if not root.is_dir():
        print(f"[ERROR] Root path is not a directory: {root}", file=sys.stderr)
        sys.exit(2)

    # Ensure output directory exists
    out_path.parent.mkdir(parents=True, exist_ok=True)

    schema_dir = out_path.parent.resolve()
    lines: list[str] = []

    # Schema header with metadata
    lines.extend(
        [
            "# =========================================================",
            "# Generated by dat2lrc v1.0.0",
            f"# Source: {root}",
            f"# Timestamp: {platform.node()} @ {platform.system()}",
            "#",
            "# LRC Schema - Lightweight Reproducible Container",
            "# Reconstruct with: lrc build <schema_file>",
            "# =========================================================",
            "",
        ]
    )

    # Add ignore patterns
    if ignores:
        lines.extend(
            [
                "# Ignore patterns used during export:",
                f"@ignore {' '.join(sorted(ignores))}",
                "",
            ]
        )

    # Build directory structure map
    dir_map: dict[Path, list[Path]] = {}
    executable_files = detect_executable_files(root)

    if verbose:
        print(f"[INFO] Scanning {root}...")

    try:
        for dirpath, dirnames, filenames in os.walk(root):
            current_dir = Path(dirpath)

            # Filter directories to traverse
            dirnames[:] = [
                dn
                for dn in dirnames
                if not should_ignore(current_dir / dn, ignores, root)
            ]

            # Skip ignored directories
            if should_ignore(current_dir, ignores, root):
                continue

            # Collect non-ignored files
            files = []
            for filename in filenames:
                file_path = current_dir / filename
                if not should_ignore(file_path, ignores, root):
                    files.append(file_path)

            if files or current_dir != root:  # Include directories even if empty
                dir_map[current_dir] = sorted(files)

    except OSError as e:
        print(f"[WARNING] Could not traverse some directories: {e}", file=sys.stderr)

    # Order sections by depth and name
    sections = sorted(
        dir_map.keys(),
        key=lambda p: (
            len(p.relative_to(root).parts) if p.is_relative_to(root) else 0,
            str(p).lower(),
        ),
    )

    used_markers: set[str] = set()
    stats = {
        "files_inline": 0,
        "files_copy": 0,
        "executables": 0,
        "directories": 0,
    }

    # Generate schema sections
    for section in sections:
        rel_section = get_relative_path(section, root)

        # Directory section
        if section != root:
            lines.append(f"@mkdir {rel_section}")
            stats["directories"] += 1

        # File entries in this directory
        for file_path in dir_map[section]:
            rel_file = get_relative_path(file_path, section)

            try:
                file_size = file_path.stat().st_size
                is_executable = file_path in executable_files

                # Try inline for text files under size/line limits
                if (
                    is_text_file(file_path)
                    and file_size <= INLINE_MAX_BYTES
                    and file_size > 0
                ):  # Skip empty files for inline
                    try:
                        content = read_text(file_path)
                        line_count = content.count("\n") + (
                            1 if content and not content.endswith("\n") else 0
                        )

                        if line_count <= INLINE_MAX_LINES:
                            marker = choose_heredoc_marker(content, used_markers)

                            lines.append(f"@write {rel_file} <<{marker}")
                            lines.extend(content.splitlines())
                            lines.append(marker)

                            # Add chmod for executable files
                            if is_executable and not IS_WINDOWS:
                                lines.append(f"  @chmod {rel_file} +x")
                                stats["executables"] += 1

                            stats["files_inline"] += 1
                            continue

                    except OSError as e:
                        if verbose:
                            print(
                                f"[WARNING] Could not read {file_path} for inline: {e}"
                            )

                # Fallback to @copy for binary or large files
                if schema_dir in file_path.parents:
                    # Use relative path if file is under schema directory
                    src_path = get_relative_path(file_path, schema_dir)
                else:
                    # Use absolute path if not relative to schema dir
                    src_path = str(file_path)

                dst_path = get_relative_path(file_path, root)
                lines.append(f"  @copy {src_path} {dst_path}")

                # Add chmod for executable files
                if is_executable and not IS_WINDOWS:
                    lines.append(f"  @chmod {dst_path} +x")
                    stats["executables"] += 1

                stats["files_copy"] += 1

            except (ValueError, OSError) as e:
                if verbose:
                    print(f"[WARNING] Could not process {file_path}: {e}")

        lines.append("")  # Section separator

    # Write output
    if not dry_run:
        try:
            out_path.write_text("\n".join(lines), encoding="utf-8")
            if verbose:
                print(f"[SUCCESS] Schema written to {out_path}")
                print(f"  Directories: {stats['directories']}")
                print(f"  Files inline: {stats['files_inline']}")
                print(f"  Files copy: {stats['files_copy']}")
                print(f"  Executables: {stats['executables']}")
        except OSError as e:
            print(f"[ERROR] Could not write output: {e}", file=sys.stderr)
            sys.exit(3)
    else:
        print("\n".join(lines))


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Export directory structure to LRC schema format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/project project.lrc
  %(prog)s /path/to/src output.lrc --ignore "*.pyc" "__pycache__"
  %(prog)s /path/to/data schema.lrc --dry-run --verbose
        """,
    )

    parser.add_argument("source", type=Path, help="Source directory to export")

    parser.add_argument("output", type=Path, help="Output LRC schema file path")

    parser.add_argument(
        "--ignore",
        "-i",
        action="append",
        default=[],
        help="Ignore patterns (can be used multiple times)",
    )

    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Show what would be generated without writing",
    )

    parser.add_argument(
        "--force", "-f", action="store_true", help="Overwrite output file if it exists"
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed progress information",
    )

    parser.add_argument("--version", action="version", version="dat2lrc 1.0.0")

    args = parser.parse_args()

    # Combine default ignores with user-provided ones
    all_ignores = set(DEFAULT_IGNORES)
    all_ignores.update(args.ignore)

    # Check if output exists
    if args.output.exists() and not args.dry_run and not args.force:
        print(f"[ERROR] Output file exists: {args.output}", file=sys.stderr)
        print("Use --force to overwrite", file=sys.stderr)
        sys.exit(1)

    export_folder(
        root=args.source,
        out_path=args.output,
        ignores=all_ignores,
        dry_run=args.dry_run,
        force=args.force,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
