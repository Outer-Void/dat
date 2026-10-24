"""policy.verify: validate a DAT policy and, optionally, re-gate an existing report against it.

An invalid policy is a verification *result* (verdict fail, exit 1). Being unable to read the policy or
the report is an execution *failure* (outcome error, exit 3).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dat import __version__
from dat.errors import DatError, ErrorCode
from dat.model import REPORT_CONTRACT, REPORT_VERSION
from dat.policy import from_document, read_document, validate

VERIFICATION_CONTRACT = "dat.policy-verification"
FINDING_KEYS = {"rule_id", "severity", "confidence", "location", "fingerprint"}


def _envelope(policy_label: str) -> dict[str, Any]:
    return {
        "contract": VERIFICATION_CONTRACT,
        "version": 1,
        "tool": {"name": "dat", "version": __version__},
        "capability": "policy.verify",
        "outcome": "completed",
        "verdict": "not_evaluated",
        "policy": {"path": policy_label, "valid": None, "problems": [], "policy_id": None, "sha256": None},
        "report": None,
        "errors": [],
    }


def _read_report(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_bytes())
    except FileNotFoundError:
        raise DatError(ErrorCode.INVALID_REPORT, "Report file not found.", details={"path": str(path)}) from None
    except (OSError, ValueError) as exc:
        raise DatError(
            ErrorCode.INVALID_REPORT, "Report is not readable JSON.", details={"exception_type": type(exc).__name__}
        ) from None
    if not isinstance(doc, dict) or doc.get("contract") != REPORT_CONTRACT or doc.get("version") != REPORT_VERSION:
        raise DatError(ErrorCode.INVALID_REPORT, f"Not a {REPORT_CONTRACT} v{REPORT_VERSION} document.")
    findings = doc.get("findings")
    if not isinstance(findings, list) or not all(isinstance(f, dict) and set(f) >= FINDING_KEYS for f in findings):
        raise DatError(ErrorCode.INVALID_REPORT, "Report findings are malformed.")
    return doc


def verify(
    policy_path: Path,
    report_path: Path | None = None,
    *,
    policy_label: str | None = None,
    report_label: str | None = None,
) -> dict[str, Any]:
    out = _envelope(policy_label or str(policy_path))
    try:
        doc = read_document(policy_path)
    except DatError as exc:
        if exc.code in (ErrorCode.POLICY_NOT_FOUND, ErrorCode.POLICY_UNREADABLE):
            out.update(outcome="error", errors=[exc.to_dict()])
            return out
        doc = None  # unparseable JSON is an invalid policy, not a failure to read
        out["policy"].update(valid=False, problems=[exc.message, *[str(v) for v in exc.details.values()]])
    if doc is not None:
        problems = validate(doc)
        out["policy"].update(valid=not problems, problems=problems)
    if not out["policy"]["valid"]:
        out["verdict"] = "fail"
        return out
    policy = from_document(doc)
    out["policy"].update(policy_id=policy.policy_id, sha256=policy.sha256)
    out["verdict"] = "pass"
    if report_path is None:
        return out
    try:
        report = _read_report(report_path)
    except DatError as exc:
        out.update(outcome="error", verdict="not_evaluated", errors=[exc.to_dict()])
        return out

    blocking: list[str] = []
    suppressed = 0
    for f in report["findings"]:
        f2 = dict(f)
        if f["rule_id"] in policy.severity_overrides:
            f2["severity"] = policy.severity_overrides[f["rule_id"]].label
        if any(s.matches(f2) for s in policy.suppress):
            suppressed += 1
            continue
        try:
            if policy.blocks(f2):
                blocking.append(f2["fingerprint"])
        except ValueError:
            out.update(
                outcome="error",
                verdict="not_evaluated",
                errors=[DatError(ErrorCode.INVALID_REPORT, "Finding has an unknown severity.").to_dict()],
            )
            return out
    evaluated = report.get("outcome") == "completed"
    out["report"] = {
        "path": report_label or str(report_path),
        "capability": report.get("capability"),
        "outcome": report.get("outcome"),
        "findings": len(report["findings"]),
        "newly_suppressed": suppressed,
        "blocking": len(blocking) if evaluated else None,
        "blocking_fingerprints": sorted(blocking) if evaluated else [],
        "fail_on": policy.fail_on.label,
        "min_confidence": policy.min_confidence.label,
    }
    out["verdict"] = ("fail" if blocking else "pass") if evaluated else "not_evaluated"
    return out
