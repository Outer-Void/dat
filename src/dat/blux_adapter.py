"""BLUX capability adapter (`dat blux-capability <capability_id>`), BLUX ADR-0009 subprocess protocol.

stdin:  {"capability_id": str, "input": {...}, "context": {"operation_id": ..., ...}}
stdout: exactly one JSON object
  exit 0  the capability's output: a complete dat.report (or dat.policy-verification) document.
          A failed gate is an audit result, so it is still exit 0 with verdict "fail".
  exit 1  {"error": {"code", "message", "details"}}: DAT could not complete the audit.

This module imports nothing from BLUX. DAT stays standalone (ADR-0002); BLUX validates the envelope.
Plugins cannot be requested through BLUX: remote input never chooses code to load.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import IO, Any

from dat import engine, verify
from dat.errors import DatError, ErrorCode
from dat.model import Confidence, Severity
from dat.policy import DEFAULT_POLICY, Policy, load

MAX_REQUEST_BYTES = 1024 * 1024
CAPABILITIES = (*engine.PROFILES, "policy.verify")
AUDIT_INPUT_KEYS = {"target", "policy", "use_target_policy", "fail_on", "min_confidence"}
CONTEXT_KEYS = ("operation_id", "parent_operation_id", "session_id", "project_id", "capability_id")


def _fail(code: ErrorCode, message: str, details: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    return 1, {"error": {"code": code.value, "message": message, "details": details or {}}}


def _linkage(context: Any, capability_id: str) -> dict[str, Any]:
    ctx = context if isinstance(context, dict) else {}
    link = {k: ctx.get(k) if isinstance(ctx.get(k), str) else None for k in CONTEXT_KEYS}
    link["capability_id"] = capability_id
    link["system"] = "blux"
    return link


def handle(capability_id: str, raw: bytes) -> tuple[int, dict[str, Any]]:
    if len(raw) > MAX_REQUEST_BYTES:
        return _fail(ErrorCode.INVALID_INPUT, "Request too large.")
    try:
        req = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _fail(ErrorCode.INVALID_INPUT, "Request is not JSON.")
    if not isinstance(req, dict) or not isinstance(req.get("input"), dict):
        return _fail(ErrorCode.INVALID_INPUT, "Request must be an object with an input object.")
    if req.get("capability_id") not in (None, capability_id):
        return _fail(ErrorCode.INVALID_INPUT, "capability_id does not match the invoked capability.")
    inp: dict[str, Any] = req["input"]
    linkage = _linkage(req.get("context"), capability_id)

    if capability_id == "policy.verify":
        return _policy_verify(inp, linkage)

    unknown = sorted(set(inp) - AUDIT_INPUT_KEYS)
    if unknown or not isinstance(inp.get("target"), str) or not inp["target"]:
        return _fail(ErrorCode.INVALID_INPUT, "Input needs a target path and only known keys.", {"unknown": unknown})
    target = Path(inp["target"])
    try:
        policy = _policy(inp, target)
    except DatError as exc:
        return _fail(exc.code, exc.message, exc.details)
    report = engine.run(capability_id, target, policy=policy, target_label=inp["target"], linkage=linkage)
    if report["outcome"] != "completed":
        first = report["errors"][0] if report["errors"] else {"code": ErrorCode.INTERNAL_ERROR.value}
        return 1, {
            "error": {
                "code": first["code"],
                "message": f"DAT audit {report['outcome']}: {first.get('message', 'no detail')}",
                "details": {
                    "outcome": report["outcome"],
                    "errors": report["errors"],
                    "analyzers": [a for a in report["analyzers"] if a["status"] == "failed"],
                },
            }
        }
    return 0, report


def _policy(inp: dict[str, Any], target: Path) -> Policy:
    if isinstance(inp.get("policy"), str):
        policy = load(Path(inp["policy"]), None)
    elif inp.get("use_target_policy", True) is True:
        policy = load(None, target if target.is_dir() else None)
    else:
        policy = DEFAULT_POLICY
    overrides: dict[str, Any] = {}
    try:
        if "fail_on" in inp:
            overrides["fail_on"] = Severity.parse(inp["fail_on"])
        if "min_confidence" in inp:
            overrides["min_confidence"] = Confidence.parse(inp["min_confidence"])
    except ValueError as exc:
        raise DatError(ErrorCode.INVALID_INPUT, str(exc)) from None
    if overrides:
        from dataclasses import replace

        policy = replace(policy, **overrides)
    return policy


def _policy_verify(inp: dict[str, Any], linkage: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    if (
        not isinstance(inp.get("policy"), str)
        or set(inp) - {"policy", "report"}
        or ("report" in inp and not isinstance(inp["report"], str))
    ):
        return _fail(ErrorCode.INVALID_INPUT, "Input needs a policy path and optionally a report path.")
    report = Path(inp["report"]) if "report" in inp else None
    doc = verify.verify(Path(inp["policy"]), report, policy_label=inp["policy"], report_label=inp.get("report"))
    if doc["outcome"] != "completed":
        err = doc["errors"][0]
        return 1, {"error": {"code": err["code"], "message": err["message"], "details": {"errors": doc["errors"]}}}
    doc["linkage"] = linkage
    return 0, doc


def main(capability_id: str, stdin: IO[bytes] | None = None, stdout: IO[str] | None = None) -> int:
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout
    try:
        code, doc = handle(capability_id, stdin.read(MAX_REQUEST_BYTES + 1))
    except Exception as exc:  # noqa: BLE001 - the protocol must always answer with one document
        code, doc = _fail(ErrorCode.INTERNAL_ERROR, "Internal DAT error.", {"exception_type": type(exc).__name__})
    stdout.write(json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
    stdout.flush()
    return code
