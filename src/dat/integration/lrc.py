"""Integration helpers for LRC generated metadata."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

from ..utils import merge_dicts

DEFAULT_LRC_CONFIG_PATH = Path.home() / ".config" / "lrc" / "dat_integration.json"
LRC_CONFIG_PATH = DEFAULT_LRC_CONFIG_PATH


class LRCIntegrationError(RuntimeError):
    """Raised when LRC integration fails."""


def _default_config() -> Dict[str, Any]:
    return {"schemas": []}


def resolve_lrc_config_path(path: Path | None = None) -> Path:
    if path:
        return Path(path)
    env_path = os.environ.get("LRC_CONFIG_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_LRC_CONFIG_PATH


def load_integration_config(path: Path | None = None) -> Dict[str, Any]:
    """Load the LRC integration configuration file.

    The loader is intentionally forgiving – missing or malformed configuration
    files simply return an empty schema list so that enterprise environments can
    bootstrap without manual setup.
    """

    config_path = resolve_lrc_config_path(path)
    if not config_path.exists():
        return _default_config()
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _default_config()

    if not isinstance(config, dict):
        return _default_config()
    schemas = config.get("schemas", [])
    if not isinstance(schemas, list):
        config["schemas"] = []
    return config


def select_schema(config: Dict[str, Any], repo_name: str | None) -> Dict[str, Any] | None:
    """Select the schema entry matching *repo_name*.

    Schema ``repos`` entries can contain exact names, glob-style patterns or
    regular expressions. The first matching schema is returned and the final
    fallback is the first schema without any ``repos`` definition.
    """

    schemas: Iterable[Dict[str, Any]] = config.get("schemas", [])  # type: ignore[assignment]
    default_schema = None
    for schema in schemas:
        targets: List[str] = schema.get("repos", [])  # type: ignore[assignment]
        if not targets:
            default_schema = default_schema or schema
            continue
        if not repo_name:
            continue
        for pattern in targets:
            if pattern == repo_name:
                return schema
            try:
                if re.fullmatch(pattern, repo_name):
                    return schema
            except re.error:  # pragma: no cover - defensive for invalid patterns
                continue
    return default_schema


def extract_rules_from_schema(schema: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    """Return policy rules defined inside *schema*."""

    if not schema:
        return []
    rules = schema.get("rules")
    if not isinstance(rules, list):
        return []
    extracted: List[Dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        entry = dict(rule)
        entry.setdefault("severity", "medium")
        extracted.append(entry)
    return extracted


def summarize_metadata(schema: Dict[str, Any] | None) -> Dict[str, Any]:
    """Return the metadata subset relevant for audit reports."""

    if not schema:
        return {}
    allowed_keys = {"owner", "repository", "compliance", "notes", "version", "build_id"}
    return {key: value for key, value in schema.items() if key in allowed_keys}


def merge_lrc_metadata(config_metadata: Dict[str, Any], build_metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge config and build metadata with build values taking precedence."""

    return merge_dicts(config_metadata, build_metadata)


__all__ = [
    "LRCIntegrationError",
    "load_integration_config",
    "select_schema",
    "extract_rules_from_schema",
    "summarize_metadata",
    "merge_lrc_metadata",
]

