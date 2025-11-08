"""Policy evaluation helpers for DAT."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Protocol


class FileRecordLike(Protocol):
    path: str
    binary: bool


@dataclass
class RuleFinding:
    """Represents a policy finding generated during scans."""

    rule_id: str
    message: str
    severity: str
    path: str | None = None


DEFAULT_RULES = (
    ("no.todo", "TODO comment detected", "low"),
    ("no.merge", "Potential merge conflict marker", "medium"),
    ("secrets.api_key", "Potential API key exposure", "high"),
    ("credentials.password", "Potential password in source", "critical"),
)

RULE_LOOKUP = {rule_id: (message, severity) for rule_id, message, severity in DEFAULT_RULES}

RULE_PATTERNS = {
    "no.todo": ["TODO"],
    "no.merge": ["<<<<", ">>>>"],
    "secrets.api_key": ["API_KEY", "SECRET_KEY", "x-api-key"],
    "credentials.password": ["password=", "pwd=", "PASS=", "SECRET="],
}


def evaluate_rules(root: Path, files: Iterable[FileRecordLike]) -> List[RuleFinding]:
    """Evaluate :data:`DEFAULT_RULES` against scanned *files* within *root*."""

    findings: List[RuleFinding] = []
    for record in files:
        if record.binary:
            continue
        if record.path.endswith(".md"):
            continue
        try:
            text = (root / record.path).read_text(encoding="utf-8")
        except Exception:
            continue
        for rule_id, patterns in RULE_PATTERNS.items():
            if any(pattern in text for pattern in patterns) and rule_id in RULE_LOOKUP:
                message, severity = RULE_LOOKUP[rule_id]
                findings.append(RuleFinding(rule_id, message, severity, record.path))
    return findings


__all__ = ["RuleFinding", "DEFAULT_RULES", "RULE_LOOKUP", "evaluate_rules"]

