"""Audit helpers for LRC integration."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable

from ..report import serialise_findings, serialise_scan
from ..rules import RuleFinding
from ..scanner import ScanResult
from ..utils import atomic_write, load_json, merge_dicts
from .lrc import LRC_CONFIG_PATH, resolve_lrc_config_path

def load_lrc_config(path: Path | None = None) -> Dict[str, Any]:
    """Load LRC integration configuration."""

    config_path = resolve_lrc_config_path(path)
    return load_json(config_path) or {"schemas": []}


def load_lrc_build(repo_root: Path) -> Dict[str, Any]:
    """Load `.lrc-build.json` from *repo_root* if present."""

    return load_json(repo_root / ".lrc-build.json") or {}


def merge_lrc_metadata(config: Dict[str, Any], build: Dict[str, Any]) -> Dict[str, Any]:
    """Merge LRC config and build metadata."""

    return merge_dicts(config, build)


def write_lrc_audit(
    repo_root: Path,
    result: ScanResult,
    findings: Iterable[RuleFinding],
    metadata: dict,
    *,
    build_context: Dict[str, Any] | None = None,
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
    atomic_write(output_path, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
    return output_path


__all__ = [
    "LRC_CONFIG_PATH",
    "load_lrc_config",
    "load_lrc_build",
    "merge_lrc_metadata",
    "write_lrc_audit",
]

