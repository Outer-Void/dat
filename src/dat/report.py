"""Report generation utilities."""

from __future__ import annotations

import datetime as dt
import getpass
import hashlib
import io
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import __version__
from .rules import RuleFinding
from .scanner import ScanResult
from .utils import atomic_write
from .utils import mask_secrets as _mask_secrets


def build_metadata(root: Path, *, lrc: dict | None = None) -> dict[str, Any]:
    """Construct metadata shared across report types."""

    now = dt.datetime.now(dt.timezone.utc)
    metadata: dict[str, Any] = {
        "dat_version": __version__,
        "generated_at": now.isoformat(),
        "root": str(root),
        "repo": Path(root).name,
        "user": getpass.getuser(),
    }
    if lrc:
        metadata["lrc"] = lrc
    return metadata


def serialise_scan(result: ScanResult) -> dict[str, Any]:
    """Serialise :class:`ScanResult` into JSON ready structure."""

    return {
        "root": str(result.root),
        "stats": {
            "scanned": result.stats.scanned,
            "skipped": result.stats.skipped,
            "binary": result.stats.binary,
            "errors": result.stats.errors,
        },
        "files": [
            {
                "path": record.path,
                "size": record.size,
                "lines": record.lines,
                "binary": record.binary,
            }
            for record in result.files
        ],
        "skipped": [
            {"path": entry.path, "reason": entry.reason} for entry in result.skipped
        ],
        "errors": result.errors,
    }


def serialise_findings(findings: Iterable[RuleFinding]) -> list[dict[str, Any]]:
    """Serialise policy findings."""
    return [
        {
            "rule_id": finding.rule_id,
            "message": finding.message,
            "severity": finding.severity,
            "path": finding.path,
        }
        for finding in findings
    ]


def calculate_report_fingerprint(
    metadata: dict[str, Any],
    scan: dict[str, Any],
    findings: list[dict[str, Any]],
) -> str:
    """Create a deterministic fingerprint for a report payload."""

    payload = {
        "metadata": metadata,
        "scan": scan,
        "findings": findings,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def write_json_report(
    path: Path, result: ScanResult, findings: Iterable[RuleFinding], metadata: dict
) -> Path:
    """Write a JSON report combining scan results and metadata."""

    findings_list = list(findings)
    serialised_scan = serialise_scan(result)
    serialised_findings = serialise_findings(findings_list)

    base_metadata = dict(metadata)
    fingerprint = base_metadata.get("fingerprint")
    if not fingerprint:
        trimmed_metadata = dict(base_metadata)
        trimmed_metadata.pop("fingerprint", None)
        fingerprint = calculate_report_fingerprint(
            trimmed_metadata,
            serialised_scan,
            serialised_findings,
        )
        metadata["fingerprint"] = fingerprint
        base_metadata["fingerprint"] = fingerprint

    report = {
        "metadata": base_metadata,
        "scan": serialised_scan,
        "findings": serialised_findings,
    }
    payload = (
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        + b"\n"
    )
    atomic_write(path, payload)
    return path


def _read_text(path: str) -> str | None:
    """Read text file with error handling."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return None


def _relpath(path: str, root: str) -> str:
    """Get relative path with error handling."""
    try:
        return os.path.relpath(path, root)
    except Exception:
        return path


def _guess_lang(p: str) -> str:
    """Detect language from file extension for syntax highlighting."""
    p = p.lower()
    if p.endswith(".py"):
        return "python"
    if p.endswith(".sh") or p.endswith(".bash") or p.endswith(".zsh"):
        return "bash"
    if p.endswith(".js"):
        return "javascript"
    if p.endswith(".ts"):
        return "typescript"
    if p.endswith(".json"):
        return "json"
    if p.endswith(".yml") or p.endswith(".yaml"):
        return "yaml"
    if p.endswith(".toml"):
        return "toml"
    if p.endswith(".md"):
        return "markdown"
    if p.endswith(".html") or p.endswith(".htm"):
        return "html"
    if p.endswith(".css"):
        return "css"
    if p.endswith(".xml"):
        return "xml"
    if p.endswith(".sql"):
        return "sql"
    if p.endswith(".java"):
        return "java"
    if p.endswith(".c") or p.endswith(".h"):
        return "c"
    if p.endswith(".cpp") or p.endswith(".cc") or p.endswith(".hpp"):
        return "cpp"
    if p.endswith(".go"):
        return "go"
    if p.endswith(".rs"):
        return "rust"
    return ""


def write_markdown_with_code(
    path: Path,
    result: ScanResult,
    findings: Iterable[RuleFinding],
    metadata: dict,
    *,
    include_snippets: bool = True,
    context_lines: str = "full",
    mask_secrets: bool = True,
    relative_paths: bool = True,
) -> Path:
    """
    Enhanced Markdown report with optional file contents.
    - If context_lines == "full": prints the entire file (masked if mask_secrets)
    - Skips binary files automatically
    - Honors relative_paths setting
    """
    findings_list = list(findings)
    buf = io.StringIO()
    ts = metadata.get("generated_at", dt.datetime.now(dt.timezone.utc).isoformat())
    version = metadata.get("dat_version", __version__)
    root = str(result.root)

    buf.write("# DAT Audit Report\n\n")
    buf.write(f"- **Version**: {version}\n")
    buf.write(f"- **Generated**: {ts}\n")
    if lrc := metadata.get("lrc"):
        buf.write(f"- **LRC Project**: {lrc.get('project', 'unknown')}\n")
    buf.write("\n")

    # Summary
    buf.write("## Summary\n\n")
    buf.write(
        f"Scanned {result.stats.scanned} files with {result.stats.binary} binary files and {len(result.errors)} errors.\n\n"
    )

    # Skipped files
    if result.skipped:
        buf.write("### Skipped Files\n\n")
        for item in result.skipped[:20]:
            spath = _relpath(item.path, root) if relative_paths else item.path
            buf.write(f"- {spath} ({item.reason})\n")
        if len(result.skipped) > 20:
            buf.write(f"- ... {len(result.skipped) - 20} more\n")
        buf.write("\n")

    # Findings
    if findings_list:
        buf.write("## Findings\n\n")
        for finding in findings_list:
            location = (
                _relpath(finding.path, root)
                if finding.path and relative_paths
                else finding.path
            )
            loc_str = f" ({location})" if location else ""
            buf.write(
                f"- **{finding.severity.upper()}** [{finding.rule_id}]{loc_str}: {finding.message}\n"
            )
        buf.write("\n")

    # Code sections - Enhanced with full file content
    if include_snippets and context_lines == "full":
        buf.write("## Code\n\n")
        for record in result.files:
            if record.binary:
                continue

            shown_path = _relpath(record.path, root) if relative_paths else record.path
            text = _read_text(str(Path(root) / record.path))
            if text is None:
                continue

            if mask_secrets:
                text = _mask_secrets(text)

            lang = _guess_lang(record.path)
            buf.write(f"### `{shown_path}`\n\n")
            buf.write(f"```{lang}\n{text}\n```\n\n")

    payload = buf.getvalue().encode("utf-8") + b"\n"
    atomic_write(path, payload)
    return path


def write_markdown_report(
    path: Path, result: ScanResult, findings: Iterable[RuleFinding], metadata: dict
) -> Path:
    """
    Persist a Markdown summary of the scan.
    Enhanced version with optional code inclusion.
    """
    # Check if we should include full file contents
    include_snippets = True  # Default behavior
    context_lines = "full"  # Default to full file content
    mask_secrets = True  # Default to masking secrets
    relative_paths = True  # Default to relative paths

    # Extract output configuration from metadata if available
    output_config = metadata.get("output", {})
    include_snippets = output_config.get("include_snippets", include_snippets)
    context_lines = output_config.get("context_lines", context_lines)
    mask_secrets = output_config.get("mask_secrets", mask_secrets)
    relative_paths = output_config.get("relative_paths", relative_paths)

    # Use enhanced version if we're including snippets with full context
    if include_snippets and context_lines == "full":
        return write_markdown_with_code(
            path,
            result,
            findings,
            metadata,
            include_snippets=include_snippets,
            context_lines=context_lines,
            mask_secrets=mask_secrets,
            relative_paths=relative_paths,
        )

    # Fall back to original simple Markdown format
    findings_list = list(findings)
    lines: list[str] = []
    lines.append("# DAT Audit Report")
    lines.append("")
    lines.append(f"- **Version**: {metadata.get('dat_version', __version__)}")
    lines.append(f"- **Generated**: {metadata.get('generated_at', '')}")
    if lrc := metadata.get("lrc"):
        lines.append(f"- **LRC Project**: {lrc.get('project', 'unknown')}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(
        f"Scanned {result.stats.scanned} files with {result.stats.binary} binary files and {len(result.errors)} errors."
    )
    if result.skipped:
        lines.append("")
        lines.append("### Skipped Files")
        for item in result.skipped[:20]:
            lines.append(f"- {item.path} ({item.reason})")
        if len(result.skipped) > 20:
            lines.append(f"- ... {len(result.skipped) - 20} more")
    if findings_list:
        lines.append("")
        lines.append("## Findings")
        for finding in findings_list:
            location = f" ({finding.path})" if finding.path else ""
            lines.append(
                f"- **{finding.severity.upper()}** [{finding.rule_id}]{location}: {finding.message}"
            )
    payload = "\n".join(lines).encode("utf-8") + b"\n"
    atomic_write(path, payload)
    return path
