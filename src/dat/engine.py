"""Audit engine: capability profiles, isolated analyzer execution, suppression and the gate.

Outcomes (docs/EXIT_CODES.md):
- completed  every selected analyzer ran or was legitimately skipped; findings and verdict are authoritative
- incomplete at least one analyzer failed; its findings are dropped and the verdict is not evaluated
- error      DAT could not audit at all (missing target, invalid policy, limit exceeded)
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dat import __version__
from dat.analyzers import BUILTIN, Analyzer, RunOptions, Skip
from dat.analyzers.artifact import archive_kind, sha256_file
from dat.analyzers.base import inline_suppressed
from dat.errors import DatError, ErrorCode
from dat.inventory import DEFAULT_MAX_FILE_BYTES, DEFAULT_MAX_FILES, Inventory
from dat.model import REPORT_CONTRACT, REPORT_VERSION, Category, Confidence, Finding, Severity, assign_fingerprints
from dat.policy import Policy

ALL_CATEGORIES = frozenset(Category)


@dataclass(frozen=True)
class Profile:
    capability: str
    summary: str
    target_kind: str
    analyzers: tuple[str, ...]
    categories: frozenset[Category] = ALL_CATEGORIES
    rule_filter: Callable[[str], bool] | None = None


REPO_ANALYZERS = ("secrets", "quality", "python", "javascript", "android", "container", "packaging", "release", "git")


def _package_rule(rule_id: str) -> bool:
    return rule_id.startswith("package.") or (rule_id.startswith("js.") and rule_id != "js.dynamic-code")


PROFILES: dict[str, Profile] = {
    p.capability: p
    for p in (
        Profile(
            "repo.audit",
            "Full repository audit: security, quality, packaging and release.",
            "repository",
            REPO_ANALYZERS,
        ),
        Profile(
            "repo.security",
            "Security findings only.",
            "repository",
            ("secrets", "python", "javascript", "android", "container", "packaging"),
            frozenset({Category.SECURITY}),
        ),
        Profile(
            "repo.quality",
            "Code and repository hygiene.",
            "repository",
            ("quality", "python", "javascript"),
            frozenset({Category.QUALITY}),
        ),
        Profile(
            "repo.release",
            "Release readiness gate: release metadata, git state, packaging, secrets, conflicts.",
            "repository",
            ("release", "git", "packaging", "secrets", "quality"),
            rule_filter=lambda r: r != "quality.work-marker",
        ),
        Profile(
            "package.audit",
            "Package manifests: metadata, pinning, lockfiles, install scripts.",
            "repository",
            ("packaging", "javascript"),
            rule_filter=_package_rule,
        ),
        Profile(
            "artifact.audit",
            "Built artifacts: archive safety, RECORD and checksum integrity, embedded secrets.",
            "artifact",
            ("artifact",),
        ),
    )
}


def _analyzer_classes(profile: Profile, plugins: list[tuple[str, type[Analyzer]]]) -> list[tuple[str, type[Analyzer]]]:
    by_id = {a.id: a for a in BUILTIN}
    selected = [("builtin", by_id[a]) for a in profile.analyzers]
    selected += [(f"plugin:{ref}", cls) for ref, cls in plugins if profile.target_kind in cls.target_kinds]
    return sorted(selected, key=lambda pair: pair[1].id)


def error_report(
    capability: str,
    target_label: str,
    error: DatError,
    *,
    linkage: dict[str, Any] | None = None,
    policy: Policy | None = None,
) -> dict[str, Any]:
    profile = PROFILES.get(capability)
    return _envelope(
        capability,
        {"kind": profile.target_kind if profile else "unknown", "path": target_label},
        outcome="error",
        findings=[],
        suppressed=[],
        analyzers=[],
        errors=[error.to_dict()],
        policy=policy,
        linkage=linkage,
    )


def run(
    capability: str,
    target: Path,
    *,
    policy: Policy,
    target_label: str | None = None,
    plugins: list[tuple[str, type[Analyzer]]] | None = None,
    enforce_gate: bool = False,
    linkage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    label = target_label if target_label is not None else str(target)
    profile = PROFILES[capability]
    try:
        inv = Inventory.build(
            target,
            ignore=policy.ignore_paths,
            max_files=policy.max_files or DEFAULT_MAX_FILES,
            max_file_bytes=policy.max_file_bytes or DEFAULT_MAX_FILE_BYTES,
        )
    except DatError as exc:
        return error_report(capability, label, exc, linkage=linkage, policy=policy)

    options = RunOptions(target_kind=profile.target_kind, settings={})
    runs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    collected: list[Finding] = []
    for origin, cls in _analyzer_classes(profile, plugins or []):
        entry: dict[str, Any] = {"id": cls.id, "version": cls.version, "origin": origin}
        if cls.id in policy.disabled:
            runs.append({**entry, "status": "disabled", "findings": 0})
            continue
        try:
            found = _run_isolated(cls, inv, options)
        except Skip as skip:
            runs.append({**entry, "status": "skipped", "reason": skip.reason, "findings": 0})
            continue
        except DatError as exc:
            runs.append({**entry, "status": "failed", "findings": 0})
            errors.append({**exc.to_dict(), "analyzer": cls.id})
            continue
        kept = [
            f
            for f in found
            if f.category in profile.categories and (profile.rule_filter is None or profile.rule_filter(f.rule_id))
        ]
        runs.append({**entry, "status": "ok", "findings": len(kept)})
        collected.extend(kept)

    collected = [
        dataclasses.replace(f, severity=policy.severity_overrides[f.rule_id])
        if f.rule_id in policy.severity_overrides
        else f
        for f in collected
    ]
    findings: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    for f, fp in assign_fingerprints(collected):
        doc = f.to_dict(fp)
        reason = _suppression(doc, inv, policy)
        if reason is None:
            findings.append(doc)
        else:
            suppressed.append(
                {
                    "fingerprint": fp,
                    "rule_id": f.rule_id,
                    "location": doc["location"],
                    "severity": doc["severity"],
                    **reason,
                }
            )

    target_doc: dict[str, Any] = {
        "kind": profile.target_kind,
        "path": label,
        "files": len(inv.files),
        "inventory": inv.source,
        "skipped": [{"path": s.rel, "reason": s.reason} for s in inv.skipped_sorted()],
    }
    if profile.target_kind == "artifact":
        target_doc["artifacts"] = [
            {"path": f.rel, "size": f.size, "sha256": sha256_file(inv.path(f)), "archive": archive_kind(f.name)}
            for f in inv.files
        ]
    return _envelope(
        capability,
        target_doc,
        outcome="incomplete" if errors else "completed",
        findings=findings,
        suppressed=suppressed,
        analyzers=runs,
        errors=errors,
        policy=policy,
        linkage=linkage,
        enforce_gate=enforce_gate,
    )


def _run_isolated(cls: type[Analyzer], inv: Inventory, options: RunOptions) -> list[Finding]:
    """Run one analyzer. Any exception, or any output that is not a valid Finding, fails only that analyzer."""
    try:
        analyzer = cls()
        out: list[Finding] = []
        for f in analyzer.run(inv, options):
            if not isinstance(f, Finding):
                raise DatError(
                    ErrorCode.ANALYZER_CONTRACT_VIOLATION,
                    "Analyzer yielded a non-Finding.",
                    details={"type": type(f).__name__},
                )
            out.append(f if f.analyzer == cls.id else dataclasses.replace(f, analyzer=cls.id))
        return out
    except (Skip, DatError):
        raise
    except Exception as exc:  # noqa: BLE001 - failure isolation: report, never crash the audit
        raise DatError(
            ErrorCode.ANALYZER_FAILED,
            f"Analyzer {cls.id} failed.",
            details={"exception_type": type(exc).__name__},
        ) from None


def _suppression(doc: dict[str, Any], inv: Inventory, policy: Policy) -> dict[str, str] | None:
    loc = doc["location"]
    if loc["path"] and loc["line"] and loc["member"] is None:
        entry = inv.get(loc["path"])
        text = inv.text(entry) if entry else None
        if text is not None:
            lines = text.splitlines()
            if 0 < loc["line"] <= len(lines) and inline_suppressed(lines[loc["line"] - 1], doc["rule_id"]):
                return {"source": "inline", "reason": "dat:ignore comment"}
    for s in policy.suppress:
        if s.matches(doc):
            return {"source": "policy", "reason": s.reason}
    return None


def _envelope(
    capability: str,
    target: dict[str, Any],
    *,
    outcome: str,
    findings: list[dict[str, Any]],
    suppressed: list[dict[str, Any]],
    analyzers: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    policy: Policy | None,
    linkage: dict[str, Any] | None,
    enforce_gate: bool = False,
) -> dict[str, Any]:
    policy_doc = policy.describe() if policy else None
    gate: dict[str, Any] = {
        "enforced": enforce_gate,
        "fail_on": policy.fail_on.label if policy else None,
        "min_confidence": policy.min_confidence.label if policy else None,
        "blocking": None,
        "verdict": "not_evaluated",
    }
    if outcome == "completed" and policy is not None:
        blocking = sum(1 for f in findings if policy.blocks(f))
        gate["blocking"] = blocking
        gate["verdict"] = "fail" if blocking else "pass"
    by_sev = Counter(f["severity"] for f in findings)
    by_cat = Counter(f["category"] for f in findings)
    return {
        "contract": REPORT_CONTRACT,
        "version": REPORT_VERSION,
        "tool": {"name": "dat", "version": __version__},
        "capability": capability,
        "target": target,
        "outcome": outcome,
        "gate": gate,
        "summary": {
            "findings": len(findings),
            "suppressed": len(suppressed),
            "by_severity": {s.label: by_sev.get(s.label, 0) for s in sorted(Severity, reverse=True)},
            "by_category": {c.value: by_cat.get(c.value, 0) for c in Category},
            "by_confidence_high": sum(1 for f in findings if f["confidence"] == Confidence.HIGH.label),
        },
        "findings": findings,
        "suppressed": suppressed,
        "analyzers": analyzers,
        "errors": errors,
        "policy": policy_doc,
        "linkage": linkage,
    }
