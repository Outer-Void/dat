"""`dat` command line. Standalone: needs no BLUX, CogA, cA or network.

Exit codes: 0 completed (gate passed or not enforced), 1 gate failed, 2 usage, 3 DAT failed to audit.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dat import __version__, blux_adapter, bundle, engine, render, signing, verify
from dat.analyzers import BUILTIN, installed_plugins, load_plugin
from dat.errors import EXIT_FAILURE, EXIT_GATE_FAILED, EXIT_OK, EXIT_USAGE, DatError, ErrorCode
from dat.model import Confidence, Severity
from dat.policy import DEFAULT_POLICY, Policy, load

AUDIT_COMMANDS = {
    "audit": "repo.audit",
    "security": "repo.security",
    "quality": "repo.quality",
    "release": "repo.release",
    "package": "package.audit",
    "artifact": "artifact.audit",
}
COMMANDS = {*AUDIT_COMMANDS, "policy", "analyzers", "version", "blux-capability", "bundle", "cmd"}
FORMATS = ("text", "json", "markdown", "sarif")
EXT_FORMAT = {".json": "json", ".md": "markdown", ".markdown": "markdown", ".sarif": "sarif", ".txt": "text"}
LEGACY_NOOP = ("--no-sign", "--deep", "--fast", "--audit", "--all")


def exit_code(report: dict[str, Any]) -> int:
    if report["outcome"] != "completed":
        return EXIT_FAILURE
    if report["gate"]["enforced"] and report["gate"]["verdict"] == "fail":
        return EXIT_GATE_FAILED
    return EXIT_OK


def _add_output_options(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("output")
    g.add_argument("--format", choices=FORMATS, default="text", help="stdout format (default: text)")
    g.add_argument("--pretty", action="store_true", help="indent JSON on stdout")
    g.add_argument("--json", dest="out_json", metavar="FILE", help="also write the JSON report to FILE")
    g.add_argument("--markdown", "--md", dest="out_markdown", metavar="FILE", help="also write Markdown to FILE")
    g.add_argument("--sarif", dest="out_sarif", metavar="FILE", help="also write SARIF 2.1.0 to FILE")
    g.add_argument("--text", dest="out_text", metavar="FILE", help="also write the text report to FILE")
    g.add_argument("--output", "-o", dest="out_auto", metavar="FILE", help="write FILE; format from its extension")
    g.add_argument(
        "--report", dest="out_report", metavar="PATH", help="write JSON to PATH (a directory gets PATH/dat-report.json)"
    )
    g.add_argument("--sign", action="store_true", help="gpg detached-sign every written file (fails if it cannot)")
    g.add_argument("--sign-key", metavar="KEYID", help="gpg key to sign with")
    g.add_argument("--quiet", "-q", action="store_true", help="print nothing on stdout")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dat",
        description="DAT: deterministic quality, security and release audits.",
        epilog="Exit codes: 0 ok, 1 gate failed, 2 usage error, 3 DAT could not complete the audit.",
    )
    parser.add_argument("--version", action="version", version=f"dat {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    for name, cap in AUDIT_COMMANDS.items():
        p = sub.add_parser(
            name, help=engine.PROFILES[cap].summary, description=f"{cap}: {engine.PROFILES[cap].summary}"
        )
        p.add_argument("target", nargs="?", default=".", help="directory or file to audit (default: .)")
        p.add_argument("--policy", type=Path, help="dat.policy JSON file (default: TARGET/.dat/policy.json)")
        p.add_argument("--no-target-policy", action="store_true", help="ignore the target's own policy file")
        p.add_argument("--gate", action="store_true", help="release-gate mode: exit 1 on blocking findings")
        p.add_argument("--fail-on", choices=[s.label for s in Severity], help="gate threshold (implies --gate)")
        p.add_argument(
            "--min-confidence",
            choices=[c.label for c in Confidence],
            help="ignore findings below this confidence for the gate",
        )
        p.add_argument(
            "--plugin",
            action="append",
            default=[],
            metavar="REF",
            help="load an analyzer plugin (entry point name or module:Class); repeatable",
        )
        _add_output_options(p)

    p = sub.add_parser("policy", help="policy.verify: validate a policy, optionally re-gate a report")
    psub = p.add_subparsers(dest="policy_command", metavar="ACTION", required=True)
    pv = psub.add_parser("verify", help="validate POLICY; with --report, re-evaluate that report's gate")
    pv.add_argument("policy", type=Path)
    pv.add_argument("--report", type=Path, help="dat.report JSON to re-gate against POLICY")
    pv.add_argument("--format", choices=("text", "json"), default="text")
    pv.add_argument("--pretty", action="store_true")

    p = sub.add_parser("analyzers", help="list analyzers, their rules and installed plugins")
    p.add_argument("--format", choices=("text", "json"), default="text")

    sub.add_parser("version", help="print the version as JSON")

    p = sub.add_parser("blux-capability", help="BLUX subprocess adapter (JSON on stdin, one JSON doc on stdout)")
    p.add_argument("capability_id", choices=blux_adapter.CAPABILITIES)

    for name in ("bundle", "cmd"):
        p = sub.add_parser(
            name,
            help="write main source files into one Markdown file" + (" (alias of bundle)" if name == "cmd" else ""),
        )
        bundle.add_arguments(p)
    return parser


def _legacy_argv(argv: list[str]) -> list[str]:
    """Pre-4.0 forms: `dat [PATH] [--json F ...]` and `dat --from-lrc DIR` (used by lrc)."""
    if argv and argv[0] in COMMANDS | {"-h", "--help", "--version"}:
        return argv
    out = [a for a in argv if a not in LEGACY_NOOP]
    if len(out) != len(argv):
        print("dat: note: --no-sign/--deep/--fast/--audit/--all are ignored since 4.0", file=sys.stderr)
    if "--from-lrc" in out:
        i = out.index("--from-lrc")
        if i + 1 >= len(out):
            return ["audit", "--from-lrc"]  # let argparse report the usage error
        target = out[i + 1]
        out = out[:i] + out[i + 2 :]
        return ["audit", target, *out]
    return ["audit", *out] if out else argv


def _policy(args: argparse.Namespace, target: Path) -> Policy:
    if args.policy is not None:
        policy = load(args.policy, None)
    elif args.no_target_policy:
        policy = DEFAULT_POLICY
    else:
        policy = load(None, target if target.is_dir() else None)
    if args.fail_on:
        policy = dataclasses.replace(policy, fail_on=Severity.parse(args.fail_on))
    if args.min_confidence:
        policy = dataclasses.replace(policy, min_confidence=Confidence.parse(args.min_confidence))
    return policy


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _outputs(args: argparse.Namespace) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for attr, fmt in (("out_json", "json"), ("out_markdown", "markdown"), ("out_sarif", "sarif"), ("out_text", "text")):
        if getattr(args, attr):
            out.append((Path(getattr(args, attr)), fmt))
    if args.out_auto:
        path = Path(args.out_auto)
        out.append((path, EXT_FORMAT.get(path.suffix.lower(), "json")))
    if args.out_report:
        path = Path(args.out_report)
        out.append((path / "dat-report.json" if path.is_dir() else path, "json"))
    return out


def _run_audit(args: argparse.Namespace, capability: str) -> int:
    target = Path(args.target)
    plugins: list[tuple[str, Any]] = []
    try:
        policy = _policy(args, target)
        plugins = [(ref, load_plugin(ref)) for ref in args.plugin]
    except DatError as exc:
        report = engine.error_report(capability, args.target, exc)
    else:
        report = engine.run(
            capability,
            target,
            policy=policy,
            target_label=args.target,
            plugins=plugins,
            enforce_gate=bool(args.gate or args.fail_on),
        )
    code = exit_code(report)
    try:
        for path, fmt in _outputs(args):
            _atomic_write(path, render.render(report, fmt, pretty=True))
            if args.sign:
                sig = signing.sign(path, key=args.sign_key)
                print(f"dat: signed {path} -> {sig}", file=sys.stderr)
    except DatError as exc:
        print(f"dat: {exc.code.value}: {exc.message}", file=sys.stderr)
        code = EXIT_FAILURE
    except OSError as exc:
        print(f"dat: {ErrorCode.OUTPUT_FAILED.value}: cannot write report ({exc.strerror})", file=sys.stderr)
        code = EXIT_FAILURE
    if not args.quiet:
        sys.stdout.write(render.render(report, args.format, pretty=args.pretty))
    return code


def _verification_text(doc: dict[str, Any]) -> str:
    pol = doc["policy"]
    lines = [f"DAT {doc['tool']['version']} · policy.verify · {pol['path']}", f"Outcome: {doc['outcome'].upper()}"]
    if doc["outcome"] == "error":
        lines += [f"  {e['code']}: {e['message']}" for e in doc["errors"]]
        return "\n".join(lines) + "\n"
    lines.append(
        f"Policy: {'valid' if pol['valid'] else 'INVALID'}"
        + (f" ({pol['policy_id']}, {pol['sha256'][:12]})" if pol["valid"] else "")
    )
    lines += [f"  - {p}" for p in pol["problems"]]
    if doc["report"]:
        r = doc["report"]
        lines.append(
            f"Report: {r['path']} ({r['capability']}, {r['outcome']}): {r['findings']} findings, "
            f"{r['newly_suppressed']} suppressed by this policy, {r['blocking']} blocking"
        )
    lines.append(f"Verdict: {doc['verdict'].upper()}")
    return "\n".join(lines) + "\n"


def _run_policy(args: argparse.Namespace) -> int:
    doc = verify.verify(args.policy, args.report)
    if args.format == "json":
        sys.stdout.write(render.to_json(doc, pretty=args.pretty))
    else:
        sys.stdout.write(_verification_text(doc))
    if doc["outcome"] != "completed":
        return EXIT_FAILURE
    return EXIT_GATE_FAILED if doc["verdict"] == "fail" else EXIT_OK


def _run_analyzers(args: argparse.Namespace) -> int:
    doc: dict[str, Any] = {
        "analyzers": [a.describe() for a in BUILTIN],
        "capabilities": {
            cap: {"summary": p.summary, "target_kind": p.target_kind, "analyzers": list(p.analyzers)}
            for cap, p in sorted(engine.PROFILES.items())
        },
        "installed_plugins": installed_plugins(),
    }
    if args.format == "json":
        sys.stdout.write(render.to_json(doc, pretty=True))
        return EXIT_OK
    for a in doc["analyzers"]:
        print(f"{a['id']} {a['version']} ({', '.join(a['target_kinds'])})")
        for r in a["rules"]:
            print(f"  {r['rule_id']:<38} {r['severity']:<8} {r['confidence']:<6} {r['title']}")
    print("plugins: " + (", ".join(doc["installed_plugins"]) or "none installed (load with --plugin)"))
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not raw:
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    try:
        args = parser.parse_args(_legacy_argv(raw))
    except SystemExit as exc:
        return EXIT_OK if exc.code == 0 else EXIT_USAGE
    try:
        if args.command in AUDIT_COMMANDS:
            return _run_audit(args, AUDIT_COMMANDS[args.command])
        if args.command == "policy":
            return _run_policy(args)
        if args.command == "analyzers":
            return _run_analyzers(args)
        if args.command == "version":
            sys.stdout.write(json.dumps({"name": "dat", "version": __version__}) + "\n")
            return EXIT_OK
        if args.command == "blux-capability":
            return blux_adapter.main(args.capability_id)
        if args.command in ("bundle", "cmd"):
            return bundle.run(args)
    except KeyboardInterrupt:
        print("dat: interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        return EXIT_FAILURE
    except Exception as exc:
        print(f"dat: {ErrorCode.INTERNAL_ERROR.value}: {type(exc).__name__}", file=sys.stderr)
        if os.environ.get("DAT_DEBUG") == "1":
            raise
        return EXIT_FAILURE
    parser.print_help(sys.stderr)
    return EXIT_USAGE
