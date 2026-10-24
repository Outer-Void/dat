"""Analyzer interface. Built-ins and plugins implement the same contract (docs/PLUGINS.md).

An analyzer is deterministic: given the same inventory and options it yields the same findings. It must
not use the network, AI models or wall-clock time to decide anything. Raising means "I could not
analyze", which the engine records as an execution failure, never as a clean result.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, ClassVar

from dat.inventory import Inventory
from dat.model import Category, Confidence, Finding, Location, Severity


class Skip(Exception):  # noqa: N818 - control flow, not an error
    """Raise from `run` when the analyzer legitimately cannot apply (recorded as status `skipped`)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Rule:
    rule_id: str
    category: Category
    severity: Severity
    confidence: Confidence
    title: str
    remediation: str | None = None


@dataclass(frozen=True)
class RunOptions:
    target_kind: str  # "repository" | "artifact"
    settings: dict[str, Any]


class Analyzer:
    id: ClassVar[str]
    version: ClassVar[str]
    target_kinds: ClassVar[frozenset[str]] = frozenset({"repository"})
    rules: ClassVar[tuple[Rule, ...]] = ()

    def run(self, inv: Inventory, options: RunOptions) -> Iterator[Finding]:
        raise NotImplementedError

    def rule(self, rule_id: str) -> Rule:
        for r in self.rules:
            if r.rule_id == rule_id:
                return r
        raise KeyError(rule_id)

    def finding(
        self,
        rule_id: str,
        message: str,
        *,
        path: str | None = None,
        line: int | None = None,
        column: int | None = None,
        member: str | None = None,
        evidence: str | None = None,
        severity: Severity | None = None,
        confidence: Confidence | None = None,
    ) -> Finding:
        r = self.rule(rule_id)
        return Finding(
            rule_id=r.rule_id,
            category=r.category,
            severity=severity if severity is not None else r.severity,
            confidence=confidence if confidence is not None else r.confidence,
            title=r.title,
            message=message,
            location=Location(path=path, line=line, column=column, member=member),
            evidence=evidence,
            remediation=r.remediation,
            analyzer=self.id,
        )

    @classmethod
    def describe(cls) -> dict[str, Any]:
        return {
            "id": cls.id,
            "version": cls.version,
            "target_kinds": sorted(cls.target_kinds),
            "rules": [
                {
                    "rule_id": r.rule_id,
                    "category": r.category.value,
                    "severity": r.severity.label,
                    "confidence": r.confidence.label,
                    "title": r.title,
                }
                for r in cls.rules
            ],
        }


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def inline_suppressed(line: str, rule_id: str) -> bool:
    """`dat:ignore` silences every rule on that line; `dat:ignore[rule.id,other.id]` only those."""
    idx = line.find("dat:ignore")
    if idx < 0:
        return False
    rest = line[idx + len("dat:ignore") :]
    if not rest.startswith("["):
        return True
    end = rest.find("]")
    if end < 0:
        return False
    return rule_id in {p.strip() for p in rest[1:end].split(",")}
