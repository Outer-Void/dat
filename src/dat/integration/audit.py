"""Audit helpers for LRC integration."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..report import serialise_findings, serialise_scan, write_markdown_with_code
from ..rules import RuleFinding
from ..scanner import ScanResult
from ..utils import atomic_write, load_json, merge_dicts
from .lrc import LRC_CONFIG_PATH, resolve_lrc_config_path


def load_lrc_config(path: Path | None = None) -> dict[str, Any]:
    """Load LRC integration configuration."""

    config_path = resolve_lrc_config_path(path)
    return load_json(config_path) or {"schemas": []}


def load_lrc_build(repo_root: Path) -> dict[str, Any]:
    """Load `.lrc-build.json` from *repo_root* if present."""

    return load_json(repo_root / ".lrc-build.json") or {}


def merge_lrc_metadata(config: dict[str, Any], build: dict[str, Any]) -> dict[str, Any]:
    """Merge LRC config and build metadata."""

    return merge_dicts(config, build)


def write_lrc_audit(
    repo_root: Path,
    result: ScanResult,
    findings: Iterable[RuleFinding],
    metadata: dict,
    *,
    build_context: dict[str, Any] | None = None,
) -> Path:
    """Write `.lrc-audit.json` next to the build metadata."""

    output_path = repo_root / ".lrc-audit.json"
    findings_list = list(findings)
    payload = {
        "metadata": metadata,
        "scan": serialise_scan(result),
        "findings": serialise_findings(findings_list),
        "summary": {
            "files_scanned": result.stats.scanned,
            "files_skipped": result.stats.skipped,
            "violations": len(findings_list),
        },
        "build_context": build_context or {},
    }
    atomic_write(
        output_path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode(
            "utf-8"
        )
        + b"\n",
    )
    return output_path


def run_audit(scan: ScanResult, out: Path, fmt: str = "json", **opts) -> None:
    """
    Enhanced audit runner with multiple output formats.

    Args:
        scan: ScanResult object containing scan data
        out: Output file path
        fmt: Output format - 'json' | 'md' | 'findings-json'
        **opts: Additional options for Markdown output:
            include_snippets: bool - Include file snippets (default: True)
            context_lines: str - "full" | "none" | "around" (default: "full")
            mask_secrets: bool - Mask sensitive data (default: True)
            relative_paths: bool - Use relative paths (default: True)
            project_root: str - Root directory for relative paths
    """
    if fmt == "md":
        # Convert ScanResult to a format compatible with write_markdown_with_code
        # Create a simple metadata structure
        metadata = {
            "dat_version": getattr(scan, "version", "unknown"),
            "generated_at": getattr(scan, "timestamp", ""),
            "root": str(getattr(scan, "root", Path.cwd())),
        }

        # Extract findings if available
        findings = getattr(scan, "findings", [])

        write_markdown_with_code(
            out,
            scan,
            findings,
            metadata,
            include_snippets=bool(opts.get("include_snippets", True)),
            context_lines=str(opts.get("context_lines", "full")),
            mask_secrets=bool(opts.get("mask_secrets", True)),
            relative_paths=bool(opts.get("relative_paths", True)),
        )
        return

    if fmt == "findings-json":
        findings = getattr(scan, "findings", [])
        payload = json.dumps(serialise_findings(findings), ensure_ascii=False, indent=2)
        out.write_text(payload, encoding="utf-8")
        return

    # Default JSON format - full scan report
    payload = json.dumps(serialise_scan(scan), ensure_ascii=False, indent=2)
    out.write_text(payload, encoding="utf-8")


def select_schema(config: dict[str, Any], project_name: str) -> dict[str, Any] | None:
    """
    Select appropriate schema from LRC configuration based on project name.

    Args:
        config: LRC configuration dictionary
        project_name: Name of the project to match against schema patterns

    Returns:
        Selected schema dictionary or None if no match found
    """
    schemas = config.get("schemas", [])

    for schema in schemas:
        # Simple pattern matching - could be enhanced with regex
        patterns = schema.get("patterns", [])
        for pattern in patterns:
            if pattern in project_name or project_name in pattern:
                return schema

    return None


def summarize_metadata(schema: dict[str, Any]) -> dict[str, Any]:
    """
    Extract summary metadata from LRC schema.

    Args:
        schema: LRC schema dictionary

    Returns:
        Simplified metadata dictionary
    """
    return {
        "project": schema.get("project", "unknown"),
        "schema_version": schema.get("version", "1.0"),
        "rules_count": len(schema.get("rules", [])),
        "description": schema.get("description", ""),
    }


def extract_rules_from_schema(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract audit rules from LRC schema.

    Args:
        schema: LRC schema dictionary

    Returns:
        List of rule dictionaries
    """
    return schema.get("rules", [])


__all__ = [
    "LRC_CONFIG_PATH",
    "extract_rules_from_schema",
    "load_lrc_build",
    "load_lrc_config",
    "merge_lrc_metadata",
    "run_audit",
    "select_schema",
    "summarize_metadata",
    "write_lrc_audit",
]
