#!/usr/bin/env python3
"""
DAT (Dev Audit Tool) - Unified CLI Interface
Combines robust simplified CLI with Typer-based commands.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .integration import (
    load_integration_config,
    load_lrc_build,
    load_lrc_config,
    merge_lrc_metadata,
    select_schema,
    summarize_metadata,
    write_lrc_audit,
)
from .integration.signing import sign_artifact
from .logging.audit import append_encrypted_log
from .report import (
    build_metadata,
    calculate_report_fingerprint,
    serialise_findings,
    serialise_scan,
    write_json_report,
    write_markdown_report,
)
from .rules import RuleFinding, evaluate_rules
from .scanner import ScanResult, scan_repository
from .utils import atomic_write, safe_mkdir


console = Console()
app = typer.Typer(
    no_args_is_help=False, help="DAT - Dev Audit Tool | Enterprise Security Scanning"
)


def _write_pdf_report(
    path: Path, result: ScanResult, findings: Iterable[RuleFinding], metadata: dict
) -> None:
    try:
        from .pdf import write_pdf_report
    except ModuleNotFoundError:
        console.print(
            "[red]PDF output requires ReportLab. Install with "
            '`pip install "outervoid-dat[pdf]"`.[/red]'
        )
        raise typer.Exit(1) from None
    write_pdf_report(path, result, findings, metadata)


# =============================================================================
# dat cmd — print main code files (incl. scripts) into a single Markdown file
# =============================================================================
_CMD_DEFAULT_EXTS: tuple[str, ...] = (
    ".py",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".bat",
    ".cmd",
    ".js",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".rb",
    ".php",
    ".pl",
    ".lua",
    ".scala",
    ".kt",
)
_CMD_DEFAULT_BASENAMES: tuple[str, ...] = ("Makefile", "GNUmakefile", "Dockerfile")
_CMD_DEFAULT_IGNORES: tuple[str, ...] = (
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "artifacts",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".idea",
    ".vscode",
    "coverage",
    "*.egg-info",
)
_CMD_LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".ps1": "powershell",
    ".bat": "bat",
    ".cmd": "bat",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".cpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".pl": "perl",
    ".lua": "lua",
    ".scala": "scala",
    ".kt": "kotlin",
}


def _cmd_should_ignore(p: Path, ignore_globs: tuple[str, ...]) -> bool:
    name = p.name
    # simple glob-ish checks without bringing extra deps
    for pat in ignore_globs:
        if pat.startswith("*.") and name.endswith(pat[1:]):
            return True
        if name == pat or pat in p.parts:
            return True
    return False


def _cmd_is_main_code(
    p: Path, allow_exts: tuple[str, ...], allow_names: tuple[str, ...]
) -> bool:
    if p.is_dir():
        return False
    if p.name in allow_names:
        return True
    return p.suffix.lower() in allow_exts


def _cmd_lang_for(p: Path) -> str:
    if p.name in ("Makefile", "GNUmakefile"):
        return "make"
    if p.name == "Dockerfile":
        return "dockerfile"
    return _CMD_LANG_BY_EXT.get(p.suffix.lower(), "")


def _cmd_mask_secrets(text: str) -> str:
    import re

    rules = [
        re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)(['\"]?)[A-Za-z0-9_\-]{12,}(\2)"),
        re.compile(r"(?i)(secret[_-]?key\s*[:=]\s*)(['\"]?)[A-Za-z0-9_\-]{12,}(\2)"),
        re.compile(r"(?i)(token\s*[:=]\s*)(['\"]?)[A-Za-z0-9\.\-_]{12,}(\2)"),
        re.compile(r"(?i)(password\s*[:=]\s*)(['\"]).*?(\2)"),
    ]
    out = text
    for rx in rules:
        out = rx.sub(r"\1\2***REDACTED***\3", out)
    return out


def _cmd_render_md(files: list[Path], title: str) -> str:
    from datetime import datetime

    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- **Generated**: {datetime.now().isoformat()}")
    lines.append(f"- **Files**: {len(files)}")
    lines.append("")
    for fp in files:
        try:
            txt = fp.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            console.print(f"[yellow]skip {fp}: {e}[/yellow]")
            continue
        lang = _cmd_lang_for(fp)
        rel = fp.as_posix()
        lines.append(f"## `{rel}`")
        lines.append("")
        lines.append(f"```{lang}".rstrip())
        lines.append(_cmd_mask_secrets(txt))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


@app.command("cmd")
def cmd(
    path: Path = typer.Argument(Path(), exists=True, file_okay=True, dir_okay=True),
    out: Path = typer.Option(
        Path("artifacts/CODEBASE.md"), "--out", "-o", help="Output Markdown file"
    ),
    exts: str = typer.Option(
        ",".join(_CMD_DEFAULT_EXTS),
        "--exts",
        help="Comma-separated allowlist of file extensions",
    ),
    names: str = typer.Option(
        ",".join(_CMD_DEFAULT_BASENAMES),
        "--names",
        help="Comma-separated allowlist of basenames",
    ),
    ignore: str = typer.Option(
        ",".join(_CMD_DEFAULT_IGNORES),
        "--ignore",
        help="Comma-separated dirs/globs to skip",
    ),
):
    """
    Print **main code files (including scripts)** from root and subdirectories to a single **Markdown** file.
    Skips vendor/build/caches by default. Secrets are masked.
    """
    allow_exts = tuple(s.strip().lower() for s in exts.split(",") if s.strip())
    allow_names = tuple(s.strip() for s in names.split(",") if s.strip())
    ignore_globs = tuple(s.strip() for s in ignore.split(",") if s.strip())

    base = path.resolve()
    files: list[Path] = []
    if base.is_file():
        if _cmd_is_main_code(base, allow_exts, allow_names) and not _cmd_should_ignore(
            base.parent, ignore_globs
        ):
            files.append(base)
    else:
        for root, dirs, filenames in os.walk(base, topdown=True):
            # prune ignored dirs in-place
            for d in list(dirs):
                if _cmd_should_ignore(Path(root, d), ignore_globs):
                    dirs.remove(d)
            for fn in filenames:
                p = Path(root, fn)
                if _cmd_should_ignore(p, ignore_globs):
                    continue
                if _cmd_is_main_code(p, allow_exts, allow_names):
                    files.append(p)
    files.sort()
    safe_mkdir(out.parent)
    md = _cmd_render_md(files, title="DAT CMD — Main Code & Scripts")
    out.write_text(md, encoding="utf-8")
    console.print(f"[green]✓ Wrote[/green] {out}")
    console.print(f"[dim]Files included: {len(files)}[/dim]")


# Shared functionality between CLI modes
def build_parser() -> argparse.ArgumentParser:
    """
    Build simplified argument parser for DAT.
    Focus on intuitive commands and sensible defaults.
    """
    parser = argparse.ArgumentParser(
        description="DAT - Dev Audit Tool | Enterprise Security Scanning",
        epilog="""
📖 Examples:
  dat                          # Quick scan of current directory
  dat /path/to/project         # Scan specific project
  dat --deep                   # Deep scan (includes binaries)
  dat --pdf report.pdf         # Generate PDF report
  dat --ignore node_modules    # Exclude directories
  dat --sign                   # Sign reports with GPG
  dat --diff baseline.json     # Compare with previous scan

🎯 File Selection:
  dat -f src                   # Scan only src folder
  dat -s main.py               # Scan only main.py file
  dat -a                       # Scan all files including hidden
  dat -f src -s main.py        # Combine folder and file filters

🔧 Advanced:
  dat --lrc                    # Enable compliance scanning
  dat --verbose                # Detailed output
  dat --json output.json       # JSON output for CI/CD
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Target selection
    target_group = parser.add_argument_group("📁 Scan Target")
    target_group.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Directory to scan (default: current directory)",
    )

    # File selection options
    selection_group = parser.add_argument_group("🎯 File Selection")
    selection_group.add_argument(
        "-f", "--folder", help="Scan only the specified folder (relative to target)"
    )
    selection_group.add_argument(
        "-s", "--single-file", help="Scan only the specified file (relative to target)"
    )
    selection_group.add_argument(
        "-a", "--all", action="store_true", help="Scan all files including hidden files"
    )

    # Scan mode
    mode_group = parser.add_argument_group("🔍 Scan Mode")
    mode_group.add_argument(
        "-d",
        "--deep",
        action="store_true",
        help="Deep scan (include binary files, no size limits)",
    )
    mode_group.add_argument(
        "--fast",
        action="store_true",
        help="Fast scan (skip large files, basic analysis)",
    )
    mode_group.add_argument(
        "--audit",
        action="store_true",
        help="Compliance audit mode (strict rules, detailed reporting)",
    )
    mode_group.add_argument(
        "--safe",
        action="store_true",
        help="Force safe mode (skip binaries and large files)",
    )

    # Output options
    output_group = parser.add_argument_group("📊 Output Options")
    output_group.add_argument(
        "-o", "--output", help="Output file (auto-detects format from extension)"
    )
    output_group.add_argument(
        "--report", help="Save comprehensive JSON report (alias for --json)"
    )
    output_group.add_argument("--json", help="Save as JSON report")
    output_group.add_argument("--jsonl", help="Save as JSON Lines report")
    output_group.add_argument("--pdf", help="Save as PDF report")
    output_group.add_argument("--md", "--markdown", help="Save as Markdown report")

    # Filtering
    filter_group = parser.add_argument_group("🎯 Filtering")
    filter_group.add_argument(
        "-i",
        "--ignore",
        action="append",
        default=[],
        help="Ignore pattern (e.g., node_modules, *.log)",
    )
    filter_group.add_argument(
        "--only",
        action="append",
        default=[],
        help="Only scan specific patterns (e.g., *.py, src/**)",
    )

    # Enterprise features
    enterprise_group = parser.add_argument_group("🏢 Enterprise Features")
    enterprise_group.add_argument(
        "--lrc", action="store_true", help="Enable LRC compliance integration"
    )
    enterprise_group.add_argument(
        "--from-lrc",
        nargs="?",
        const="",
        help="Load LRC configuration (optional path) and write audit summary",
    )
    enterprise_group.add_argument(
        "--sign", action="store_true", help="Sign reports with GPG"
    )
    enterprise_group.add_argument(
        "--no-sign", action="store_true", help="Disable artifact signing"
    )
    enterprise_group.add_argument("--diff", help="Compare with previous scan report")

    # Information & debugging
    info_group = parser.add_argument_group("ℹ️  Information")
    info_group.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output with detailed information",
    )
    info_group.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for confirmation before scanning",
    )
    info_group.add_argument(
        "--version", action="store_true", help="Show version information and exit"
    )
    info_group.add_argument(
        "--stats", action="store_true", help="Show detailed statistics after scan"
    )

    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments with version handling."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        raise SystemExit(0)
    return args


def display_banner() -> None:
    """Display DAT banner with version information."""
    banner = Text()
    banner.append("DAT ", style="bold cyan")
    banner.append("Dev Audit Tool", style="bold white")
    banner.append(f" v{__version__}", style="bold green")
    banner.append("\nEnterprise Security & Compliance Scanning", style="dim")

    console.print(Panel(banner, style="cyan", padding=(1, 2)))


def display_quick_help() -> None:
    """Display quick help reference."""
    help_table = Table(show_header=False, box=None, padding=(0, 2))
    help_table.add_column("Command", style="cyan")
    help_table.add_column("Description", style="white")

    help_table.add_row("dat [path]", "Quick scan of current directory")
    help_table.add_row("dat --deep", "Deep scan (includes binaries)")
    help_table.add_row("dat --pdf report.pdf", "Generate PDF report")
    help_table.add_row("dat --ignore node_modules", "Exclude directories")
    help_table.add_row("dat -f src", "Scan only src folder")
    help_table.add_row("dat -s main.py", "Scan only main.py file")
    help_table.add_row("dat -a", "Scan all files including hidden")
    help_table.add_row("dat --lrc --sign", "Compliance scan with signing")
    help_table.add_row("dat --diff baseline.json", "Compare with previous scan")

    console.print(Panel(help_table, title="🚀 Quick Start", style="green"))


def validate_args(args: argparse.Namespace) -> tuple[bool, str]:
    """
    Validate command line arguments.

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check path exists
    target_path = Path(args.path)
    if not target_path.exists():
        return False, f"Target path does not exist: {args.path}"

    if not target_path.is_dir():
        return False, f"Target path is not a directory: {args.path}"

    # Check folder selection exists
    if args.folder:
        folder_path = target_path / args.folder
        if not folder_path.exists():
            return False, f"Selected folder does not exist: {args.folder}"
        if not folder_path.is_dir():
            return False, f"Selected folder is not a directory: {args.folder}"

    # Check single file selection exists
    if args.single_file:
        file_path = target_path / args.single_file
        if not file_path.exists():
            return False, f"Selected file does not exist: {args.single_file}"
        if not file_path.is_file():
            return False, f"Selected file is not a file: {args.single_file}"

    # Check diff file exists if provided
    if args.diff and not Path(args.diff).exists():
        return False, f"Diff baseline file not found: {args.diff}"

    if getattr(args, "from_lrc", None):
        lrc_path = Path(args.from_lrc)
        if args.from_lrc and not lrc_path.exists():
            return False, f"LRC configuration not found: {args.from_lrc}"

    return True, ""


def determine_scan_mode(args: argparse.Namespace) -> dict:
    """
    Determine scan parameters based on mode flags.

    Returns:
        Dictionary of scan parameters
    """
    if getattr(args, "safe", False):
        return {
            "safe": True,
            "deep": False,
            "max_size": 10 * 1024 * 1024,
            "max_lines": 1000,
        }
    if args.deep:
        return {"safe": False, "deep": True, "max_size": None, "max_lines": None}
    if args.fast:
        return {
            "safe": True,
            "deep": False,
            "max_size": 5 * 1024 * 1024,  # 5MB
            "max_lines": 500,
        }
    if args.audit:
        return {"safe": False, "deep": True, "max_size": None, "max_lines": None}
    # Default balanced mode
    return {
        "safe": True,
        "deep": False,
        "max_size": 10 * 1024 * 1024,  # 10MB
        "max_lines": 1000,
    }


def build_custom_ignore_patterns(args: argparse.Namespace, target: Path) -> list[str]:
    """
    Build custom ignore patterns based on file selection arguments.

    Returns:
        List of ignore patterns
    """
    ignore_patterns = list(args.ignore or [])

    # If --all is not specified, ignore hidden files by default
    if not args.all:
        ignore_patterns.extend(
            [
                ".*",
                "*/.*",
                "**/.*",
            ]
        )

    return ignore_patterns


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


def display_scan_progress(target: Path, mode: str, args: argparse.Namespace) -> None:
    """Display scan progress information."""
    mode_descriptions = {
        "deep": "🔍 Deep Scan (all files, no limits)",
        "fast": "⚡ Fast Scan (skip large files)",
        "audit": "🏢 Compliance Audit (strict rules)",
        "default": "✅ Standard Scan (safe defaults)",
    }

    console.print(f"\n[bold]Target:[/bold] {target}")

    # Show file selection info
    selection_info = []
    if args.folder:
        selection_info.append(f"Folder: {args.folder}")
    if args.single_file:
        selection_info.append(f"File: {args.single_file}")
    if args.all:
        selection_info.append("All files (including hidden)")

    if selection_info:
        console.print(f"[bold]Selection:[/bold] {', '.join(selection_info)}")

    console.print(
        f"[bold]Mode:[/bold] {mode_descriptions.get(mode, mode_descriptions['default'])}"
    )
    console.print("Scanning...", end="")


def display_scan_summary(
    result: ScanResult, findings: list[RuleFinding], args: argparse.Namespace
) -> None:
    """Display comprehensive scan summary."""

    verbose = args.verbose or args.stats
    stats = result.stats
    total_files = stats.scanned
    total_violations = len(findings)
    critical_violations = sum(
        1 for finding in findings if finding.severity.lower() == "critical"
    )
    high_violations = sum(
        1 for finding in findings if finding.severity.lower() == "high"
    )

    summary_table = Table(
        title="📊 Scan Summary", show_header=True, header_style="bold magenta"
    )
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", style="white")
    summary_table.add_column("Status", style="green")

    summary_table.add_row(
        "Files Scanned", str(total_files), "✅" if total_files > 0 else "⚠️"
    )
    summary_table.add_row("Files Skipped", str(stats.skipped), "✅")
    summary_table.add_row(
        "Total Violations",
        str(total_violations),
        "✅" if total_violations == 0 else "❌",
    )
    summary_table.add_row(
        "Critical", str(critical_violations), "✅" if critical_violations == 0 else "🔴"
    )
    summary_table.add_row(
        "High", str(high_violations), "✅" if high_violations == 0 else "🟡"
    )
    console.print(summary_table)

    if args.single_file or (args.folder and total_files <= 20):
        files_table = Table(
            title="📁 Scanned Files", show_header=True, header_style="bold blue"
        )
        files_table.add_column("File", style="cyan")
        files_table.add_column("Size", style="white")
        files_table.add_column("Binary", style="white")

        for record in result.files:
            files_table.add_row(
                record.path,
                format_file_size(record.size),
                "Yes" if record.binary else "No",
            )

        console.print(files_table)

    if verbose and result.files:
        file_types: dict[str, int] = {}
        for record in result.files:
            ext = Path(record.path).suffix.lower() or "no extension"
            file_types[ext] = file_types.get(ext, 0) + 1

        if file_types:
            type_table = Table(
                title="📁 File Types", show_header=True, header_style="bold blue"
            )
            type_table.add_column("Extension", style="cyan")
            type_table.add_column("Count", style="white")

            for ext, count in sorted(
                file_types.items(), key=lambda item: item[1], reverse=True
            )[:10]:
                type_table.add_row(ext, str(count))

            console.print(type_table)

    if total_violations > 0:
        violations_table = Table(
            title="🚨 Top Violations", show_header=True, header_style="bold red"
        )
        violations_table.add_column("Rule", style="yellow")
        violations_table.add_column("Severity", style="red")
        violations_table.add_column("Message", style="white")
        violations_table.add_column("Location", style="cyan")

        for finding in findings[:10]:
            severity_emoji = {
                "critical": "🔴",
                "high": "🟡",
                "medium": "🟠",
                "low": "🔵",
                "info": "⚪",
            }.get(finding.severity.lower(), "⚪")

            violations_table.add_row(
                finding.rule_id,
                f"{severity_emoji} {finding.severity}",
                finding.message,
                finding.path or "(not specified)",
            )

        console.print(violations_table)


def write_report_file(
    result: ScanResult,
    findings: Iterable[RuleFinding],
    metadata: dict,
    file_path: str,
    format_type: str,
) -> Path:
    """Write report in specified format."""
    path = Path(file_path)
    safe_mkdir(path.parent)  # Use safe_mkdir instead of path.parent.mkdir()

    if format_type == "json":
        write_json_report(path, result, findings, metadata)
        console.print(f"[green]✓ JSON report saved:[/green] {path}")
    elif format_type == "jsonl":
        serialised_scan = serialise_scan(result)
        serialised_findings = serialise_findings(findings)
        metadata.setdefault("user", getpass.getuser())
        metadata.setdefault("repo", Path(result.root).name)

        trimmed_metadata = dict(metadata)
        trimmed_metadata.pop("fingerprint", None)
        fingerprint = metadata.get("fingerprint") or calculate_report_fingerprint(
            trimmed_metadata,
            serialised_scan,
            serialised_findings,
        )
        metadata["fingerprint"] = fingerprint

        summary_entry = {
            "type": "report",
            "repo": metadata.get("repo"),
            "report": path.name,
            "timestamp": metadata.get("generated_at"),
            "user": metadata.get("user"),
            "fingerprint": fingerprint,
        }
        metadata_entry = {"type": "metadata", **metadata}
        stats_entry = {"type": "stats", **serialised_scan["stats"]}

        lines = [
            json.dumps(summary_entry, ensure_ascii=False),
            json.dumps(metadata_entry, ensure_ascii=False),
            json.dumps(stats_entry, ensure_ascii=False),
        ]

        for file_entry in serialised_scan["files"]:
            lines.append(json.dumps({"type": "file", **file_entry}, ensure_ascii=False))
        for skipped_entry in serialised_scan.get("skipped", []):
            lines.append(
                json.dumps({"type": "skipped", **skipped_entry}, ensure_ascii=False)
            )
        for finding in serialised_findings:
            lines.append(json.dumps({"type": "finding", **finding}, ensure_ascii=False))

        atomic_write(path, ("\n".join(lines) + "\n").encode("utf-8"))
        console.print(f"[green]✓ JSONL report saved:[/green] {path}")
    elif format_type == "pdf":
        _write_pdf_report(path, result, findings, metadata)
        console.print(f"[green]✓ PDF report saved:[/green] {path}")
    elif format_type in {"md", "markdown"}:
        write_markdown_report(path, result, findings, metadata)
        console.print(f"[green]✓ Markdown report saved:[/green] {path}")
    else:
        write_json_report(path, result, findings, metadata)
        console.print(f"[green]✓ Report saved:[/green] {path}")
    return path


def run_scan(
    args: argparse.Namespace,
) -> tuple[ScanResult, list[RuleFinding], dict, dict]:
    """Execute the scan and return results, findings, metadata, and LRC context."""

    target = Path(args.path).resolve()
    scan_root = target
    if args.folder:
        scan_root = target / args.folder

    scan_params = determine_scan_mode(args)
    mode_name = (
        "deep"
        if args.deep
        else "fast"
        if args.fast
        else "audit"
        if args.audit
        else "default"
    )

    display_scan_progress(scan_root, mode_name, args)

    ignore_patterns = build_custom_ignore_patterns(args, scan_root)

    max_size = (
        scan_params["max_size"]
        if scan_params["max_size"] is not None
        else 10 * 1024 * 1024
    )
    max_lines = (
        scan_params["max_lines"] if scan_params["max_lines"] is not None else 1_000_000
    )

    try:
        result = scan_repository(
            scan_root,
            ignore_patterns=ignore_patterns,
            max_lines=max_lines,
            max_size=max_size,
            safe=scan_params["safe"],
            deep=scan_params["deep"],
        )
        console.print(" [green]✓[/green]")
    except Exception as exc:  # pragma: no cover - defensive logging
        console.print(" [red]✗[/red]")
        raise RuntimeError(f"scan failed: {exc}") from exc

    if args.single_file:
        relative_path = args.single_file
        filtered = [record for record in result.files if record.path == relative_path]
        result.files = filtered
        result.stats.scanned = len(filtered)

    findings = list(evaluate_rules(scan_root, result.files))

    build_context: dict = {}
    merged_lrc: dict = {}
    if args.lrc or args.from_lrc is not None:
        config_path = Path(args.from_lrc) if args.from_lrc else None
        config = (
            load_lrc_config(config_path) if config_path else load_integration_config()
        )
        schema = select_schema(config, target.name)
        lrc_config = summarize_metadata(schema) if schema else {}
        build_context = load_lrc_build(target)
        merged_lrc = merge_lrc_metadata(lrc_config, build_context)
        console.print("\n[blue]✓ LRC integration enabled[/blue]")

    metadata = build_metadata(target, lrc=merged_lrc or None)
    return result, findings, metadata, build_context


# Typer command implementations
def _defaults_out(fmt: str) -> Path:
    """Determine default output path based on format."""
    if fmt == "md":
        return Path("artifacts/report.md")
    if fmt == "findings-json":
        return Path("artifacts/findings.json")
    return Path("artifacts/report.json")


def _run_scan_to_file(
    path: Path,
    out: Path,
    fmt: str,
    include_snippets: bool,
    context_lines: str,
    mask_secrets: bool,
    relative_paths: bool,
    safe: bool = True,
    sign: bool = True,
):
    """Run scan and write to file (Typer version)."""

    # Convert Typer arguments to argparse-like namespace for compatibility
    class Args:
        def __init__(self):
            self.path = str(path)
            self.folder = None
            self.single_file = None
            self.all = False
            self.deep = False
            self.fast = False
            self.audit = False
            self.safe = safe  # Pass the safe parameter
            self.output = str(out)
            self.report = None
            self.json = None
            self.jsonl = None
            self.pdf = None
            self.md = None
            self.ignore = []
            self.only = []
            self.lrc = False
            self.from_lrc = None
            self.sign = sign  # Pass the sign parameter
            self.no_sign = not sign  # Set no_sign based on sign parameter
            self.diff = None
            self.verbose = False
            self.interactive = False
            self.version = False
            self.stats = False

    args = Args()

    # Set format-specific flags
    if fmt == "json":
        args.json = str(out)
    elif fmt == "jsonl":
        args.jsonl = str(out)
    elif fmt == "pdf":
        args.pdf = str(out)
    elif fmt in ["md", "markdown"]:
        args.md = str(out)
    else:
        args.output = str(out)

    try:
        result, findings, metadata, build_context = run_scan(args)
        display_scan_summary(result, findings, args)

        # Write the report
        format_type = "json" if fmt == "findings-json" else fmt
        output_path = write_report_file(
            result, findings, metadata, str(out), format_type
        )

        if sign:
            try:
                signature = sign_artifact(output_path)
                console.print(f"[green]✓ Signed:[/green] {signature}")
            except Exception as exc:
                console.print(f"[yellow]⚠ Signing failed: {exc}[/yellow]")

        console.print(f"[green]✓ Wrote {fmt.upper()} → {out}[/green]")

    except Exception as e:
        console.print(f"[red]Error during scan: {e}[/red]")
        raise typer.Exit(1)


@app.command("scan")
def scan_cmd(
    path: Path = typer.Argument(Path(), help="Directory to scan"),
    fmt: str = typer.Option(
        "md", "--format", "-f", help="Output format: md|json|findings-json|pdf|jsonl"
    ),
    out: Path = typer.Option(None, "--out", "-o", "--report", help="Output file path"),
    include_snippets: bool = typer.Option(
        True, help="Embed code snippets in Markdown reports"
    ),
    context_lines: str = typer.Option("full", help="Context lines: full|around|none"),
    mask_secrets: bool = typer.Option(True, help="Mask potential secrets in output"),
    relative_paths: bool = typer.Option(True, help="Use relative paths in reports"),
    deep: bool = typer.Option(
        False, "--deep", "-d", help="Deep scan (include binary files)"
    ),
    fast: bool = typer.Option(False, help="Fast scan (skip large files)"),
    audit: bool = typer.Option(False, help="Compliance audit mode"),
    safe: bool = typer.Option(
        True, "--safe/--no-safe", help="Enable safe scanning limits"
    ),
    ignore: list[str] = typer.Option([], "--ignore", "-i", help="Ignore patterns"),
    lrc: bool = typer.Option(False, help="Enable LRC compliance integration"),
    sign: bool = typer.Option(True, "--sign/--no-sign", help="Sign reports with GPG"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """
    Scan a directory for security issues and policy violations.
    """
    out = out or _defaults_out(fmt)

    # For Typer command, we need to handle the additional arguments
    # by creating a compatible args object
    class Args:
        def __init__(self):
            self.path = str(path)
            self.folder = None
            self.single_file = None
            self.all = False
            self.deep = deep
            self.fast = fast
            self.audit = audit
            self.safe = safe  # Pass the safe parameter
            self.output = str(out) if fmt in ["json", "pdf", "jsonl"] else None
            self.report = None
            self.json = str(out) if fmt == "json" else None
            self.jsonl = str(out) if fmt == "jsonl" else None
            self.pdf = str(out) if fmt == "pdf" else None
            self.md = str(out) if fmt in ["md", "markdown"] else None
            self.ignore = ignore
            self.only = []
            self.lrc = lrc
            self.from_lrc = None
            self.sign = sign  # Pass the sign parameter
            self.no_sign = not sign  # Set no_sign based on sign parameter
            self.diff = None
            self.verbose = verbose
            self.interactive = False
            self.version = False
            self.stats = verbose

    args = Args()

    try:
        result, findings, metadata, build_context = run_scan(args)
        display_scan_summary(result, findings, args)

        # Write the report
        format_type = "json" if fmt == "findings-json" else fmt
        output_path = write_report_file(
            result, findings, metadata, str(out), format_type
        )

        if sign:
            try:
                signature = sign_artifact(output_path)
                console.print(f"[green]✓ Signed:[/green] {signature}")
            except Exception as exc:
                console.print(f"[yellow]⚠ Signing failed: {exc}[/yellow]")

    except Exception as e:
        console.print(f"[red]Error during scan: {e}[/red]")
        raise typer.Exit(1)


@app.callback(invoke_without_command=True)
def _default_scan(
    ctx: typer.Context,
    path: Path = typer.Option(Path(), "--path", help="Project root to scan"),
    fmt: str = typer.Option("md", "--format", "-f", help="Output format"),
    out: Path = typer.Option(None, "--out", "-o", "--report", help="Output file path"),
    include_snippets: bool = typer.Option(True, help="Include code snippets"),
    context_lines: str = typer.Option("full", help="Context lines mode"),
    mask_secrets: bool = typer.Option(True, help="Mask secrets"),
    relative_paths: bool = typer.Option(True, help="Use relative paths"),
    safe: bool = typer.Option(
        True, "--safe/--no-safe", help="Enable safe scanning limits"
    ),
    sign: bool = typer.Option(True, "--sign/--no-sign", help="Sign reports with GPG"),
):
    """
    DAT - Dev Audit Tool | Enterprise Security Scanning

    Default command: scan the current directory and generate a Markdown report.
    """
    if ctx.invoked_subcommand is not None:
        return

    # Plain `dat` → behave like `dat scan`
    out = out or _defaults_out(fmt)
    _run_scan_to_file(
        path,
        out,
        fmt,
        include_snippets,
        context_lines,
        mask_secrets,
        relative_paths,
        safe,
        sign,
    )


# Legacy CLI entry point for backward compatibility
def main(argv: Sequence[str] | None = None) -> int:
    """Main CLI entry point (legacy)."""

    # If no arguments or just help, use Typer
    if not argv or (len(argv) == 1 and argv[0] in ["-h", "--help"]):
        try:
            app()
            return 0
        except SystemExit as e:
            return e.code

    # Otherwise, use legacy argparse-based CLI
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return exc.code

    try:
        target = Path(args.path).resolve()

        if not any(
            [args.report, args.output, args.json, args.jsonl, args.pdf, args.md]
        ):
            display_banner()
            if argv is None and len(sys.argv) <= 2:
                display_quick_help()

        if args.interactive:
            response = input("Proceed with DAT scan? [y/N]: ").strip().lower()
            if response not in {"y", "yes"}:
                console.print("[yellow]Scan cancelled by user[/yellow]")
                return 1

        is_valid, error_msg = validate_args(args)
        if not is_valid:
            console.print(f"[red]Error: {error_msg}[/red]")
            return 1

        result, findings, metadata, build_context = run_scan(args)
        display_scan_summary(result, findings, args)

        outputs: list[Path] = []

        def queue_output(path: str, format_type: str) -> None:
            outputs.append(
                write_report_file(result, findings, metadata, path, format_type)
            )

        if args.output:
            ext = Path(args.output).suffix.lower()
            if ext == ".json":
                queue_output(args.output, "json")
            elif ext == ".jsonl":
                queue_output(args.output, "jsonl")
            elif ext in {".md", ".markdown"}:
                queue_output(args.output, "markdown")
            elif ext == ".pdf":
                queue_output(args.output, "pdf")
            else:
                queue_output(args.output, "json")
        if args.report:
            report_ext = Path(args.report).suffix.lower()
            if report_ext == ".jsonl":
                queue_output(args.report, "jsonl")
            elif report_ext in {".md", ".markdown"}:
                queue_output(args.report, "markdown")
            elif report_ext == ".pdf":
                queue_output(args.report, "pdf")
            else:
                queue_output(args.report, "json")
        if args.json:
            queue_output(args.json, "json")
        if args.jsonl:
            queue_output(args.jsonl, "jsonl")
        if args.pdf:
            queue_output(args.pdf, "pdf")
        if args.md:
            queue_output(args.md, "markdown")

        if not outputs:
            default_output = Path(
                "artifacts/dat-report.json"
            )  # Changed from dat/ to artifacts/
            queue_output(str(default_output), "json")

        sign_outputs = not args.no_sign
        if args.sign:
            sign_outputs = True
        if sign_outputs:
            for output_path in outputs:
                try:
                    signature = sign_artifact(output_path)
                    console.print(f"[green]✓ Signed:[/green] {signature}")
                except Exception as exc:  # pragma: no cover - signing optional
                    console.print(f"[yellow]⚠ Signing failed: {exc}[/yellow]")

        if args.from_lrc is not None:
            try:
                write_lrc_audit(
                    target, result, findings, metadata, build_context=build_context
                )
                console.print("[green]✓ LRC audit written[/green]")
            except Exception as exc:  # pragma: no cover - defensive
                console.print(f"[yellow]⚠ Failed to write LRC audit: {exc}[/yellow]")

        if args.diff:
            try:
                previous_data = json.loads(Path(args.diff).read_text(encoding="utf-8"))
                previous_findings = previous_data.get("findings", [])
                previous_count = len(previous_findings)
                current_count = len(findings)
                previous_signature = {
                    (
                        entry.get("rule_id"),
                        entry.get("path"),
                        entry.get("message"),
                        entry.get("severity"),
                    )
                    for entry in previous_findings
                }
                current_signature = {
                    (
                        finding.rule_id,
                        finding.path,
                        finding.message,
                        finding.severity,
                    )
                    for finding in findings
                }
                findings_changed = previous_signature != current_signature

                previous_scan = previous_data.get("scan", {})
                current_scan = serialise_scan(result)
                scan_changed = (
                    previous_scan.get("files") != current_scan["files"]
                    or previous_scan.get("stats") != current_scan["stats"]
                    or previous_scan.get("skipped") != current_scan["skipped"]
                    or previous_scan.get("errors") != current_scan["errors"]
                )
                differences_detected = findings_changed or scan_changed
                if current_count > previous_count:
                    console.print(
                        f"[red]❌ Policy regressions: {previous_count} → {current_count} violations[/red]"
                    )
                elif current_count < previous_count:
                    console.print("[yellow]Differences detected between scans[/yellow]")
                    console.print(
                        f"[green]✓ Improvements: {previous_count} → {current_count} violations[/green]"
                    )
                elif differences_detected:
                    console.print("[yellow]Differences detected between scans[/yellow]")
                    console.print(
                        f"[green]✓ No change: {current_count} violations[/green]"
                    )
                else:
                    console.print(
                        f"[green]✓ No change: {current_count} violations[/green]"
                    )
            except Exception as exc:  # pragma: no cover - diff optional
                console.print(f"[yellow]⚠ Diff comparison failed: {exc}[/yellow]")

        try:
            selection_type = (
                "single_file"
                if args.single_file
                else "folder"
                if args.folder
                else "all"
                if args.all
                else "standard"
            )
            append_encrypted_log(
                {
                    "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "user": getpass.getuser(),
                    "repo": Path(result.root).name,
                    "files": result.stats.scanned,
                    "violations": len(findings),
                    "selection": selection_type,
                    "mode": "deep"
                    if args.deep
                    else "fast"
                    if args.fast
                    else "audit"
                    if args.audit
                    else "standard",
                }
            )
        except Exception as exc:  # pragma: no cover - logging best effort
            if args.verbose:
                console.print(f"[yellow]⚠ Audit logging failed: {exc}[/yellow]")

        if len(findings) > 0:
            console.print(
                f"\n[yellow]⚠ Scan completed with {len(findings)} violations[/yellow]"
            )
        else:
            console.print(
                "\n[green]✓ Scan completed successfully - no violations found[/green]"
            )
        return 0

    except KeyboardInterrupt:
        console.print("\n[yellow]⚠ Scan interrupted by user[/yellow]")
        return 130
    except Exception as exc:  # pragma: no cover - unexpected errors
        console.print(f"[red]💥 Unexpected error: {exc}[/red]")
        if os.getenv("DAT_DEBUG"):
            import traceback

            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
