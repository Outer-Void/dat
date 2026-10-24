"""Finding and report model: the stable machine surface of DAT (docs/FINDINGS.md).

Everything here is deterministic. Reports carry no timestamps, durations, host names or absolute paths
unless the caller supplied them, so the same input always produces byte-identical JSON.
"""

from __future__ import annotations

import enum
import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

FINDING_VERSION = 1
REPORT_CONTRACT = "dat.report"
REPORT_VERSION = 1

RULE_ID = re.compile(r"^[a-z][a-z0-9]*(\.[a-z0-9][a-z0-9-]*)+$")
MAX_EVIDENCE = 200


class Severity(enum.IntEnum):
    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @property
    def label(self) -> str:
        return self.name.lower()

    @classmethod
    def parse(cls, value: str) -> Severity:
        try:
            return cls[value.upper()]
        except (KeyError, AttributeError):
            raise ValueError(f"unknown severity {value!r}") from None


class Confidence(enum.IntEnum):
    """How sure a deterministic rule is that a match is a real problem (not how bad it is)."""

    LOW = 0
    MEDIUM = 1
    HIGH = 2

    @property
    def label(self) -> str:
        return self.name.lower()

    @classmethod
    def parse(cls, value: str) -> Confidence:
        try:
            return cls[value.upper()]
        except (KeyError, AttributeError):
            raise ValueError(f"unknown confidence {value!r}") from None


class Category(enum.StrEnum):
    SECURITY = "security"
    QUALITY = "quality"
    RELEASE = "release"
    PACKAGE = "package"
    ARTIFACT = "artifact"


@dataclass(frozen=True)
class Location:
    path: str | None = None
    line: int | None = None
    column: int | None = None
    member: str | None = None  # entry inside an archive

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "line": self.line, "column": self.column, "member": self.member}


@dataclass(frozen=True)
class Finding:
    rule_id: str
    category: Category
    severity: Severity
    confidence: Confidence
    title: str
    message: str
    location: Location = field(default_factory=Location)
    evidence: str | None = None  # always redacted by the analyzer
    remediation: str | None = None
    analyzer: str = ""

    def __post_init__(self) -> None:
        if not RULE_ID.match(self.rule_id):
            raise ValueError(f"invalid rule_id {self.rule_id!r}")
        if not isinstance(self.severity, Severity) or not isinstance(self.confidence, Confidence):
            raise TypeError("severity and confidence must be Severity and Confidence")
        if not isinstance(self.category, Category):
            raise TypeError("category must be a Category")
        if self.evidence is not None and len(self.evidence) > MAX_EVIDENCE:
            object.__setattr__(self, "evidence", self.evidence[: MAX_EVIDENCE - 1] + "…")

    def sort_key(self) -> tuple[Any, ...]:
        loc = self.location
        return (loc.path or "", loc.member or "", loc.line or 0, loc.column or 0, self.rule_id, self.message)

    def base_fingerprint(self) -> str:
        """Stable across line moves: rule, file, archive member and evidence, but not line numbers."""
        loc = self.location
        raw = "\x1f".join([self.rule_id, loc.path or "", loc.member or "", self.evidence or self.message])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_dict(self, fingerprint: str) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "analyzer": self.analyzer,
            "category": self.category.value,
            "severity": self.severity.label,
            "confidence": self.confidence.label,
            "title": self.title,
            "message": self.message,
            "location": self.location.to_dict(),
            "evidence": self.evidence,
            "remediation": self.remediation,
            "fingerprint": fingerprint,
        }


def assign_fingerprints(findings: Iterable[Finding]) -> list[tuple[Finding, str]]:
    """Sort findings and give each a fingerprint; repeated identical matches get an occurrence suffix."""
    seen: dict[str, int] = {}
    out: list[tuple[Finding, str]] = []
    for f in sorted(findings, key=Finding.sort_key):
        base = f.base_fingerprint()
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.append((f, base if n == 0 else hashlib.sha256(f"{base}:{n}".encode()).hexdigest()))
    return out


def canonical_json(doc: Any) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def redact(value: str, keep: int = 4) -> str:
    """Show a short prefix and the length, never the secret itself."""
    value = value.strip()
    if len(value) <= keep * 2:
        return f"[REDACTED len={len(value)}]"
    return f"{value[:keep]}…[REDACTED len={len(value)}]"
