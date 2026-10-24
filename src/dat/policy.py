"""DAT policy documents (`dat.policy` v1, docs/POLICY.md) and the release gate.

Policies are data. Validation is strict: unknown keys, bad severities, unreasoned suppressions or
negated ignore patterns are rejected rather than guessed at.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dat.errors import DatError, ErrorCode
from dat.model import RULE_ID, Confidence, Severity, canonical_json

POLICY_CONTRACT = "dat.policy"
POLICY_VERSION = 1
DEFAULT_POLICY_FILES = (".dat/policy.json", "dat-policy.json")
TOP_KEYS = {
    "contract",
    "version",
    "policy_id",
    "description",
    "gate",
    "analyzers",
    "severity_overrides",
    "suppress",
    "ignore_paths",
    "limits",
}


@dataclass(frozen=True)
class Suppression:
    reason: str
    rule_id: str | None = None
    path: str | None = None
    fingerprint: str | None = None

    def matches(self, finding: dict[str, Any]) -> bool:
        if self.fingerprint is not None and finding["fingerprint"] != self.fingerprint:
            return False
        if self.rule_id is not None and not fnmatch.fnmatchcase(finding["rule_id"], self.rule_id):
            return False
        if self.path is not None:
            path = finding["location"]["path"]
            if path is None or not fnmatch.fnmatchcase(path, self.path):
                return False
        return True


@dataclass(frozen=True)
class Policy:
    policy_id: str = "dat-default"
    fail_on: Severity = Severity.HIGH
    min_confidence: Confidence = Confidence.MEDIUM
    disabled: frozenset[str] = frozenset()
    severity_overrides: dict[str, Severity] = field(default_factory=dict)
    suppress: tuple[Suppression, ...] = ()
    ignore_paths: tuple[str, ...] = ()
    max_files: int | None = None
    max_file_bytes: int | None = None
    source: dict[str, Any] | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical_json(self.source or {"default": True}).encode()).hexdigest()

    def describe(self) -> dict[str, Any]:
        return {"policy_id": self.policy_id, "sha256": self.sha256, "default": self.source is None}

    def blocks(self, finding: dict[str, Any]) -> bool:
        return (
            Severity.parse(finding["severity"]) >= self.fail_on
            and Confidence.parse(finding["confidence"]) >= self.min_confidence
        )


DEFAULT_POLICY = Policy()


def validate(doc: Any) -> list[str]:
    """Return every problem with a policy document (empty when valid)."""
    if not isinstance(doc, dict):
        return ["policy must be a JSON object"]
    problems: list[str] = []
    if doc.get("contract") != POLICY_CONTRACT:
        problems.append(f"contract must be {POLICY_CONTRACT!r}")
    version = doc.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        problems.append("version must be an integer")
    elif version != POLICY_VERSION:
        problems.append(f"unsupported policy version {version} (supported: {POLICY_VERSION})")
    for key in sorted(set(doc) - TOP_KEYS):
        problems.append(f"unknown key {key!r}")
    pid = doc.get("policy_id", "x")
    if not isinstance(pid, str) or not pid or len(pid) > 128:
        problems.append("policy_id must be a non-empty string of at most 128 characters")

    gate = doc.get("gate", {})
    if not isinstance(gate, dict):
        problems.append("gate must be an object")
    else:
        for key in sorted(set(gate) - {"fail_on", "min_confidence"}):
            problems.append(f"unknown key gate.{key}")
        problems += _enum(gate, "fail_on", Severity, "gate.fail_on")
        problems += _enum(gate, "min_confidence", Confidence, "gate.min_confidence")

    analyzers = doc.get("analyzers", {})
    if not isinstance(analyzers, dict) or set(analyzers) - {"disable"}:
        problems.append("analyzers must be an object with only 'disable'")
    elif not _str_list(analyzers.get("disable", [])):
        problems.append("analyzers.disable must be a list of strings")

    overrides = doc.get("severity_overrides", {})
    if not isinstance(overrides, dict):
        problems.append("severity_overrides must be an object")
    else:
        for rule, sev in sorted(overrides.items()):
            if not RULE_ID.match(rule):
                problems.append(f"severity_overrides: invalid rule id {rule!r}")
            problems += _enum({"s": sev}, "s", Severity, f"severity_overrides.{rule}")

    suppress = doc.get("suppress", [])
    if not isinstance(suppress, list):
        problems.append("suppress must be a list")
    else:
        for i, s in enumerate(suppress):
            where = f"suppress[{i}]"
            if not isinstance(s, dict):
                problems.append(f"{where} must be an object")
                continue
            for key in sorted(set(s) - {"rule_id", "path", "fingerprint", "reason"}):
                problems.append(f"unknown key {where}.{key}")
            if not isinstance(s.get("reason"), str) or len(s["reason"].strip()) < 3:
                problems.append(f"{where}.reason is required (why is this acceptable?)")
            if not any(k in s for k in ("rule_id", "path", "fingerprint")):
                problems.append(f"{where} must narrow by rule_id, path or fingerprint")
            if s.get("rule_id") in ("*", "**") and "path" not in s and "fingerprint" not in s:
                problems.append(f"{where} would suppress every finding")
            for key in ("rule_id", "path", "fingerprint"):
                if key in s and (not isinstance(s[key], str) or not s[key]):
                    problems.append(f"{where}.{key} must be a non-empty string")

    ignore = doc.get("ignore_paths", [])
    if not _str_list(ignore):
        problems.append("ignore_paths must be a list of strings")
    elif any(p.startswith("!") for p in ignore):
        problems.append("ignore_paths does not support negation ('!')")

    limits = doc.get("limits", {})
    if not isinstance(limits, dict) or set(limits) - {"max_files", "max_file_bytes"}:
        problems.append("limits may only contain max_files and max_file_bytes")
    else:
        for key, value in sorted(limits.items()):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                problems.append(f"limits.{key} must be a positive integer")
    return problems


def _enum(obj: dict[str, Any], key: str, enum_cls: type[Severity] | type[Confidence], where: str) -> list[str]:
    if key not in obj:
        return []
    value = obj[key]
    try:
        if not isinstance(value, str):
            raise ValueError
        enum_cls.parse(value)
    except ValueError:
        names = ", ".join(m.label for m in enum_cls)
        return [f"{where} must be one of: {names}"]
    return []


def _str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) and v for v in value)


def from_document(doc: Any) -> Policy:
    problems = validate(doc)
    if problems:
        raise DatError(ErrorCode.INVALID_POLICY, "Policy is invalid.", details={"problems": problems})
    gate = doc.get("gate", {})
    limits = doc.get("limits", {})
    return Policy(
        policy_id=doc.get("policy_id", "unnamed"),
        fail_on=Severity.parse(gate.get("fail_on", "high")),
        min_confidence=Confidence.parse(gate.get("min_confidence", "medium")),
        disabled=frozenset(doc.get("analyzers", {}).get("disable", [])),
        severity_overrides={k: Severity.parse(v) for k, v in doc.get("severity_overrides", {}).items()},
        suppress=tuple(
            Suppression(
                reason=s["reason"], rule_id=s.get("rule_id"), path=s.get("path"), fingerprint=s.get("fingerprint")
            )
            for s in doc.get("suppress", [])
        ),
        ignore_paths=tuple(doc.get("ignore_paths", [])),
        max_files=limits.get("max_files"),
        max_file_bytes=limits.get("max_file_bytes"),
        source=doc,
    )


def read_document(path: Path) -> Any:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise DatError(ErrorCode.POLICY_NOT_FOUND, "Policy file not found.", details={"path": str(path)}) from None
    except OSError as exc:
        raise DatError(ErrorCode.POLICY_UNREADABLE, "Policy file unreadable.", details={"errno": exc.errno}) from None
    try:
        return json.loads(raw, object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatError(ErrorCode.INVALID_POLICY, "Policy is not valid JSON.", details={"error": str(exc)}) from None
    except ValueError as exc:
        raise DatError(ErrorCode.INVALID_POLICY, str(exc)) from None


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"duplicate key {k!r}")
        out[k] = v
    return out


def load(path: Path | None, target_root: Path | None) -> Policy:
    """Explicit --policy wins; otherwise the target's own .dat/policy.json; otherwise the default."""
    if path is not None:
        return from_document(read_document(path))
    if target_root is not None:
        for name in DEFAULT_POLICY_FILES:
            candidate = target_root / name
            if candidate.is_file() and not candidate.is_symlink():
                return from_document(read_document(candidate))
    return DEFAULT_POLICY
