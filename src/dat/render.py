"""Renderers for a dat.report document: human text, Markdown, JSON and SARIF 2.1.0.

All renderers are pure functions of the report, so they are as deterministic as the report itself.
"""

from __future__ import annotations

import json
from typing import Any

SARIF_LEVEL = {"critical": "error", "high": "error", "medium": "warning", "low": "note", "info": "note"}
SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")


def to_json(report: dict[str, Any], *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def _where(loc: dict[str, Any]) -> str:
    out = loc.get("path") or "<target>"
    if loc.get("member"):
        out += f"!{loc['member']}"
    if loc.get("line"):
        out += f":{loc['line']}"
        if loc.get("column"):
            out += f":{loc['column']}"
    return out


def _gate_line(gate: dict[str, Any]) -> str:
    verdict = gate["verdict"]
    if verdict == "not_evaluated":
        return "Gate: NOT EVALUATED (audit did not complete)"
    word = "FAIL" if verdict == "fail" else "PASS"
    mode = "enforced" if gate["enforced"] else "advisory"
    return (
        f"Gate: {word} ({gate['blocking']} blocking at severity >= {gate['fail_on']}, "
        f"confidence >= {gate['min_confidence']}; {mode})"
    )


def _sorted_findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    rank = {s: i for i, s in enumerate(SEVERITY_ORDER)}
    return sorted(report["findings"], key=lambda f: (rank[f["severity"]], _where(f["location"]), f["rule_id"]))


def to_text(report: dict[str, Any]) -> str:
    tool, target, summary = report["tool"], report["target"], report["summary"]
    lines = [
        f"DAT {tool['version']} · {report['capability']} · {target.get('path')}",
        f"Outcome: {report['outcome'].upper()}",
    ]
    if report["outcome"] != "error":
        lines.append(_gate_line(report["gate"]))
        skipped = len(target.get("skipped", []))
        lines.append(f"Files: {target.get('files', 0)} scanned, {skipped} skipped")
        counts = ", ".join(f"{s} {n}" for s, n in summary["by_severity"].items() if n)
        lines.append(
            f"Findings: {summary['findings']}{f' ({counts})' if counts else ''}; suppressed: {summary['suppressed']}"
        )
    for f in _sorted_findings(report):
        lines.append("")
        lines.append(f"  {f['severity'].upper():<8} {f['confidence']:<6} {f['rule_id']}  {_where(f['location'])}")
        lines.append(f"           {f['message']}")
        if f.get("evidence"):
            lines.append(f"           evidence: {f['evidence']}")
        if f.get("remediation"):
            lines.append(f"           fix: {f['remediation']}")
    if report["analyzers"]:
        lines.append("")
        parts = []
        for a in report["analyzers"]:
            status = a["status"] if a["status"] != "failed" else "FAILED"
            extra = f" ({a['reason']})" if a.get("reason") else f" ({a['findings']})" if a["status"] == "ok" else ""
            parts.append(f"{a['id']} {status}{extra}")
        lines.append("Analyzers: " + " · ".join(parts))
    if report["errors"]:
        lines.append("")
        lines.append("Errors (DAT could not complete these; they are not findings):")
        for e in report["errors"]:
            who = f"{e['analyzer']}: " if e.get("analyzer") else ""
            details = ", ".join(f"{k}={v}" for k, v in sorted(e.get("details", {}).items()))
            lines.append(f"  {e['code']}  {who}{e['message']}{f' ({details})' if details else ''}")
    return "\n".join(lines) + "\n"


def _md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def to_markdown(report: dict[str, Any]) -> str:
    target, summary = report["target"], report["summary"]
    lines = [
        f"# DAT report: `{report['capability']}`",
        "",
        "| | |",
        "|---|---|",
        f"| Target | `{_md(target.get('path'))}` ({target.get('kind')}) |",
        f"| DAT | {report['tool']['version']} |",
        f"| Outcome | **{report['outcome']}** |",
    ]
    if report["outcome"] != "error":
        lines.append(f"| Gate | {_md(_gate_line(report['gate']).removeprefix('Gate: '))} |")
        lines.append(f"| Findings | {summary['findings']} (suppressed {summary['suppressed']}) |")
        if report.get("policy"):
            lines.append(f"| Policy | `{_md(report['policy']['policy_id'])}` (`{report['policy']['sha256'][:12]}`) |")
    if report["findings"]:
        lines += [
            "",
            "## Findings",
            "",
            "| Severity | Confidence | Rule | Location | Message |",
            "|---|---|---|---|---|",
        ]
        for f in _sorted_findings(report):
            lines.append(
                f"| {f['severity']} | {f['confidence']} | `{f['rule_id']}` | `{_md(_where(f['location']))}` "
                f"| {_md(f['message'])} |"
            )
    if report["suppressed"]:
        lines += ["", "## Suppressed", "", "| Rule | Location | Source | Reason |", "|---|---|---|---|"]
        for s in report["suppressed"]:
            lines.append(f"| `{s['rule_id']}` | `{_md(_where(s['location']))}` | {s['source']} | {_md(s['reason'])} |")
    if report["analyzers"]:
        lines += ["", "## Analyzers", "", "| Analyzer | Version | Status | Findings |", "|---|---|---|---|"]
        for a in report["analyzers"]:
            status = a["status"] + (f" ({_md(a['reason'])})" if a.get("reason") else "")
            lines.append(f"| `{a['id']}` | {a['version']} | {status} | {a['findings']} |")
    if report["errors"]:
        lines += ["", "## Errors", "", "These are execution failures, not findings.", ""]
        for e in report["errors"]:
            lines.append(
                f"- `{e['code']}`{' (' + e['analyzer'] + ')' if e.get('analyzer') else ''}: {_md(e['message'])}"
            )
    return "\n".join(lines) + "\n"


def to_sarif(report: dict[str, Any]) -> str:
    rules: dict[str, dict[str, Any]] = {}
    results = []
    for f in _sorted_findings(report):
        rules.setdefault(
            f["rule_id"],
            {
                "id": f["rule_id"],
                "shortDescription": {"text": f["title"]},
                "properties": {"category": f["category"], "analyzer": f["analyzer"]},
                **({"help": {"text": f["remediation"]}} if f.get("remediation") else {}),
            },
        )
        loc = f["location"]
        physical: dict[str, Any] = {"artifactLocation": {"uri": loc.get("path") or "."}}
        if loc.get("line"):
            physical["region"] = {
                "startLine": loc["line"],
                **({"startColumn": loc["column"]} if loc.get("column") else {}),
            }
        results.append(
            {
                "ruleId": f["rule_id"],
                "level": SARIF_LEVEL[f["severity"]],
                "message": {"text": f["message"] + (f" [{loc['member']}]" if loc.get("member") else "")},
                "locations": [{"physicalLocation": physical}],
                "partialFingerprints": {"datFingerprint/v1": f["fingerprint"]},
                "properties": {"severity": f["severity"], "confidence": f["confidence"]},
            }
        )
    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "dat",
                        "version": report["tool"]["version"],
                        "informationUri": "https://github.com/Outer-Void/dat",
                        "rules": [rules[k] for k in sorted(rules)],
                    }
                },
                "results": results,
                "invocations": [
                    {
                        "executionSuccessful": report["outcome"] == "completed",
                        "toolExecutionNotifications": [
                            {"level": "error", "message": {"text": f"{e['code']}: {e['message']}"}}
                            for e in report["errors"]
                        ],
                    }
                ],
            }
        ],
    }
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


RENDERERS = {"text": to_text, "markdown": to_markdown, "sarif": to_sarif}


def render(report: dict[str, Any], fmt: str, *, pretty: bool = False) -> str:
    if fmt == "json":
        return to_json(report, pretty=pretty)
    return RENDERERS[fmt](report)
